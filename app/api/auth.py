"""登录 / 退出 / 首次登录强制修改密码。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..db import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, engine
from ..deps import get_current_user
from ..models import User
from ..security import hash_password, verify_password
from ..templating import flash, render

router = APIRouter()


def _default_hint() -> str | None:
    """仍有账号未修改默认密码时，在登录页提示默认账号。"""
    with Session(engine) as s:
        pending = s.exec(select(User).where(User.must_change_password == True)).first()  # noqa: E712
    return f"{DEFAULT_ADMIN_USERNAME} / {DEFAULT_ADMIN_PASSWORD}" if pending else None


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", nav=False, default_hint=_default_hint())


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.username == username)).first()
        if user and verify_password(password, user.password_hash):
            # 兜底：任何时候用默认密码登录成功都要求先改密码
            if password == DEFAULT_ADMIN_PASSWORD and not user.must_change_password:
                user.must_change_password = True
                s.add(user)
                s.commit()
            request.session["uid"] = user.id
            return RedirectResponse("/account/setup" if user.must_change_password else "/", status_code=303)
    return render(request, "login.html", nav=False, default_hint=_default_hint(),
                  flash="账号或密码错误", flash_type="error", status_code=401)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account/setup")
def account_setup_page(request: Request):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.must_change_password:
        return RedirectResponse("/", status_code=303)
    return render(request, "account_setup.html", nav=False, user=user)


@router.post("/account/setup")
def account_setup_submit(request: Request, username: str = Form(...),
                         new_password: str = Form(...), confirm_password: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.must_change_password:
        return RedirectResponse("/", status_code=303)

    username = username.strip()
    error = None
    if not username:
        error = "用户名不能为空"
    elif new_password != confirm_password:
        error = "两次输入的新密码不一致"
    elif len(new_password) < 6:
        error = "新密码至少 6 位"
    elif new_password == DEFAULT_ADMIN_PASSWORD:
        error = "新密码不能是默认密码 admin"
    with Session(engine) as s:
        u = s.get(User, user.id)
        if not error and verify_password(new_password, u.password_hash):
            error = "新密码不能与当前密码相同"
        if not error and s.exec(select(User).where(User.username == username, User.id != u.id)).first():
            error = f"用户名「{username}」已被占用"
        if error:
            return render(request, "account_setup.html", nav=False, user=u, username=username,
                          flash=error, flash_type="error", status_code=400)
        u.username = username
        u.password_hash = hash_password(new_password)
        u.must_change_password = False
        s.add(u)
        s.commit()
    flash(request, "密码已设置，欢迎使用 Misaka 监控", "success")
    return RedirectResponse("/", status_code=303)
