"""日志记录规则 + 文件日志。

- 按来源（sync 目录同步 / task 监控任务）决定哪些事件类型写入事件日志；
- 文件日志只记录被写入事件日志的事件，并受总开关控制。
"""
from __future__ import annotations

import glob
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

from ..config import settings
from ..db import get_bool_setting, get_setting
from . import events as ev

_LOG_PATH = settings.log_dir / "events.log"

_logger = logging.getLogger("misaka.events")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    settings.ensure_dirs()
    _h = RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_h)


def recorded_types(source: str) -> set[str]:
    """某来源（'sync' / 'task'）需要记录的事件类型。"""
    raw = get_setting(f"{source}_log_types")
    if raw is None:
        return set(ev.DEFAULT_LOG_TYPES.get(source, ev.ALL_EVENT_TYPES))
    return {t for t in raw.split(",") if t}


def log_event(event: dict[str, Any], source: str = "") -> None:
    """写入文件日志（调用方已按来源过滤类型）。"""
    if not get_bool_setting("log_enabled", True):
        return
    etype = event.get("type", "")
    region = event.get("region", "")
    slug = event.get("plan_slug", "")
    where = f"{region}/{slug}" if slug else region
    tag = {"sync": "同步", "task": "监控"}.get(source, "")
    prefix = f"[{tag}·{ev.label(etype)}]" if tag else f"[{ev.label(etype)}]"
    _logger.info(f"{prefix} {where} | {event.get('detail', '')}")


def clear_file_log() -> None:
    """清空当前文件日志并删除轮转备份。"""
    for h in _logger.handlers:
        if isinstance(h, RotatingFileHandler) and h.stream:
            h.acquire()
            try:
                h.stream.seek(0)
                h.stream.truncate()
            finally:
                h.release()
    for backup in glob.glob(f"{_LOG_PATH}.*"):
        try:
            os.remove(backup)
        except OSError:
            pass


def log_path() -> str:
    return str(_LOG_PATH)
