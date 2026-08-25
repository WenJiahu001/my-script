# -*- coding: utf-8 -*-
"""流水线触发历史与高频项目权重管理。"""

import json
import os
import tempfile
import time
from contextlib import contextmanager

from config import DATA_DIR

CURRENT_DIR = DATA_DIR
HISTORY_FILE = os.path.join(CURRENT_DIR, "history.json")
LOCK_FILE = f"{HISTORY_FILE}.lock"


@contextmanager
def _history_lock(timeout_seconds: float = 2.0):
    """跨进程互斥，防止多个 Flow Launcher 进程覆盖历史记录。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK_FILE) > 30:
                    os.unlink(LOCK_FILE)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("历史记录正被其他插件进程更新")
            time.sleep(0.03)
    try:
        yield
    finally:
        try:
            os.unlink(LOCK_FILE)
        except FileNotFoundError:
            pass


def load_history() -> dict:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_history(history_data: dict) -> bool:
    os.makedirs(CURRENT_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".jenkins_history_", suffix=".tmp", dir=CURRENT_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, HISTORY_FILE)
        return True
    except (OSError, TypeError):
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False


def record_job_trigger(env_key: str, job_url: str, job_name: str) -> None:
    if not job_url and not job_name:
        return
    try:
        with _history_lock():
            history = load_history()
            key, now = job_url or job_name, time.time()
            item = history.setdefault(key, {"name": job_name, "url": job_url, "env_key": env_key, "count": 0})
            item.update({"name": job_name or item.get("name", ""), "env_key": env_key, "last_triggered_at": now})
            item["count"] = int(item.get("count", 0)) + 1
            save_history(history)
    except (OSError, TimeoutError, ValueError):
        pass


def get_job_weight(job_url: str, job_name: str, history: dict | None = None) -> tuple[int, float]:
    """传入历史快照时不再重复读取磁盘。"""
    history = history if history is not None else load_history()
    item = history.get(job_url or job_name)
    if not item:
        item = next((value for value in history.values() if value.get("name") == job_name), None)
    if not isinstance(item, dict):
        return 0, 0.0
    return int(item.get("count", 0)), float(item.get("last_triggered_at", 0.0))
