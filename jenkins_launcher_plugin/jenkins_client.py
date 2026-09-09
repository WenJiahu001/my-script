# -*- coding: utf-8 -*-
"""Jenkins REST 客户端：多环境、深层 Folder、缓存与参数化构建。"""

import base64
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from config import DATA_DIR, PLUGIN_DIR, get_default_env_key, get_env_config, get_environments, is_env_configured, load_cache, load_views_cache, save_cache, save_views_cache
from windows_notifications import show_notification

JOB_TREE = "jobs[name,url,color,description,lastBuild[number,result],_class]"
PARAM_TREE = "property[parameterDefinitions[name,description,defaultParameterValue[value]]]"


class JenkinsClient:
    BUILD_TRACK_INTERVAL_SECONDS = 5
    BUILD_TRACK_TIMEOUT_SECONDS = 5 * 60

    def __init__(self, env_key: str):
        self.env_key = env_key
        self.config = get_env_config(env_key) or {}
        self.base_url = str(self.config.get("jenkins_url", "")).rstrip("/")
        self.username = str(self.config.get("username", ""))
        self.api_token = str(self.config.get("api_token", ""))
        try:
            self.timeout = max(1, min(30, int(self.config.get("timeout", 5))))
        except (TypeError, ValueError):
            self.timeout = 5
        self.verify_ssl = bool(self.config.get("verify_ssl", True))
        self.configured_views = self.config.get("views", []) or []
        self.env_name = str(self.config.get("name", env_key.upper()))
        self._cached_crumb: tuple[str, str] | None = None
        # 仅保存本次触发获得的队列地址，供入口层启动后台追踪。
        self.last_queue_url = ""

    def _get_auth_header(self) -> dict[str, str]:
        raw_auth = f"{self.username}:{self.api_token}".encode("utf-8")
        return {"Authorization": f"Basic {base64.b64encode(raw_auth).decode('ascii')}"}

    def _get_ssl_context(self) -> ssl.SSLContext | None:
        if self.verify_ssl:
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _request(self, endpoint_or_url: str, method: str = "GET", data: bytes | None = None, extra_headers: dict | None = None) -> tuple[int, dict | str, dict]:
        full_url = endpoint_or_url if endpoint_or_url.startswith(("http://", "https://")) else f"{self.base_url}/{endpoint_or_url.lstrip('/')}"
        headers = {"User-Agent": f"FlowLauncher-JenkinsPlugin/{self.env_key}", **self._get_auth_header(), **(extra_headers or {})}
        request = urllib.request.Request(full_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self._get_ssl_context()) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                response_headers = dict(response.info())
                try:
                    body = json.loads(raw_body) if "application/json" in response_headers.get("Content-Type", "") else raw_body
                except json.JSONDecodeError:
                    body = raw_body
                return response.status, body, response_headers
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"[{self.env_name}] HTTP {error.code}: {error.reason}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"[{self.env_name}] 连接失败: {error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError(f"[{self.env_name}] 请求超时，请检查网络状态") from error

    def get_crumb(self, force_refresh: bool = False) -> tuple[str, str] | None:
        if self._cached_crumb and not force_refresh:
            return self._cached_crumb
        try:
            _, data, _ = self._request("crumbIssuer/api/json")
            if isinstance(data, dict) and data.get("crumbRequestField") and data.get("crumb"):
                self._cached_crumb = (str(data["crumbRequestField"]), str(data["crumb"]))
                return self._cached_crumb
        except RuntimeError:
            pass
        return None

    def get_remote_views(self) -> list[dict]:
        if not is_env_configured(self.env_key)[0]:
            return []
        cached = load_views_cache(self.env_key)
        if cached is not None:
            return cached
        try:
            _, data, _ = self._request("api/json?tree=views[name,url,description]")
            views = data.get("views", []) if isinstance(data, dict) and isinstance(data.get("views"), list) else []
            # 即使没有视图，也缓存结果，避免无权限时每次输入 views 都重复请求。
            save_views_cache(self.env_key, views, self.config.get("cache_duration_seconds", 600))
            return views
        except RuntimeError:
            return []

    def _parse_job_status(self, color: str | None) -> tuple[str, str]:
        base_color = (color or "").replace("_anime", "")
        if (color or "").endswith("_anime"):
            return "正在构建中...", "building.png"
        if base_color in ("blue", "green"):
            return "最近构建: 成功", "prod_success.png" if self.config.get("require_confirm", False) else "success.png"
        if base_color in ("red", "yellow", "aborted"):
            return "最近构建: 失败" if base_color == "red" else "最近构建: 不稳定 / 中止", "failed.png"
        if base_color in ("disabled", "nobuilt", "notbuilt"):
            return "已禁用 / 未构建", "icon.png"
        return "就绪", "icon.png"

    @staticmethod
    def _is_folder(item: dict) -> bool:
        return "folder" in str(item.get("_class", "")).lower() or isinstance(item.get("jobs"), list)

    def _to_job(self, item: dict, parent_path: str, view_name: str) -> dict:
        name = str(item.get("name", ""))
        last_build = item.get("lastBuild") if isinstance(item.get("lastBuild"), dict) else {}
        status_text, icon_name = self._parse_job_status(item.get("color"))
        return {"env_key": self.env_key, "env_name": self.env_name, "requires_confirmation": bool(self.config.get("require_confirm", False)), "view_name": view_name, "name": name, "display_name": f"{parent_path}/{name}" if parent_path else name, "url": str(item.get("url", "")), "color": item.get("color", ""), "status_text": status_text, "icon_name": icon_name, "description": str(item.get("description", "") or ""), "last_build_number": last_build.get("number")}

    def _collect_jobs(self, items: list[dict], parent_path: str = "", view_name: str = "", seen_folders: set[str] | None = None, depth: int = 0) -> list[dict]:
        """逐层请求 Folder，避免 Jenkins tree 查询深度截断；上限防止异常循环。"""
        if depth > 20:
            return []
        seen_folders = seen_folders if seen_folders is not None else set()
        result: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            display_name = f"{parent_path}/{item.get('name', '')}" if parent_path else str(item.get("name", ""))
            if self._is_folder(item):
                folder_url = str(item.get("url", "")).rstrip("/")
                if not folder_url or folder_url in seen_folders:
                    continue
                seen_folders.add(folder_url)
                try:
                    _, folder_data, _ = self._request(f"{folder_url}/api/json?tree={JOB_TREE}")
                    children = folder_data.get("jobs", []) if isinstance(folder_data, dict) else []
                    if isinstance(children, list):
                        result.extend(self._collect_jobs(children, display_name, view_name, seen_folders, depth + 1))
                except RuntimeError:
                    continue
            else:
                result.append(self._to_job(item, parent_path, view_name))
        return result

    def _fetch_jobs(self) -> tuple[bool, list[dict] | str]:
        endpoints = [(str(view).strip(), f"view/{urllib.parse.quote(str(view).strip())}/api/json?tree={JOB_TREE}") for view in self.configured_views if str(view).strip()]
        if not endpoints:
            endpoints = [("", f"api/json?tree={JOB_TREE}")]
        merged, errors = {}, []
        for view_name, endpoint in endpoints:
            try:
                _, data, _ = self._request(endpoint)
                jobs = self._collect_jobs(data.get("jobs", []), view_name=view_name) if isinstance(data, dict) else []
                for job in jobs:
                    key = job.get("url") or job.get("display_name")
                    if key in merged and view_name:
                        old = merged[key].get("view_name", "")
                        if view_name not in old.split(" / "):
                            merged[key]["view_name"] = f"{old} / {view_name}" if old else view_name
                    else:
                        merged[key] = job
            except RuntimeError as error:
                errors.append(f"视图 [{view_name or '全局'}] 拉取失败: {error}")
        jobs = list(merged.values())
        if jobs or not errors:
            # 空列表同样是有效结果，必须缓存以避免反复请求空 Views。
            save_cache(self.env_key, jobs, self.config.get("cache_duration_seconds", 600))
            return True, jobs
        return False, "；".join(errors)

    def get_jobs(self, force_refresh: bool = False) -> tuple[bool, list[dict] | str]:
        valid, message = is_env_configured(self.env_key)
        if not valid:
            return False, message
        if not force_refresh:
            fresh = load_cache(self.env_key)
            if fresh is not None:
                return True, fresh
            stale = load_cache(self.env_key, allow_stale=True)
            if stale is not None:
                JenkinsManager.refresh_in_background(self.env_key)
                return True, stale
            # 首次同步也放入后台，避免 Flow Launcher 每次键入都阻塞等待 Jenkins。
            JenkinsManager.refresh_in_background(self.env_key)
            return False, f"[{self.env_name}] 正在后台同步流水线列表，请稍后重试"
        return self._fetch_jobs()

    def _get_parameter_definitions(self, job_url: str) -> tuple[bool, list[dict]]:
        """返回 (读取成功, 参数定义)，读取失败时必须阻止默认构建。"""
        try:
            _, data, _ = self._request(f"{job_url.rstrip('/')}/api/json?tree={PARAM_TREE}")
            if not isinstance(data, dict):
                return False, []
            result = []
            for prop in data.get("property", []):
                if isinstance(prop, dict) and isinstance(prop.get("parameterDefinitions"), list):
                    result.extend(p for p in prop["parameterDefinitions"] if isinstance(p, dict))
            return True, result
        except RuntimeError:
            return False, []

    def trigger_build(self, job_url_or_name: str) -> tuple[bool, str]:
        self.last_queue_url = ""
        valid, message = is_env_configured(self.env_key)
        if not valid:
            return False, message
        job_url = job_url_or_name.rstrip("/") if job_url_or_name.startswith(("http://", "https://")) else f"{self.base_url}/job/{urllib.parse.quote(job_url_or_name)}"
        definitions_loaded, definitions = self._get_parameter_definitions(job_url)
        if not definitions_loaded:
            return False, "无法读取该 Job 的参数定义，为避免误触发已停止构建。请在 Jenkins 页面确认参数后执行。"
        headers = {}
        if crumb := self.get_crumb():
            headers[crumb[0]] = crumb[1]
        missing = [str(p.get("name", "未命名参数")) for p in definitions if not isinstance(p.get("defaultParameterValue"), dict) or "value" not in p["defaultParameterValue"]]
        if missing:
            return False, f"该 Job 存在必填参数：{', '.join(missing)}。请在 Jenkins 页面填写参数后构建。"
        if definitions:
            values = {str(p["name"]): p["defaultParameterValue"].get("value", "") for p in definitions if p.get("name")}
            endpoint, data = f"{job_url}/buildWithParameters", urllib.parse.urlencode(values).encode("utf-8")
        else:
            endpoint, data = f"{job_url}/build", b""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                status, _, response_headers = self._request(endpoint, method="POST", data=data, extra_headers=headers)
                if status in (200, 201, 202):
                    queue_url = next((str(value) for key, value in response_headers.items() if str(key).lower() == "location"), "")
                    self.last_queue_url = queue_url
                    return True, f"已成功触发构建！{' (队列: ' + queue_url + ')' if queue_url else ''}"
            except RuntimeError as error:
                last_error = error
                if attempt == 0 and (crumb := self.get_crumb(force_refresh=True)):
                    headers[crumb[0]] = crumb[1]
        return False, f"构建失败: {last_error or 'Jenkins 未返回成功状态'}"

    @staticmethod
    def _format_duration(duration: object) -> str:
        """将 Jenkins 返回的毫秒耗时转换为易读文本。"""
        try:
            total_seconds = max(0, int(duration) // 1000)
        except (TypeError, ValueError):
            return "未知"
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"

    @staticmethod
    def _show_tracking_message(title: str, message: str, icon: int) -> None:
        """后台子进程独立发送通知中心消息，icon 参数保留兼容现有调用。"""
        show_notification(title, message)

    def track_build_until_complete(
        self,
        job_name: str,
        queue_url: str,
        poll_interval_seconds: float | None = None,
        max_wait_seconds: float | None = None,
        sleep_fn=time.sleep,
        monotonic_fn=time.monotonic,
        notify_fn=None,
    ) -> None:
        """追踪单次队列直至构建完成；仅在后台进程调用，绝不阻塞搜索。"""
        queue_url = str(queue_url or "").rstrip("/")
        if not queue_url.startswith(("http://", "https://")):
            return
        interval = self.BUILD_TRACK_INTERVAL_SECONDS if poll_interval_seconds is None else max(0, poll_interval_seconds)
        timeout = self.BUILD_TRACK_TIMEOUT_SECONDS if max_wait_seconds is None else max(0, max_wait_seconds)
        notify = notify_fn or self._show_tracking_message
        started_at, build_url, last_error = monotonic_fn(), "", ""

        while monotonic_fn() - started_at <= timeout:
            try:
                if not build_url:
                    _, queue_data, _ = self._request(f"{queue_url}/api/json")
                    if not isinstance(queue_data, dict):
                        raise RuntimeError("队列状态返回格式异常")
                    if queue_data.get("cancelled"):
                        notify("Jenkins 队列已取消", f"【{self.env_name}】项目 [{job_name}]\n该次构建在队列中被取消，未开始执行。", 0x30)
                        return
                    executable = queue_data.get("executable")
                    if isinstance(executable, dict) and executable.get("url"):
                        build_url = str(executable["url"]).rstrip("/")
                else:
                    _, build_data, _ = self._request(f"{build_url}/api/json?tree=building,result,number,duration,url")
                    if not isinstance(build_data, dict):
                        raise RuntimeError("构建状态返回格式异常")
                    result = str(build_data.get("result") or "").upper()
                    if not build_data.get("building") and result:
                        number = build_data.get("number", "未知")
                        duration = self._format_duration(build_data.get("duration"))
                        actual_url = str(build_data.get("url") or build_url).rstrip("/")
                        if result == "SUCCESS":
                            notify("Jenkins 构建完成", f"【{self.env_name}】项目 [{job_name}]\n构建 #{number} 成功，耗时：{duration}。", 0x40)
                        else:
                            console_url = f"{actual_url}/console"
                            notify("Jenkins 构建未成功", f"【{self.env_name}】项目 [{job_name}]\n构建 #{number} 结果：{result}，耗时：{duration}。\n控制台日志：{console_url}", 0x10)
                        return
            except RuntimeError as error:
                # 临时网络波动不应让一次已经入队的构建失去最终反馈。
                last_error = str(error)

            if interval:
                sleep_fn(interval)

        target_url = build_url or queue_url
        error_hint = f"\n最后一次请求错误：{last_error}" if last_error else ""
        notify("Jenkins 构建跟踪超时", f"【{self.env_name}】项目 [{job_name}]\n超过 5 分钟仍未获取最终结果，请在 Jenkins 中查看。\n地址：{target_url}{error_hint}", 0x30)


class JenkinsManager:
    """多环境 Jenkins 协调管理器。"""
    def __init__(self):
        self._clients: dict[str, JenkinsClient] = {}

    def get_client(self, env_key: str | None = None) -> JenkinsClient:
        env_key = env_key or get_default_env_key()
        if env_key not in self._clients:
            self._clients[env_key] = JenkinsClient(env_key)
        return self._clients[env_key]

    @classmethod
    def refresh_in_background(cls, env_key: str) -> None:
        """后台刷新，锁占用或失效时立即返回，绝不阻塞 Flow 搜索进程。"""
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_id = hashlib.sha256(str(env_key).encode("utf-8")).hexdigest()[:16]
        lock_path = os.path.join(DATA_DIR, f".refresh_{lock_id}.lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            try:
                # 单次同步最长受 timeout 与 Folder 数量影响；30 秒后视为异常遗留。
                if os.path.getmtime(lock_path) < time.time() - 30:
                    os.unlink(lock_path)
                    return cls.refresh_in_background(env_key)
            except FileNotFoundError:
                return cls.refresh_in_background(env_key)
            return
        script = f"from jenkins_client import JenkinsManager; import os\np={lock_path!r}\ntry:\n JenkinsManager().get_jobs_for_env({env_key!r}, True)\nfinally:\n os.path.exists(p) and os.unlink(p)"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            subprocess.Popen([sys.executable, "-c", script], cwd=PLUGIN_DIR, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass

    @classmethod
    def start_build_tracking(cls, env_key: str, job_name: str, queue_url: str) -> bool:
        """为每次构建启动独立追踪进程，允许同一项目并发构建。"""
        if not isinstance(queue_url, str) or not queue_url.startswith(("http://", "https://")):
            return False
        script = (
            "from jenkins_client import JenkinsClient\n"
            f"JenkinsClient({env_key!r}).track_build_until_complete({job_name!r}, {queue_url!r})"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            subprocess.Popen([sys.executable, "-c", script], cwd=PLUGIN_DIR, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False

    def get_jobs_for_env(self, env_key: str, force_refresh: bool = False) -> tuple[bool, list[dict] | str]:
        return self.get_client(env_key).get_jobs(force_refresh)

    def get_all_jobs(self, force_refresh: bool = False) -> tuple[bool, list[dict] | str]:
        all_jobs, errors = [], []
        for env_key in get_environments():
            if not is_env_configured(env_key)[0]:
                continue
            success, result = self.get_jobs_for_env(env_key, force_refresh)
            if success and isinstance(result, list):
                all_jobs.extend(result)
            elif not success:
                errors.append(str(result))
        return (True, all_jobs) if all_jobs or not errors else (False, "；".join(errors))
