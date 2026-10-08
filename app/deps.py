"""认证依赖：基于签名 session cookie 的单管理员登录。"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from sqlmodel import Session

from .db import engine
from .models import User


class NotAuthenticated(Exception):
    """未登录，触发重定向到 /login。"""


class PasswordChangeRequired(Exception):
    """已登录但仍在使用默认 / 重置后的密码，触发重定向到 /account/setup。"""


def get_current_user(request: Request) -> Optional[User]:
    try:
        uid = request.session.get("uid")
    except (AssertionError, KeyError):
        return None
    if not uid:
        return None
    with Session(engine) as s:
        return s.get(User, uid)


def require_user(request: Request) -> User:
    user = get_current_user(request)
    if user is None:
        raise NotAuthenticated()
    if user.must_change_password:
        raise PasswordChangeRequired()
    return user
