"""数据库模型（SQLModel / SQLite）。

时间统一存「朴素 UTC」（naive UTC），展示时再按 DISPLAY_TZ 换算，避免 SQLite 时区歧义。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """当前 UTC 时间（朴素，无 tzinfo）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------------------
# 账号
# ----------------------------------------------------------------------------
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    # 使用默认密码 / 被重置后为 True：登录后必须先修改密码才能进入系统
    must_change_password: bool = False
    created_at: datetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------
# 通知渠道（支持多个 TG；type 预留 bark/webhook/email）
# ----------------------------------------------------------------------------
class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(default="telegram")
    name: str
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------
# 监控任务
# ----------------------------------------------------------------------------
class MonitorTask(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True
    interval_seconds: int = 60
    # 订阅的事件类型列表，见 services/events.py 的 EVENT_TYPES
    event_types: list = Field(default_factory=list, sa_column=Column(JSON))
    notify_policy: str = Field(default="once")  # once（跳变一次）
    # 自动下单（预留，当前仅 Noop）
    auto_order: bool = False
    order_params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_run_at: Optional[datetime] = None
    last_status: str = ""  # 上次运行结果摘要


class TaskTarget(SQLModel, table=True):
    """任务监控的目标。plan_slug='*' 表示整个区域；region='*' 表示全站（用于新区域检测）。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(index=True, foreign_key="monitortask.id")
    region: str
    plan_slug: str
    # 同步时发现该目标已从目录消失的时间（已通知过失效）；重新上架后清空
    missing_since: Optional[datetime] = None


class TaskChannel(SQLModel, table=True):
    """任务 ↔ 通知渠道 多对多。"""
    __table_args__ = (UniqueConstraint("task_id", "channel_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(index=True, foreign_key="monitortask.id")
    channel_id: int = Field(index=True, foreign_key="channel.id")


# ----------------------------------------------------------------------------
# 目录快照（全站产品）
# ----------------------------------------------------------------------------
class CatalogRegion(SQLModel, table=True):
    slug: str = Field(primary_key=True)
    name: str = ""
    country: str = ""
    country_code: str = ""
    continent: str = ""
    available: bool = True
    raw: dict = Field(default_factory=dict, sa_column=Column(JSON))
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    last_changed: datetime = Field(default_factory=utcnow)


class CatalogPlan(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("region", "slug"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    region: str = Field(index=True)
    slug: str = Field(index=True)
    name: str = ""
    category: str = ""   # 产品分类（来自 API tags[0]，如 "Standard NVMe (Gen3, AMD EPYC)"）
    vcores: int = 0
    memory: int = 0       # MiB
    disk: int = 0         # MiB
    transfer: int = 0
    nvme: bool = False
    price_monthly: float = 0.0
    available: bool = False
    unavailable_reason: str = ""
    raw: dict = Field(default_factory=dict, sa_column=Column(JSON))
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    last_changed: datetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------
# 事件（结构化日志 / 审计）
# ----------------------------------------------------------------------------
class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    type: str = Field(index=True)
    region: str = ""
    plan_slug: str = ""
    old: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    new: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    detail: str = ""
    task_id: Optional[int] = Field(default=None, index=True)
    notified: bool = False


class NotificationLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    channel_id: Optional[int] = None
    channel_name: str = ""
    task_id: Optional[int] = None
    event_id: Optional[int] = None
    status: str = ""   # ok / failed / skipped
    error: str = ""


# ----------------------------------------------------------------------------
# 全局设置（key-value）
# ----------------------------------------------------------------------------
class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
