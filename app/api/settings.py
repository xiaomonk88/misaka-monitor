"""设置：常规参数、日志分项开关、修改密码。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..db import engine, get_bool_setting, get_setting, set_setting
from ..deps import require_user
from ..models import MonitorTask, User
from ..security import hash_password, verify_password
from ..templating import flash, render
from ..services import scheduler as sched

router = APIRouter()



@router.get("/settings")
def settings_page(request: Request, user=Depends(require_user)):
    general = {
        "display_tz": get_setting("display_tz") or "UTC",
        "catalog_sync_hours": get_setting("catalog_sync_hours") or "24",
        "min_interval_seconds": get_setting("min_interval_seconds") or "15",
    }
    return render(request, "settings.html", user=user, general=general,
                  notify_invalid=get_bool_setting("notify_target_invalid", True))


@router.post("/settings")
async def settings_save(request: Request, user=Depends(require_user)):
    form = await request.form()
    set_setting("display_tz", (form.get("display_tz") or "UTC").strip())
    set_setting("catalog_sync_hours", str(form.get("catalog_sync_hours") or "24").strip())
    set_setting("min_interval_seconds", str(form.get("min_interval_seconds") or "15").strip())
    # 日志记录规则已移至日志页「记录设置」（POST /logs/settings），此处不再处理
    set_setting("notify_target_invalid", "1" if form.get("notify_target_invalid") == "on" else "0")
    # 应用新的同步间隔与最小轮询间隔
    sched.schedule_global_sync()
    with Session(engine) as s:
        for t in s.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all():  # noqa: E712
            sched.add_task_job(t, run_soon=False)
    flash(request, "设置已保存", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/account")
def change_account(request: Request, user=Depends(require_user),
                   username: str = Form(...), old_password: str = Form(...),
                   new_password: str = Form(""), confirm_password: str = Form("")):
    """修改用户名和/或密码；新密码留空表示不修改。均需验证当前密码。"""
    username = username.strip()
    if not username:
        flash(request, "用户名不能为空", "error")
        return RedirectResponse("/settings", status_code=303)
    if new_password or confirm_password:
        if new_password != confirm_password:
            flash(request, "两次新密码不一致", "error")
            return RedirectResponse("/settings", status_code=303)
        if len(new_password) < 6:
            flash(request, "新密码至少 6 位", "error")
            return RedirectResponse("/settings", status_code=303)
    with Session(engine) as s:
        u = s.get(User, user.id)
        if not u or not verify_password(old_password, u.password_hash):
            flash(request, "当前密码错误", "error")
            return RedirectResponse("/settings", status_code=303)
        changed = []
        if username != u.username:
            if s.exec(select(User).where(User.username == username, User.id != u.id)).first():
                flash(request, f"用户名「{username}」已被占用", "error")
                return RedirectResponse("/settings", status_code=303)
            u.username = username
            changed.append("用户名")
        if new_password:
            u.password_hash = hash_password(new_password)
            changed.append("密码")
        if not changed:
            flash(request, "没有需要保存的修改", "info")
            return RedirectResponse("/settings", status_code=303)
        s.add(u)
        s.commit()
    flash(request, f"已更新{'和'.join(changed)}，下次登录请使用新的凭证", "success")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
def change_password(request: Request, user=Depends(require_user),
                    old_password: str = Form(...), new_password: str = Form(...),
                    confirm_password: str = Form(...)):
    if new_password != confirm_password:
        flash(request, "两次新密码不一致", "error")
        return RedirectResponse("/settings", status_code=303)
    if len(new_password) < 6:
        flash(request, "新密码至少 6 位", "error")
        return RedirectResponse("/settings", status_code=303)
    with Session(engine) as s:
        u = s.get(User, user.id)
        if not u or not verify_password(old_password, u.password_hash):
            flash(request, "原密码错误", "error")
            return RedirectResponse("/settings", status_code=303)
        u.password_hash = hash_password(new_password)
        s.add(u)
        s.commit()
    flash(request, "密码已修改", "success")
    return RedirectResponse("/settings", status_code=303)
