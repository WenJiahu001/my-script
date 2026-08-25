import ctypes
import inspect
import json
import os
import subprocess
import sys
import traceback
from typing import Any
import webbrowser

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from config import (
    clear_cache,
    get_config_path,
    get_default_env_key,
    get_env_config,
    get_environments,
    is_any_configured,
    is_env_configured,
    load_config,
)
# Flow 的 runpy 启动方式不会稳定地把插件目录加入 sys.path，因此协议层内置于入口。
def log_debug(message: str) -> None:
    try:
        log_path = os.path.join(os.environ.get("APPDATA", CURRENT_DIR), "FlowLauncher", "JenkinsLauncher", "plugin_debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"{message}\n")
    except OSError:
        pass


def _parse_flow_request(raw_arg: str) -> dict:
    try:
        request = json.loads(raw_arg)
        return request if isinstance(request, dict) and "method" in request else {"method": "query", "parameters": [raw_arg]}
    except json.JSONDecodeError:
        return {"method": "query", "parameters": [raw_arg]}


class FlowLauncher:
    """自包含 JSON-RPC 协议层，不依赖插件目录的额外 Python 模块。"""
    def __init__(self):
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass
        try:
            request = _parse_flow_request(sys.argv[1]) if len(sys.argv) > 1 else {"method": "query", "parameters": [""]}
            name, params = request.get("method", "query"), request.get("parameters", [])
            method = getattr(self, name, None) if isinstance(name, str) and not name.startswith("_") else None
            if not callable(method):
                method, params = self.query, [str(sys.argv[1]) if len(sys.argv) > 1 else ""]
            params = params if isinstance(params, list) else []
            count = len([p for p in inspect.signature(method).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
            params = params[:count] + [""] * max(0, count - len(params))
            print(json.dumps({"result": method(*params) or []}, ensure_ascii=False))
        except Exception as error:
            log_debug(traceback.format_exc())
            print(json.dumps({"result": [{"Title": "Jenkins 插件执行异常", "SubTitle": str(error), "IcoPath": "Images/failed.png"}]}, ensure_ascii=False))
from history import get_job_weight, load_history, record_job_trigger
from jenkins_client import JenkinsManager


class JenkinsLauncher(FlowLauncher):
    """Flow Launcher 插件主类"""

    def __init__(self):
        self.manager = JenkinsManager()
        self.config = load_config()
        super().__init__()

    def _get_setting_icon(self) -> str:
        """快捷操作统一使用设置图标"""
        full_path = os.path.join(CURRENT_DIR, "Images", "setting.png")
        if os.path.exists(full_path):
            return full_path
        return os.path.join(CURRENT_DIR, "Images", "icon.png")

    def _get_pipeline_icon(self) -> str:
        """流水线项目统一使用流水线图标"""
        full_path = os.path.join(CURRENT_DIR, "Images", "pipeline.png")
        if os.path.exists(full_path):
            return full_path
        return os.path.join(CURRENT_DIR, "Images", "icon.png")

    def _get_quick_actions(self, target_mode: str) -> list[dict]:
        """获取标准快捷操作项清单"""
        action_env = get_default_env_key() if target_mode == "all" else target_mode
        actions = [
            {
                "Title": "[config] 打开 Jenkins 配置文件",
                "SubTitle": "回车立即打开 config.json 修改连接地址、Token 与视图",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {
                    "method": "open_config",
                    "parameters": [],
                    "dontHideAfterAction": False,
                },
                "ContextData": [target_mode, "", "config"],
                "_action_key": "config",
            },
            {
                "Title": "[refresh] 刷新 Job 缓存列表",
                "SubTitle": "回车立即清除本地缓存，从 Jenkins 服务器同步最新流水线列表",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {
                    "method": "refresh_cache",
                    "parameters": [action_env],
                    "dontHideAfterAction": False,
                },
                "ContextData": [target_mode, "", "refresh"],
                "_action_key": "refresh",
            },
            {
                "Title": "[refresh-all] 刷新全部环境缓存",
                "SubTitle": "清除并同步所有已配置环境的流水线列表",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {
                    "method": "refresh_cache",
                    "parameters": [],
                    "dontHideAfterAction": False,
                },
                "ContextData": ["all", "", "refresh-all"],
                "_action_key": "refresh-all",
            },
            {
                "Title": "[views] 查看 Jenkins 视图列表",
                "SubTitle": "查看当前 Jenkins 上所有可用视图分组 (Views)",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {
                    "method": "show_views",
                    "parameters": [action_env],
                    "dontHideAfterAction": False,
                },
                "ContextData": [target_mode, "", "views"],
                "_action_key": "views",
            },
        ]
        # 环境入口完全由配置生成，新增 beta/local 等环境无需改代码。
        for env_key, env_cfg in get_environments().items():
            if env_key == action_env:
                continue
            env_name = env_cfg.get("name", env_key)
            actions.append({
                "Title": f"[{env_key}] 打开 {env_name}",
                "SubTitle": f"在浏览器打开；检索请直接输入 'jk {env_key}'",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {"method": "open_browser", "parameters": [(get_env_config(env_key) or {}).get("jenkins_url", "")], "dontHideAfterAction": False},
                "ContextData": [env_key, "", "environment"],
                "_action_key": env_key.lower(),
            })
        return actions

    def _parse_query_target(self, query_text: str) -> tuple[str, str]:
        """解析环境标识与搜索关键词"""
        parts = query_text.strip().split()
        if not parts:
            return get_default_env_key(), ""

        first_token = parts[0].lower()
        rest_keyword = " ".join(parts[1:])

        envs = get_environments()
        env_key = next((key for key in envs if key.casefold() == first_token.casefold()), None)
        if env_key:
            return env_key, rest_keyword
        if first_token in ("all", "跨环境"):
            return "all", rest_keyword

        return get_default_env_key(), query_text.strip()

    def query(self, query_text: str = "") -> list[dict]:
        """处理 Flow Launcher 搜索查询"""
        query_text = (query_text or "").strip()
        query_lower = query_text.lower()
        log_debug(f"[query] 检索: '{query_text}'")

        # 1. 检查配置状态
        any_valid, config_msg = is_any_configured()
        if not any_valid:
            return [
                {
                    "Title": "[config] Jenkins 插件尚未配置完成",
                    "SubTitle": f"{config_msg} (回车快速打开 config.json 进行配置)",
                    "IcoPath": self._get_setting_icon(),
                    "JsonRPCAction": {
                        "method": "open_config",
                        "parameters": [],
                        "dontHideAfterAction": False,
                    },
                    "ContextData": [get_default_env_key(), "", "config"],
                }
            ]

        # 2. 支持 `jk views` 与 `jk <环境键> views`。
        query_env, query_keyword = self._parse_query_target(query_text)
        if query_keyword.lower() in ("views", "view"):
            return self.show_views(get_default_env_key() if query_env == "all" else query_env)

        # 3. 解析目标环境与关键词
        target_mode, search_keyword = self._parse_query_target(query_text)
        keyword_lower = search_keyword.lower()

        # 4. 【精准前缀匹配】快捷设置项
        matched_quick_actions = []
        all_quick_actions = self._get_quick_actions(target_mode)

        if not query_text:
            for act in all_quick_actions:
                item = act.copy()
                item.pop("_action_key", None)
                matched_quick_actions.append(item)
        else:
            for act in all_quick_actions:
                action_key = act.get("_action_key", "")
                if action_key.startswith(keyword_lower):
                    item = act.copy()
                    item.pop("_action_key", None)
                    matched_quick_actions.append(item)

        # 5. 获取对应环境数据
        if target_mode == "all":
            success, jobs_or_err = self.manager.get_all_jobs(force_refresh=False)
            current_env_title = f"跨环境全量搜索 ({' / '.join(get_environments())})"
        else:
            success, jobs_or_err = self.manager.get_jobs_for_env(target_mode, force_refresh=False)
            env_cfg = get_env_config(target_mode) or {}
            current_env_title = env_cfg.get("name", target_mode.upper())

        if not success:
            is_syncing = "正在后台同步" in str(jobs_or_err)
            error_res = [
                {
                    "Title": f"正在同步 {current_env_title} 的流水线列表" if is_syncing else f"无法获取 {current_env_title} 的流水线列表",
                    "SubTitle": "首次同步已在后台开始，稍后再次输入 jk 即可查看结果。" if is_syncing else f"错误: {jobs_or_err} (回车打开配置文件修改)",
                    "IcoPath": self._get_setting_icon(),
                    "JsonRPCAction": {
                        "method": "refresh_cache" if is_syncing else "open_config",
                        "parameters": [target_mode] if is_syncing else [],
                        "dontHideAfterAction": False,
                    },
                    "ContextData": [target_mode, "", "error"],
                }
            ]
            return matched_quick_actions + error_res

        jobs: list[dict] = jobs_or_err or []

        # 6. 流水线项目模糊过滤
        filtered_jobs = []
        for job in jobs:
            display_name = job.get("display_name", "")
            description = job.get("description", "")
            view_name = job.get("view_name", "")
            match_target = f"{display_name} {description} {view_name}".lower()

            if not keyword_lower or all(k in match_target for k in keyword_lower.split()):
                filtered_jobs.append(job)

        # 7. 高频与置顶智能排序：历史文件只读取一次。
        history_snapshot = load_history()
        def calculate_sort_key(job_item: dict):
            j_name = job_item.get("display_name", job_item.get("name", ""))
            j_url = job_item.get("url", "")
            j_env = job_item.get("env_key", target_mode)

            env_conf = get_env_config(j_env) or {}
            pinned_list = env_conf.get("pinned_jobs", [])
            is_pinned = (j_name in pinned_list) or (job_item.get("name") in pinned_list)

            count, last_time = get_job_weight(j_url, j_name, history_snapshot)
            return (1 if is_pinned else 0, count, last_time)

        sorted_jobs = sorted(filtered_jobs, key=calculate_sort_key, reverse=True)

        results = []
        results.extend(matched_quick_actions)

        # 8. 构造流水线项目展示项
        pipeline_icon = self._get_pipeline_icon()
        for job in sorted_jobs:
            name = job.get("display_name", job.get("name", ""))
            url = job.get("url", name)
            env_key = job.get("env_key", target_mode)
            is_sensitive = job.get("requires_confirmation", False) or bool((get_env_config(env_key) or {}).get("require_confirm", False))
            status_text = job.get("status_text", "就绪")
            last_build_num = job.get("last_build_number")
            build_info = f" #{last_build_num}" if last_build_num else ""
            desc = job.get("description", "")
            desc_prefix = f" [{desc}]" if desc else ""
            view_name = job.get("view_name", "")
            view_tag = f" 🏷️[{view_name}]" if view_name else ""

            env_conf = get_env_config(env_key) or {}
            pinned_list = env_conf.get("pinned_jobs", [])
            is_pinned = (name in pinned_list) or (job.get("name") in pinned_list)
            count, _ = get_job_weight(url, name, history_snapshot)

            pin_prefix = "📌 [置顶] " if is_pinned else ""
            freq_tag = f" ⭐[常用 {count}次]" if (count > 0 and not is_pinned) else ""

            env_tag = f"[{env_key.upper()}] " if target_mode == "all" else ""
            title_prefix = f"{pin_prefix}{'🚨 ' if is_sensitive else ''}{env_tag}"
            env_alert = " ⚠️ 需要构建确认" if is_sensitive else ""

            title = f"{title_prefix}{name}"
            subtitle = f"[{status_text}{build_info}]{view_tag}{freq_tag}{desc_prefix}{env_alert} | 回车立即构建 | 右键菜单"

            results.append({
                "Title": title,
                "SubTitle": subtitle,
                "IcoPath": pipeline_icon,
                "JsonRPCAction": {
                    "method": "build_job",
                    "parameters": [env_key, url, name],
                    "dontHideAfterAction": False,
                },
                "ContextData": [env_key, url, name],
            })

        if not results:
            results.append({
                "Title": f"未在 {current_env_title} 中找到与 '{search_keyword or query_text}' 相关的流水线或快捷设置",
                "SubTitle": f"输入 'refresh' 刷新列表，或输入 'jk all' 跨环境检索",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {
                    "method": "refresh_cache",
                    "parameters": [target_mode],
                    "dontHideAfterAction": False,
                },
                "ContextData": [target_mode, "", "empty"],
            })

        return results

    def show_views(self, env_key: str = "") -> list[dict]:
        """展示指定环境的 Jenkins Views。"""
        env_key = env_key or get_default_env_key()
        client = self.manager.get_client(env_key)
        remote_views = client.get_remote_views()
        if not remote_views:
            return [{
                "Title": f"[views] 未获取到 {env_key.upper()} Jenkins 视图列表",
                "SubTitle": "请检查网络、凭据或 Views 权限",
                "IcoPath": self._get_setting_icon(),
                "JsonRPCAction": {"method": "open_config", "parameters": [], "dontHideAfterAction": False},
                "ContextData": [env_key, "", "views"],
            }]
        configured_views = client.configured_views
        return [{
            "Title": f"📁 视图: {v.get('name', '')}{' [已在配置中启用]' if v.get('name', '') in configured_views else ''}",
            "SubTitle": f"{v.get('description', '') or ''} (回车在浏览器打开该视图)",
            "IcoPath": self._get_setting_icon(),
            "JsonRPCAction": {"method": "open_browser", "parameters": [v.get('url', '')], "dontHideAfterAction": False},
            "ContextData": [env_key, v.get("url", ""), v.get("name", "")],
        } for v in remote_views]

    def context_menu(self, context_data: Any = None) -> list[dict]:
        """
        右键上下文菜单 (高鲁棒性自适应解析)
        无论 context_data 格式如何，均能安全解析并提供【查看控制台日志】等菜单项
        """
        log_debug(f"[context_menu] 收到 ContextData: {context_data} (type: {type(context_data)})")

        env_key = get_default_env_key()
        job_url = ""
        job_name = ""

        # 1. 尝试自适应提取 context 数据
        if isinstance(context_data, list) and len(context_data) > 0:
            env_key = str(context_data[0]) if context_data[0] else get_default_env_key()
            job_url = str(context_data[1]) if len(context_data) > 1 and context_data[1] else ""
            job_name = str(context_data[2]) if len(context_data) > 2 and context_data[2] else ""
        elif isinstance(context_data, dict):
            env_key = context_data.get("env_key", get_default_env_key())
            job_url = context_data.get("url", "")
            job_name = context_data.get("name", "")
        elif isinstance(context_data, str) and (
            context_data.startswith("http://") or context_data.startswith("https://")
        ):
            job_url = context_data

        menus = []

        # 2. 如果存在 job_url（流水线项目），置顶提供【查看控制台日志】和【打开主页】
        if job_url and (job_url.startswith("http://") or job_url.startswith("https://")):
            base_job_url = job_url.rstrip("/")
            console_url = f"{base_job_url}/lastBuild/console"
            display_title = job_name or base_job_url.split("/")[-1]

            # 菜单项 1: 查看控制台构建日志 (Console Output)
            menus.append({
                "Title": f"📋 查看控制台构建日志 (Console Output)",
                "SubTitle": f"在默认浏览器中打开实时日志: {console_url}",
                "IcoPath": self._get_pipeline_icon(),
                "JsonRPCAction": {
                    "method": "open_browser",
                    "parameters": [console_url],
                    "dontHideAfterAction": False,
                },
            })

            # 菜单项 2: 在浏览器打开项目首页
            menus.append({
                "Title": f"🌐 在浏览器打开流水线首页: {display_title}",
                "SubTitle": job_url,
                "IcoPath": self._get_pipeline_icon(),
                "JsonRPCAction": {
                    "method": "open_browser",
                    "parameters": [job_url],
                    "dontHideAfterAction": False,
                },
            })

            # 菜单项 3: 一键触发构建
            menus.append({
                "Title": f"🚀 立即触发构建: {display_title} ({env_key.upper()})",
                "SubTitle": "以默认参数立即触发执行该流水线",
                "IcoPath": self._get_pipeline_icon(),
                "JsonRPCAction": {
                    "method": "build_job",
                    "parameters": [env_key, job_url, display_title],
                    "dontHideAfterAction": False,
                },
            })

        # 3. 常规快捷菜单项
        menus.append({
            "Title": f"[refresh] 刷新 [{env_key.upper()}] 缓存列表",
            "SubTitle": "从 Jenkins 服务器获取最新流水线状态与列表",
            "IcoPath": self._get_setting_icon(),
            "JsonRPCAction": {
                "method": "refresh_cache",
                "parameters": [env_key],
                "dontHideAfterAction": False,
            },
        })

        menus.append({
            "Title": "[config] 打开 Jenkins 配置文件",
            "SubTitle": f"配置文件路径: {get_config_path()}",
            "IcoPath": self._get_setting_icon(),
            "JsonRPCAction": {
                "method": "open_config",
                "parameters": [],
                "dontHideAfterAction": False,
            },
        })

        log_debug(f"[context_menu] 成功返回菜单项数: {len(menus)}")
        return menus

    def build_job(self, env_key: str, job_url_or_name: str, display_name: str = "") -> None:
        """执行触发构建并记录历史"""
        name_str = display_name or job_url_or_name
        env_cfg = get_env_config(env_key) or {}
        env_name = env_cfg.get("name", env_key.upper())
        require_confirm = env_cfg.get("require_confirm", False)
        log_debug(f"[build_job] 开始触发环境 [{env_key}] 项目 [{name_str}] ({job_url_or_name})")

        if require_confirm and os.name == "nt":
            msg = f"您即将触发【{env_name}】的构建发布！\n\n目标项目: {name_str}\n\n是否确认立即触发？"
            res = ctypes.windll.user32.MessageBoxW(0, msg, f"⚠️ Jenkins 生产环境构建确认", 0x31)
            if res != 1:
                log_debug("[build_job] 用户取消了构建确认")
                return

        client = self.manager.get_client(env_key)
        success, message = client.trigger_build(job_url_or_name)
        log_debug(f"[build_job] 触发结果: success={success}, msg={message}")

        if success:
            record_job_trigger(env_key, job_url_or_name, name_str)

        # 保留旧缓存以便下一次搜索立即可用；后台刷新成功后会原子替换它。
        # 缓存不存在时无需清理。

        if os.name == "nt":
            try:
                if success:
                    title = f"Jenkins 构建触发成功"
                    msg_text = f"【{env_name}】项目 [{name_str}]\n{message}"
                    ctypes.windll.user32.MessageBoxW(0, msg_text, title, 0x40)
                else:
                    title = f"Jenkins 构建触发失败"
                    msg_text = f"【{env_name}】项目 [{name_str}]\n{message}"
                    ctypes.windll.user32.MessageBoxW(0, msg_text, title, 0x10)
            except Exception as e:
                log_debug(f"[build_job] 弹窗通知异常: {e}")

    @staticmethod
    def _show_message(title: str, message: str, icon: int = 0x40) -> None:
        """显示 Windows 通知；失败时仅记录日志，不影响主操作。"""
        log_debug(f"[notify] {title}: {message}")
        if os.name != "nt":
            return
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, icon)
        except Exception as error:
            log_debug(f"[notify] 弹窗失败: {error}")

    def open_browser(self, url: str) -> None:
        """在默认浏览器打开 URL (采用 Windows 原生 ShellExecute)"""
        log_debug(f"[open_browser] 准备打开浏览器: {url}")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            log_debug("[open_browser] 拒绝非 HTTP(S) 地址")
            return
        try:
            if os.name == "nt":
                os.startfile(url)
            else:
                webbrowser.open(url)
            log_debug(f"[open_browser] 成功唤起默认浏览器: {url}")
        except Exception as e:
            log_debug(f"[open_browser] os.startfile 异常: {e}，尝试备用方案")
            try:
                webbrowser.open(url)
            except Exception as e2:
                log_debug(f"[open_browser] webbrowser.open 亦异常: {e2}")

    def open_config(self) -> None:
        """使用记事本直接打开主配置，避免 .json 文件关联导致动作无响应。"""
        cfg_path = get_config_path()
        if not os.path.exists(cfg_path):
            load_config()
        log_debug(f"[open_config] 准备打开配置文件: {cfg_path}")
        try:
            if os.name == "nt":
                subprocess.Popen(
                    ["notepad.exe", cfg_path],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
            else:
                webbrowser.open(cfg_path)
        except OSError as error:
            log_debug(f"[open_config] 打开配置文件失败: {error}")

    def refresh_cache(self, env_key: str | None = None) -> None:
        """清理缓存后同步 Jenkins，并向用户反馈实际结果。"""
        target_env = env_key if env_key and env_key != "all" else None
        target_name = (get_env_config(target_env) or {}).get("name", target_env.upper()) if target_env else "全部环境"
        if not clear_cache(target_env):
            self._show_message("Jenkins 缓存刷新失败", f"无法清理【{target_name}】的本地缓存。", 0x10)
            return

        if target_env:
            success, jobs_or_error = self.manager.get_jobs_for_env(target_env, force_refresh=True)
        else:
            success, jobs_or_error = self.manager.get_all_jobs(force_refresh=True)

        if success:
            job_count = len(jobs_or_error) if isinstance(jobs_or_error, list) else 0
            self._show_message("Jenkins 缓存刷新成功", f"【{target_name}】已同步，共获取 {job_count} 个流水线。")
        else:
            self._show_message("Jenkins 缓存刷新失败", f"【{target_name}】缓存已清理，但同步失败：\n{jobs_or_error}", 0x10)


if __name__ == "__main__":
    JenkinsLauncher()
