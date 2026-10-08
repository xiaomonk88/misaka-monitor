"""数据库引擎、初始化、会话与设置读写。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .models import Setting, User
from .security import hash_password

settings.ensure_dirs()

engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


# 默认管理员账号（新建库或执行 reset_admin 时使用；登录后必须修改密码）
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"

# 首次启动播种的默认设置（之后可在后台修改）
DEFAULT_SETTINGS: dict[str, str] = {
    "catalog_sync_hours": str(settings.catalog_sync_hours),
    "display_tz": settings.display_tz,
    "min_interval_seconds": str(settings.min_interval_seconds),
    # 文件日志开关；各来源记录的事件类型（逗号分隔，见 services/events.DEFAULT_LOG_TYPES）
    "log_enabled": "1",
    "sync_log_types": "price_change,spec_change,new_plan,removed_plan,new_region,removed_region,region_availability",
    "task_log_types": "restock,sold_out",
    # 同步时检测启用任务的目标是否失效（下架）/恢复，并推送通知
    "notify_target_invalid": "1",
}


def _migrate() -> None:
    """轻量迁移：为旧库补列并从已存 raw 回填，避免丢失现有任务/事件。"""
    from sqlalchemy import text
    with engine.begin() as conn:
        plan_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(catalogplan)"))}
        if "category" not in plan_cols:
            conn.execute(text("ALTER TABLE catalogplan ADD COLUMN category VARCHAR DEFAULT ''"))
        region_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(catalogregion)"))}
        if "continent" not in region_cols:
            conn.execute(text("ALTER TABLE catalogregion ADD COLUMN continent VARCHAR DEFAULT ''"))
        user_cols = {r[1] for r in conn.execute(text('PRAGMA table_info("user")'))}
        if "must_change_password" not in user_cols:
            conn.execute(text('ALTER TABLE "user" ADD COLUMN must_change_password BOOLEAN DEFAULT 0'))
        target_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tasktarget)"))}
        if "missing_since" not in target_cols:
            conn.execute(text("ALTER TABLE tasktarget ADD COLUMN missing_since DATETIME"))

    # 回填 category（从 plan.raw.tags[0]）
    with Session(engine) as s:
        from .models import CatalogPlan, CatalogRegion
        for p in s.exec(select(CatalogPlan).where((CatalogPlan.category == "") | (CatalogPlan.category.is_(None)))).all():
            tags = (p.raw or {}).get("tags") or []
            p.category = tags[0] if tags else "其他"
            s.add(p)
        for r in s.exec(select(CatalogRegion).where((CatalogRegion.continent == "") | (CatalogRegion.continent.is_(None)))).all():
            r.continent = (r.raw or {}).get("contient", "") or ""
            s.add(r)
        s.commit()


def init_db() -> None:
    """建表 + 迁移 + 播种管理员与默认设置。"""
    SQLModel.metadata.create_all(engine)
    _migrate()
    with Session(engine) as s:
        # 播种默认管理员 admin/admin，首次登录强制修改密码
        existing = s.exec(select(User)).first()
        if existing is None:
            s.add(User(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                must_change_password=True,
            ))
        # 播种默认设置
        for k, v in DEFAULT_SETTINGS.items():
            if s.get(Setting, k) is None:
                s.add(Setting(key=k, value=v))
        s.commit()


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s


# ---- 设置读写 -------------------------------------------------------------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with Session(engine) as s:
        row = s.get(Setting, key)
        return row.value if row else default


def get_bool_setting(key: str, default: bool = False) -> bool:
    val = get_setting(key)
    if val is None:
        return default
    return val in ("1", "true", "True", "yes", "on")


def set_setting(key: str, value: str) -> None:
    with Session(engine) as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
        s.add(row)
        s.commit()
