"""目录服务：抓取 → 规范化 → 快照 upsert → 变更检测(diff)。

产出的「事件」由 P4/P5 的路由层决定是否推送通知；本模块只负责写入 Event 表与维护快照。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from sqlmodel import Session, func, select

from ..db import engine
from ..models import CatalogPlan, CatalogRegion, Event, utcnow
from . import events as ev
from .misaka_client import client
from ..util import fmt_gb, fmt_mem, fmt_transfer

# 目录写入串行化（防止 24h 同步与各任务并发写同一行竞争）
catalog_lock = asyncio.Lock()

_SPEC_FIELDS = [
    ("vcores", "vCPU"), ("memory", "内存"), ("disk", "磁盘"),
    ("transfer", "流量"), ("nvme", "NVMe"),
]


# 接口返回的不可售原因 -> 中文
_REASON_CN = {"out_of_stock": "缺货", "": "缺货"}

_SPEC_FMT = {"memory": fmt_mem, "disk": fmt_gb, "transfer": fmt_transfer}


# ---- 规范化 ---------------------------------------------------------------
def normalize_plan(region: str, p: dict[str, Any]) -> dict[str, Any]:
    tags = p.get("tags") or []
    return {
        "region": region,
        "slug": p.get("slug", ""),
        "name": p.get("name", ""),
        "category": tags[0] if tags else "其他",
        "vcores": int(p.get("vcores") or 0),
        "memory": int(p.get("memory") or 0),
        "disk": int(p.get("disk") or 0),
        "transfer": int(p.get("transfer") or 0),
        "nvme": bool(p.get("nvme")),
        "price_monthly": float(p.get("price_monthly") or 0),
        "available": bool(p.get("available")),
        "unavailable_reason": p.get("unavailable_reason") or "",
        "raw": p,
    }


def normalize_region(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": r.get("slug", ""),
        "name": r.get("name", ""),
        "country": r.get("country", ""),
        "country_code": r.get("country_code", ""),
        "continent": r.get("contient", "") or r.get("continent", ""),
        "available": bool(r.get("available")),
        "raw": r,
    }


def _plan_snap(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d.get(k) for k in
            ("name", "vcores", "memory", "disk", "transfer", "nvme", "price_monthly", "available")}


def _model_snap(m: CatalogPlan) -> dict[str, Any]:
    return {k: getattr(m, k) for k in
            ("name", "vcores", "memory", "disk", "transfer", "nvme", "price_monthly", "available")}


def _ev(type_: str, region: str, slug: str, old, new, detail: str) -> dict[str, Any]:
    return {"type": type_, "region": region, "plan_slug": slug,
            "old": old, "new": new, "detail": detail}


# ---- 变更检测 -------------------------------------------------------------
def detect_plan_changes(old: CatalogPlan, norm: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    region, slug, name = norm["region"], norm["slug"], norm["name"]

    if old.available != norm["available"]:
        if norm["available"]:
            out.append(_ev(ev.RESTOCK, region, slug, _model_snap(old), _plan_snap(norm),
                           f"{name} 补货（{_REASON_CN.get(old.unavailable_reason, old.unavailable_reason or '缺货')} → 有货）"))
        else:
            out.append(_ev(ev.SOLD_OUT, region, slug, _model_snap(old), _plan_snap(norm),
                           f"{name} 售罄"))

    if abs(old.price_monthly - norm["price_monthly"]) > 1e-9:
        out.append(_ev(ev.PRICE_CHANGE, region, slug,
                       {"price_monthly": old.price_monthly}, {"price_monthly": norm["price_monthly"]},
                       f"{name} 月付 ${old.price_monthly} → ${norm['price_monthly']}"))

    spec_diffs = []
    for field, lbl in _SPEC_FIELDS:
        if getattr(old, field) != norm[field]:
            fmt = _SPEC_FMT.get(field, str)
            spec_diffs.append(f"{lbl} {fmt(getattr(old, field))}→{fmt(norm[field])}")
    if spec_diffs:
        out.append(_ev(ev.SPEC_CHANGE, region, slug, _model_snap(old), _plan_snap(norm),
                       f"{name} 规格变更：" + "，".join(spec_diffs)))
    return out


def _apply_plan(session: Session, norm: dict[str, Any], existing: Optional[CatalogPlan],
                *, allow_new_plan_event: bool) -> tuple[CatalogPlan, list[dict[str, Any]]]:
    """upsert 单个套餐，返回 (模型, 事件列表)。"""
    now = utcnow()
    if existing is None:
        plan = CatalogPlan(**norm, first_seen=now, last_seen=now, last_changed=now)
        session.add(plan)
        evs = []
        if allow_new_plan_event:
            evs.append(_ev(ev.NEW_PLAN, norm["region"], norm["slug"], None, _plan_snap(norm),
                           f"新规格上线：{norm['name']}（{norm['region']}）"))
        return plan, evs

    evs = detect_plan_changes(existing, norm)
    for k in ("name", "category", "vcores", "memory", "disk", "transfer", "nvme",
              "price_monthly", "available", "unavailable_reason", "raw"):
        setattr(existing, k, norm[k])
    existing.last_seen = now
    if evs:
        existing.last_changed = now
    session.add(existing)
    return existing, evs


def record_events(session: Session, evs: list[dict[str, Any]], task_id: Optional[int] = None,
                  write_log: bool = True) -> list[Optional[Event]]:
    """按来源（task_id 有值 = 监控任务，否则 = 目录同步）的记录规则写入事件日志。

    返回与 evs 一一对应的列表；未被记录的事件对应 None（不影响后续推送）。"""
    from .logbook import log_event, recorded_types
    source = "task" if task_id else "sync"
    allowed = recorded_types(source)
    rows: list[Optional[Event]] = []
    for e in evs:
        if e["type"] not in allowed and e["type"] not in ev.SYSTEM_EVENTS:
            rows.append(None)
            continue
        row = Event(type=e["type"], region=e.get("region", ""), plan_slug=e.get("plan_slug", ""),
                    old=e.get("old"), new=e.get("new"), detail=e.get("detail", ""), task_id=task_id)
        session.add(row)
        rows.append(row)
        if write_log:
            log_event(e, source)
    return rows


# ---- 全量目录同步（24h 作业 / 手动刷新）-----------------------------------
async def full_sync() -> dict[str, Any]:
    regions, plans_by_region = await client.fetch_all()
    summary = {"regions": len(regions), "plans": 0, "events": 0, "initial": False}

    async with catalog_lock:
        with Session(engine) as s:
            is_initial = s.exec(select(func.count()).select_from(CatalogPlan)).one() == 0
            summary["initial"] = is_initial

            existing_regions = {r.slug: r for r in s.exec(select(CatalogRegion)).all()}
            existing_plans = {(p.region, p.slug): p for p in s.exec(select(CatalogPlan)).all()}
            seen_regions: set[str] = set()
            seen_plans: set[tuple[str, str]] = set()
            all_events: list[dict[str, Any]] = []
            now = utcnow()

            # 区域
            for r in regions:
                norm = normalize_region(r)
                slug = norm["slug"]
                if not slug:
                    continue
                seen_regions.add(slug)
                old = existing_regions.get(slug)
                if old is None:
                    s.add(CatalogRegion(**norm, first_seen=now, last_seen=now, last_changed=now))
                    if not is_initial:
                        all_events.append(_ev(ev.NEW_REGION, slug, "", None,
                                              {"name": norm["name"]}, f"新区域上线：{norm['name']}（{slug}）"))
                else:
                    if old.available != norm["available"]:
                        all_events.append(_ev(ev.REGION_AVAILABILITY, slug, "",
                                              {"available": old.available}, {"available": norm["available"]},
                                              f"区域 {norm['name']} 可用性变化：{old.available}→{norm['available']}"))
                        old.last_changed = now
                    old.name = norm["name"]; old.country = norm["country"]
                    old.country_code = norm["country_code"]; old.available = norm["available"]
                    old.continent = norm["continent"]
                    old.raw = norm["raw"]; old.last_seen = now
                    s.add(old)

            # 套餐
            for region, plans in plans_by_region.items():
                for p in plans:
                    norm = normalize_plan(region, p)
                    if not norm["slug"]:
                        continue
                    summary["plans"] += 1
                    key = (region, norm["slug"])
                    seen_plans.add(key)
                    _, evs = _apply_plan(s, norm, existing_plans.get(key),
                                         allow_new_plan_event=not is_initial)
                    all_events.extend(evs)

            # 下架检测（仅对本次确实抓到 plans 的区域，避免抓取失败误判）
            fetched_regions = {rg for rg, pl in plans_by_region.items() if pl}
            for (region, slug), old in existing_plans.items():
                if region in fetched_regions and (region, slug) not in seen_plans:
                    all_events.append(_ev(ev.REMOVED_PLAN, region, slug, _model_snap(old), None,
                                          f"规格下架：{old.name}（{region}）"))
                    s.delete(old)

            record_events(s, all_events)
            s.commit()
            summary["events"] = len(all_events)
            summary["event_list"] = all_events

    return summary
