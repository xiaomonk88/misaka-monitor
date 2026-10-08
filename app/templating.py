"""Jinja2 模板配置与展示过滤器。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from . import __version__
from .util import fmt_gb, fmt_mem, fmt_transfer

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _display_tz() -> ZoneInfo:
    # 延迟导入避免循环依赖
    from .db import get_setting
    name = get_setting("display_tz") or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def fmt_dt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """朴素 UTC -> 展示时区字符串。"""
    if value is None:
        return "-"
    aware = value.replace(tzinfo=timezone.utc)
    return aware.astimezone(_display_tz()).strftime(fmt)


templates.env.filters["dt"] = fmt_dt
templates.env.filters["mem"] = fmt_mem
templates.env.filters["gb"] = fmt_gb
templates.env.filters["tf"] = fmt_transfer
templates.env.globals["app_version"] = __version__


def render(request, name: str, *, user=None, status_code: int = 200, **ctx):
    """统一渲染：注入 request/user，并从 session 弹出一次性 flash 提示。"""
    flash, flash_type = None, "info"
    try:
        raw = request.session.pop("flash", None)
        if raw:
            flash, flash_type = (raw if isinstance(raw, (list, tuple)) else (raw, "info"))
    except (AssertionError, KeyError):
        pass  # 无 SessionMiddleware 时忽略
    context = {"user": user, "flash": flash, "flash_type": flash_type, **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def flash(request, message: str, level: str = "info") -> None:
    try:
        request.session["flash"] = (message, level)
    except (AssertionError, KeyError):
        pass
