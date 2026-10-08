"""APScheduler 调度器（进程内）：每个监控任务一个作业 + 一个全局目录同步作业。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, func, select

from ..config import settings
from ..db import engine, get_setting
from ..models import CatalogPlan, MonitorTask
from .poller import run_global_sync, run_task

scheduler = AsyncIOScheduler(timezone="UTC")


def _min_interval() -> int:
    try:
        return int(get_setting("min_interval_seconds") or settings.min_interval_seconds)
    except (TypeError, ValueError):
        return settings.min_interval_seconds


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- 任务作业 -------------------------------------------------------------
def add_task_job(task: MonitorTask, run_soon: bool = True) -> None:
    interval = max(_min_interval(), int(task.interval_seconds or 60))
    scheduler.add_job(
        run_task, "interval", seconds=interval, id=f"task:{task.id}", args=[task.id],
        replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=30,
        next_run_time=_now() + timedelta(seconds=2) if run_soon else None,
    )


def remove_task_job(task_id: int) -> None:
    try:
        scheduler.remove_job(f"task:{task_id}")
    except Exception:
        pass


def reschedule_task(task: MonitorTask) -> None:
    if task.enabled:
        add_task_job(task)
    else:
        remove_task_job(task.id)


# ---- 全局目录同步作业 -----------------------------------------------------
def schedule_global_sync() -> None:
    try:
        hours = float(get_setting("catalog_sync_hours") or settings.catalog_sync_hours)
    except (TypeError, ValueError):
        hours = settings.catalog_sync_hours
    hours = max(0.1, hours)
    scheduler.add_job(
        run_global_sync, "interval", hours=hours, id="global_sync",
        replace_existing=True, max_instances=1, coalesce=True,
    )


# ---- 启动装配 -------------------------------------------------------------
def setup_jobs() -> None:
    with Session(engine) as s:
        tasks = s.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all()  # noqa: E712
        plan_count = s.exec(select(func.count()).select_from(CatalogPlan)).one()
    for t in tasks:
        add_task_job(t, run_soon=True)
    schedule_global_sync()
    # 目录为空时启动后尽快做一次基线同步
    if plan_count == 0:
        scheduler.add_job(run_global_sync, id="initial_sync", replace_existing=True,
                          next_run_time=_now() + timedelta(seconds=1))


def start() -> None:
    if not scheduler.running:
        scheduler.start()


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
