"""忘记密码时重置管理员账号为默认 admin/admin，下次登录强制修改密码。

用法（在项目根目录）：
    python -m app.reset_admin
Docker：
    docker compose exec misaka-monitor python -m app.reset_admin
"""
from __future__ import annotations

from sqlmodel import Session, select

from .db import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, engine, init_db
from .models import User
from .security import hash_password


def reset_admin() -> str:
    init_db()  # 确保表与新列存在
    with Session(engine) as s:
        users = s.exec(select(User).order_by(User.id)).all()
        user = users[0] if users else User(username=DEFAULT_ADMIN_USERNAME, password_hash="")
        # 其它账号若占用了 admin 用户名，先让出（单管理员系统通常不会出现）
        for other in users[1:]:
            if other.username == DEFAULT_ADMIN_USERNAME:
                other.username = f"{DEFAULT_ADMIN_USERNAME}_{other.id}"
                s.add(other)
        old = user.username
        user.username = DEFAULT_ADMIN_USERNAME
        user.password_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
        user.must_change_password = True
        s.add(user)
        s.commit()
    return old


if __name__ == "__main__":
    old = reset_admin()
    print(f"已重置管理员（原用户名：{old}）为 {DEFAULT_ADMIN_USERNAME} / {DEFAULT_ADMIN_PASSWORD}，"
          f"下次登录时需先修改密码。已登录的会话也会被要求修改密码。")
