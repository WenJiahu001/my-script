# -*- coding: utf-8 -*-
"""多环境配置、环境变量凭据与本地缓存管理。"""

import json
import os
import shutil
import tempfile
import time
import re
import urllib.parse
from copy import deepcopy

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# 缓存、历史与日志保存在用户数据目录；配置文件则由项目目录统一管理。
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "FlowLauncher", "JenkinsLauncher")
CURRENT_DIR = DATA_DIR  # 保留旧变量名，供缓存与既有调用兼容。
CONFIG_SOURCE_POINTER = os.path.join(PLUGIN_DIR, "config_source_path.txt")
def _get_project_config_file() -> str:
    """复制安装时通过安装器写入的指针继续读取原项目主配置。"""
    try:
        with open(CONFIG_SOURCE_POINTER, "r", encoding="utf-8") as f:
            source_dir = f.read().strip()
        source_file = os.path.abspath(os.path.join(source_dir, "config.json"))
        if source_dir and os.path.isfile(source_file):
            return source_file
    except OSError:
        pass
    return os.path.join(PLUGIN_DIR, "config.json")

CONFIG_FILE = _get_project_config_file()
USER_CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
TEMPLATE_FILE = os.path.join(PLUGIN_DIR, "config.json.template")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CONFIG_CACHE: dict | None = None
_CONFIG_CACHE_KEY: tuple[str, int, int] | None = None
_SYNCED_CACHE_KEY: tuple[str, int, int] | None = None

DEFAULT_MULTI_CONFIG = {
    "default_environment": "dev",
    "environments": {
        "dev": {
            "name": "DEV 开发环境", "jenkins_url": "${JENKINS_DEV_URL}",
            "username": "${JENKINS_DEV_USERNAME}", "api_token": "${JENKINS_DEV_API_TOKEN}",
            "views": [], "pinned_jobs": [], "cache_duration_seconds": 600,
            "verify_ssl": True, "timeout": 5, "require_confirm": False,
        },
        "prod": {
            "name": "PROD 生产环境", "jenkins_url": "${JENKINS_PROD_URL}",
            "username": "${JENKINS_PROD_USERNAME}", "api_token": "${JENKINS_PROD_API_TOKEN}",
            "views": [], "pinned_jobs": [], "cache_duration_seconds": 300,
            "verify_ssl": True, "timeout": 5, "require_confirm": True,
        },
    },
}


def _atomic_write_json(path: str, data: dict) -> None:
    """先写临时文件再原子替换，避免并发读取到半截 JSON。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".jenkins_launcher_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _resolve_environment_value(value: object) -> str:
    """解析 ${ENV_NAME} 引用；普通值保留以兼容已有本地配置。"""
    text = str(value or "").strip()
    if text.startswith("${") and text.endswith("}"):
        return os.environ.get(text[2:-1], "").strip()
    return text


def _as_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def get_config_path() -> str:
    return CONFIG_FILE


def get_cache_path(env_key: str = "") -> str:
    clean_env = "".join(c for c in str(env_key) if c.isalnum() or c in ("_", "-"))
    return os.path.join(CURRENT_DIR, f"cache_{clean_env}.json")


def get_views_cache_path(env_key: str = "") -> str:
    clean_env = "".join(c for c in str(env_key) if c.isalnum() or c in ("_", "-"))
    return os.path.join(CURRENT_DIR, f"views_cache_{clean_env}.json")


def _normalize_config(data: dict) -> dict:
    if not isinstance(data, dict):
        return deepcopy(DEFAULT_MULTI_CONFIG)
    raw_envs = data.get("environments")
    normalized_envs: dict[str, dict] = {}
    if isinstance(raw_envs, dict):
        for raw_key, raw_cfg in raw_envs.items():
            env_key = str(raw_key).strip()
            if not ENV_KEY_PATTERN.fullmatch(env_key) or not isinstance(raw_cfg, dict):
                continue
            cfg = deepcopy(raw_cfg)
            cfg["name"] = str(cfg.get("name") or env_key)
            for field in ("jenkins_url", "username", "api_token"):
                cfg[field] = str(cfg.get(field) or "").strip()
            cfg["views"] = [str(item).strip() for item in cfg.get("views", []) if str(item).strip()] if isinstance(cfg.get("views"), list) else []
            cfg["pinned_jobs"] = [str(item).strip() for item in cfg.get("pinned_jobs", []) if str(item).strip()] if isinstance(cfg.get("pinned_jobs"), list) else []
            cfg["cache_duration_seconds"] = _as_int(cfg.get("cache_duration_seconds"), 600, 1, 86400)
            cfg["timeout"] = _as_int(cfg.get("timeout"), 5, 1, 30)
            cfg["verify_ssl"] = cfg.get("verify_ssl") if isinstance(cfg.get("verify_ssl"), bool) else True
            cfg["require_confirm"] = cfg.get("require_confirm") if isinstance(cfg.get("require_confirm"), bool) else False
            normalized_envs[env_key] = cfg
    data["environments"] = normalized_envs or deepcopy(DEFAULT_MULTI_CONFIG["environments"])
    if data.get("default_environment") not in data["environments"]:
        data["default_environment"] = next(iter(data["environments"]))
    return data


def init_config_if_missing() -> dict:
    if not os.path.exists(CONFIG_FILE):
        # 项目配置缺失时，迁移旧用户配置；否则从模板创建。
        if os.path.exists(USER_CONFIG_FILE):
            shutil.copy(USER_CONFIG_FILE, CONFIG_FILE)
        elif os.path.exists(TEMPLATE_FILE):
            shutil.copy(TEMPLATE_FILE, CONFIG_FILE)
        else:
            save_config(DEFAULT_MULTI_CONFIG)
    return load_config()


def _sync_user_config(config_data: dict) -> None:
    """仅从项目主配置单向同步至用户目录，用户目录不参与读取与覆盖。"""
    try:
        current = None
        if os.path.exists(USER_CONFIG_FILE):
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                current = json.load(f)
        if current != config_data:
            _atomic_write_json(USER_CONFIG_FILE, config_data)
    except (OSError, json.JSONDecodeError):
        # 镜像失败不能阻断插件读取主配置。
        pass


def _get_cache_key() -> tuple[str, int, int] | None:
    try:
        stat = os.stat(CONFIG_FILE)
        return CONFIG_FILE, stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def load_config() -> dict:
    global _CONFIG_CACHE, _CONFIG_CACHE_KEY, _SYNCED_CACHE_KEY
    if not os.path.exists(CONFIG_FILE):
        return init_config_if_missing()
    cache_key = _get_cache_key()
    if _CONFIG_CACHE is not None and cache_key == _CONFIG_CACHE_KEY:
        return deepcopy(_CONFIG_CACHE)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config_data = _normalize_config(json.load(f))
        _CONFIG_CACHE, _CONFIG_CACHE_KEY = config_data, cache_key
        if cache_key != _SYNCED_CACHE_KEY:
            _sync_user_config(config_data)
            _SYNCED_CACHE_KEY = cache_key
        return deepcopy(config_data)
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_MULTI_CONFIG)


def save_config(config_data: dict) -> bool:
    global _CONFIG_CACHE, _CONFIG_CACHE_KEY, _SYNCED_CACHE_KEY
    try:
        normalized = _normalize_config(deepcopy(config_data))
        _atomic_write_json(CONFIG_FILE, normalized)
        _CONFIG_CACHE, _CONFIG_CACHE_KEY = normalized, _get_cache_key()
        _sync_user_config(normalized)
        _SYNCED_CACHE_KEY = _CONFIG_CACHE_KEY
        return True
    except OSError:
        return False


def get_default_env_key() -> str:
    config_data = load_config()
    environments = config_data.get("environments", {})
    preferred = str(config_data.get("default_environment", ""))
    return preferred if preferred in environments else next(iter(environments), "")


def get_environments() -> dict[str, dict]:
    return load_config().get("environments", {})


def get_env_config(env_key: str) -> dict | None:
    environments = get_environments()
    env_cfg = environments.get(env_key)
    if env_cfg is None:
        env_cfg = next((value for key, value in environments.items() if key.casefold() == str(env_key).casefold()), None)
    if not isinstance(env_cfg, dict):
        return None
    resolved = deepcopy(env_cfg)
    # 凭据可只存在于当前用户的环境变量中，不进入项目文件。
    for key in ("jenkins_url", "username", "api_token"):
        resolved[key] = _resolve_environment_value(resolved.get(key))
    return resolved


def is_env_configured(env_key: str) -> tuple[bool, str]:
    env_cfg = get_env_config(env_key)
    if not env_cfg:
        return False, f"未找到环境 [{env_key}] 的配置项"
    url, user, token = (str(env_cfg.get(k, "")).strip() for k in ("jenkins_url", "username", "api_token"))
    env_display = env_cfg.get("name", env_key)
    parsed_url = urllib.parse.urlparse(url)
    if not url or not parsed_url.scheme in ("http", "https") or not parsed_url.netloc or url.startswith("http://127.0.0.1") or url.startswith("https://jenkins.prod.example.com"):
        return False, f"[{env_display}] 尚未配置有效的 Jenkins URL"
    if not user or user.startswith("your_"):
        return False, f"[{env_display}] 尚未配置用户名"
    if not token or token.startswith("your_"):
        return False, f"[{env_display}] 尚未配置 API Token"
    return True, "已配置"


def is_any_configured() -> tuple[bool, str]:
    envs = get_environments()
    if not envs:
        return False, "尚未配置任何环境"
    errors = [msg for key in envs for valid, msg in [is_env_configured(key)] if not valid]
    return (True, "就绪") if len(errors) < len(envs) else (False, "；".join(errors))


def load_cache(env_key: str = "", allow_stale: bool = False) -> list[dict] | None:
    try:
        with open(get_cache_path(env_key), "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        if not isinstance(cache_data, dict) or not isinstance(cache_data.get("jobs"), list):
            return None
        expired = time.time() - float(cache_data.get("timestamp", 0)) > float(cache_data.get("duration", 600))
        return cache_data["jobs"] if allow_stale or not expired else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_cache(env_key: str, jobs: list[dict], duration_seconds: int = 600) -> bool:
    try:
        _atomic_write_json(get_cache_path(env_key), {
            "env_key": env_key, "timestamp": time.time(), "duration": max(1, int(duration_seconds)), "jobs": jobs,
        })
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_views_cache(env_key: str) -> list[dict] | None:
    try:
        with open(get_views_cache_path(env_key), "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        expired = time.time() - float(cache_data.get("timestamp", 0)) > float(cache_data.get("duration", 600))
        views = cache_data.get("views")
        return views if not expired and isinstance(views, list) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_views_cache(env_key: str, views: list[dict], duration_seconds: int = 600) -> bool:
    try:
        _atomic_write_json(get_views_cache_path(env_key), {
            "env_key": env_key, "timestamp": time.time(), "duration": max(1, int(duration_seconds)), "views": views,
        })
        return True
    except (OSError, TypeError, ValueError):
        return False


def clear_cache(env_key: str | None = None) -> bool:
    try:
        paths = [get_cache_path(env_key)] if env_key else [
            os.path.join(CURRENT_DIR, name) for name in os.listdir(CURRENT_DIR)
            if (name.startswith("cache_") or name.startswith("views_cache_")) and name.endswith(".json")
        ]
        if env_key:
            paths.append(get_views_cache_path(env_key))
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False
