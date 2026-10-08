"""任务轮询器：执行单个监控任务，检测目标产品的事件并推送。

设计要点：
- 检测 + 写快照在 catalog_lock 内串行，保证「一次跳变一次通知」的状态一致；
- 网络推送（Telegram）放在锁外，避免长时间持锁。
"""
from __future__ import annotations

from typing import Any

import httpx
from sqlmodel import Session, select

from ..db import engine
from ..models import CatalogPlan, Event, MonitorTask, TaskChannel, TaskTarget, utcnow
from ..util import canon_region, canon_slug
from . import events as ev
from .catalog import _apply_plan, catalog_lock, full_sync, normalize_plan, record_events
from .messages import format_event_message, format_status_summary
from .misaka_client import client
from .notifier import notify_channels
from .order import get_executor


async def run_global_sync() -> dict[str, Any]:
    """全局目录同步作业（24h / 启动 / 手动刷新）：
    full_sync 更新快照并产出目录级事件，随后把这些事件路由到订阅的任务并推送。"""
    from ..db import set_setting
    summary = await full_sync()
    await route_global_events(summary.get("event_list", []))
    summary["targets"] = await check_target_validity(summary.get("event_list", []))
    set_setting("last_catalog_sync", utcnow().isoformat())
    return summary


def _last_removed_snap(s: Session, region: str, slug: str) -> dict[str, Any] | None:
    """从历史「规格下架」事件中找回该套餐下架前的快照（用于失效提醒展示规格价格）。"""
    row = s.exec(select(Event).where(Event.type == ev.REMOVED_PLAN, Event.region == region,
                                     Event.plan_slug == slug).order_by(Event.ts.desc())).first()
    return row.old if row and row.old else None


async def check_target_validity(event_list: list[dict[str, Any]]) -> dict[str, int]:
    """同步后检查启用任务的指定套餐目标：从目录消失 → 失效提醒；重新出现 → 恢复提醒。

    每个目标用 TaskTarget.missing_since 标记，只在状态变化时通知一次；与任务的事件订阅无关。
    整区（slug='*'）与全站（region='*'）目标不检查。"""
    from ..db import get_bool_setting
    from .messages import format_target_validity
    counts = {"invalid": 0, "restored": 0}
    if not get_bool_setting("notify_target_invalid", True):
        return counts

    removed_snaps = {(e.get("region"), e.get("plan_slug")): e.get("old")
                     for e in event_list if e["type"] == ev.REMOVED_PLAN}
    now = utcnow()
    changes: list[tuple[int, str, list[int], list[dict], list[dict]]] = []
    with Session(engine) as s:
        catalog = {(p.region, p.slug): p for p in s.exec(select(CatalogPlan)).all()}
        regions_with_plans = {r for r, _ in catalog}
        if not catalog:  # 目录为空（从未成功同步）时不做判断，避免误报
            return counts
        for t in s.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all():  # noqa: E712
            invalid, restored = [], []
            for tt in s.exec(select(TaskTarget).where(TaskTarget.task_id == t.id)).all():
                if tt.region == "*" or tt.plan_slug == "*":
                    continue
                key = (canon_region(tt.region), canon_slug(tt.plan_slug))
                if key[0] not in regions_with_plans:  # 该区域整体无数据（可能抓取失败），跳过
                    continue
                plan = catalog.get(key)
                if plan is None and tt.missing_since is None:
                    tt.missing_since = now
                    s.add(tt)
                    invalid.append({"region": key[0], "slug": key[1],
                                    "snap": removed_snaps.get(key) or _last_removed_snap(s, *key)})
                elif plan is not None and tt.missing_since is not None:
                    tt.missing_since = None
                    s.add(tt)
                    restored.append({"region": key[0], "slug": key[1],
                                     "name": plan.name, "price": plan.price_monthly})
            if invalid or restored:
                chans = [tc.channel_id for tc in
                         s.exec(select(TaskChannel).where(TaskChannel.task_id == t.id)).all()]
                changes.append((t.id, t.name, chans, invalid, restored))
        s.commit()

    # 记录监控事件 + 每个任务合并推送一条（锁外）
    for tid, tname, chans, invalid, restored in changes:
        evs = [{"type": ev.TARGET_INVALID, "region": x["region"], "plan_slug": x["slug"],
                "old": x["snap"], "new": None,
                "detail": f"监控目标失效：{(x['snap'] or {}).get('name') or x['slug']}（{x['region']}）已下架"}
               for x in invalid]
        evs += [{"type": ev.TARGET_RESTORED, "region": x["region"], "plan_slug": x["slug"],
                 "old": None, "new": {"name": x["name"], "price_monthly": x["price"]},
                 "detail": f"监控目标恢复：{x['name']}（{x['region']}）重新上架"}
                for x in restored]
        with Session(engine) as s:
            rows = record_events(s, evs, task_id=tid)
            s.commit()
            ids = [r.id for r in rows if r]
        results = await notify_channels(chans, format_target_validity(tname, invalid, restored),
                                        task_id=tid, event_id=ids[0] if ids else None)
        ok = any(r_ok for _, r_ok, _ in results)
        if ok and ids:
            with Session(engine) as s:
                for eid in ids:
                    row = s.get(Event, eid)
                    if row:
                        row.notified = True
                        s.add(row)
                s.commit()
        counts["invalid"] += len(invalid)
        counts["restored"] += len(restored)
    return counts


def _target_matches(targets: list[tuple[str, str]], region: str, slug: str) -> bool:
    """判断任务目标是否覆盖某事件的 (region, slug)。支持通配 '*'。

    - region 级事件（slug 为空）：任务有该区域的任意目标，或 region='*' 即匹配。
    """
    for tr, ts in targets:
        if tr == "*":
            if ts == "*" or ts == slug:
                return True
        elif tr == region:
            if ts == "*" or ts == slug or slug == "":
                return True
    return False


async def route_global_events(event_list: list[dict[str, Any]]) -> int:
    """把全局同步产出的事件路由到订阅任务：记录 task 维度事件并推送。返回推送命中数。"""
    if not event_list:
        return 0

    # 预载启用任务的目标/渠道/订阅
    with Session(engine) as s:
        tasks = s.exec(select(MonitorTask).where(MonitorTask.enabled == True)).all()  # noqa: E712
        task_meta = []
        for t in tasks:
            targets = [(tt.region, tt.plan_slug) for tt in
                       s.exec(select(TaskTarget).where(TaskTarget.task_id == t.id)).all()]
            chans = [tc.channel_id for tc in
                     s.exec(select(TaskChannel).where(TaskChannel.task_id == t.id)).all()]
            task_meta.append((t.id, t.name, set(t.event_types or []), targets, chans))

    sent = 0
    for ev_dict in event_list:
        etype = ev_dict["type"]
        region = ev_dict.get("region", "")
        slug = ev_dict.get("plan_slug", "")
        for tid, tname, subscribed, targets, chans in task_meta:
            if etype not in subscribed:
                continue
            if not _target_matches(targets, region, slug):
                continue
            with Session(engine) as s:
                rows = record_events(s, [ev_dict], task_id=tid)
                s.commit()
                event_id = rows[0].id if rows[0] else None
            msg = format_event_message(ev_dict, tname)
            results = await notify_channels(chans, msg, task_id=tid, event_id=event_id)
            ok = any(r_ok for _, r_ok, _ in results)
            if ok:
                sent += 1
            if event_id is None:
                continue
            with Session(engine) as s:
                row = s.get(Event, event_id)
                if row:
                    row.notified = ok
                    s.add(row)
                    s.commit()
    return sent


async def run_task(task_id: int, force_status_push: bool = False,
                   status_kind: str | None = None) -> dict[str, Any]:
    # 1) 载入任务配置
    with Session(engine) as s:
        task = s.get(MonitorTask, task_id)
        if not task or not task.enabled:
            return {"target_status": [], "hits": 0, "sent": 0, "first_run": False, "status_pushed": False}
        targets = s.exec(select(TaskTarget).where(TaskTarget.task_id == task_id)).all()
        channel_ids = [tc.channel_id for tc in
                       s.exec(select(TaskChannel).where(TaskChannel.task_id == task_id)).all()]
        subscribed = set(task.event_types or [])
        task_name = task.name
        auto_order = task.auto_order
        order_params = task.order_params or {}
        first_run = task.last_run_at is None

    # 2) 目标按区域归组（plan_slug='*' = 整个区域；region='*' 交给全局同步）
    regions: dict[str, set[str]] = {}
    for t in targets:
        if t.region == "*":
            continue
        regions.setdefault(canon_region(t.region), set()).add(
            "*" if t.plan_slug == "*" else canon_slug(t.plan_slug))

    to_notify: list[tuple[dict[str, Any], int]] = []
    target_status: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []   # 指定了 slug 但 API 本次未返回（多为已下架）
    errors: list[str] = []

    for region, slugs in regions.items():
        try:
            plans = await client.fetch_region_plans(region)
        except httpx.HTTPError as e:
            errors.append(f"{region}:抓取失败({type(e).__name__})")
            continue

        watch_all = "*" in slugs
        candidates = [p for p in plans
                      if watch_all or canon_slug(p.get("slug", "")) in slugs]

        returned = {canon_slug(p.get("slug", "")) for p in candidates}
        missing.extend({"region": region, "slug": sl} for sl in sorted(slugs)
                       if sl != "*" and sl not in returned)

        # 收集当前状态（用于首运行/手动运行反馈）
        for p in candidates:
            target_status.append({"region": region, "slug": canon_slug(p.get("slug", "")),
                                  "name": p.get("name", ""), "available": bool(p.get("available")),
                                  "price": p.get("price_monthly")})

        async with catalog_lock:
            with Session(engine) as s:
                existing_map = {
                    (p.region, p.slug): p
                    for p in s.exec(select(CatalogPlan).where(CatalogPlan.region == region)).all()
                }
                detected: list[dict[str, Any]] = []
                for p in candidates:
                    norm = normalize_plan(region, p)
                    if not norm["slug"]:
                        continue
                    existing = existing_map.get((region, norm["slug"]))
                    # 目标必须已在目录中（由全局同步收录）；未知规格留给全局同步处理，
                    # 避免在此静默建行而掩盖 new_plan 检测。
                    if existing is None:
                        continue
                    _, evs = _apply_plan(s, norm, existing, allow_new_plan_event=False)
                    detected.extend(e for e in evs if e["type"] in subscribed)
                rows = record_events(s, detected, task_id=task_id)
                s.commit()
                to_notify.extend((e, r.id if r else None) for e, r in zip(detected, rows))

    # 3) 锁外推送
    sent = 0
    for ev_dict, event_id in to_notify:
        msg = format_event_message(ev_dict, task_name)
        # 预留：补货且开启自动下单时，调用下单执行器（当前 Noop）
        if auto_order and ev_dict["type"] == ev.RESTOCK:
            res = await get_executor().place_order(
                task_id=task_id, task_name=task_name,
                region=ev_dict.get("region", ""), slug=ev_dict.get("plan_slug", ""),
                plan_snapshot=ev_dict.get("new") or {}, order_params=order_params)
            msg += f"\n🤖 自动下单：{res.message}"
        results = await notify_channels(channel_ids, msg, task_id=task_id, event_id=event_id)
        ok = any(r_ok for _, r_ok, _ in results)
        if ok:
            sent += 1
        if event_id is None:
            continue
        with Session(engine) as s:
            row = s.get(Event, event_id)
            if row:
                row.notified = ok
                s.add(row)
                s.commit()

    # 3.5) 首次运行 / 恢复启用 / 手动请求：向渠道推送状态报告
    status_pushed = False
    if (first_run or force_status_push) and channel_ids and (target_status or missing):
        kind = status_kind or ("start" if first_run else "status")
        summary_msg = format_status_summary(task_name, target_status, kind=kind, missing=missing)
        res = await notify_channels(channel_ids, summary_msg, task_id=task_id)
        status_pushed = any(ok for _, ok, _ in res)

    # 4) 更新任务状态
    with Session(engine) as s:
        task = s.get(MonitorTask, task_id)
        if task:
            task.last_run_at = utcnow()
            status = f"命中 {len(to_notify)} · 推送 {sent}" if to_notify else "正常"
            if errors:
                status += " · " + "，".join(errors)
            task.last_status = status[:200]
            s.add(task)
            s.commit()

    return {"target_status": target_status, "hits": len(to_notify), "sent": sent,
            "first_run": first_run, "status_pushed": status_pushed, "errors": errors,
            "missing": missing}
