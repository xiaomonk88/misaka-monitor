"""把事件/状态格式化为 Telegram 文本消息。

产品行统一格式：大洲 | 区域名 | 型号 | 规格(含流量) | $价格/月 | 下单网址（可点击跳转）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from ..db import engine
from ..models import CatalogPlan, CatalogRegion
from ..util import continent_cn, fmt_price, fmt_specs, order_url
from . import events as ev

_HEAD = {
    ev.RESTOCK: "🟢 补货提醒",
    ev.SOLD_OUT: "🔴 售罄提醒",
    ev.PRICE_CHANGE: "💰 价格变更",
    ev.SPEC_CHANGE: "⚙️ 规格变更",
    ev.NEW_PLAN: "✨ 新规格上线",
    ev.REMOVED_PLAN: "🗑️ 规格下架",
    ev.NEW_REGION: "🌍 新区域上线",
    ev.REMOVED_REGION: "🗑️ 区域下架",
    ev.REGION_AVAILABILITY: "🔁 区域可用性变化",
}


def _region_info(slug: str) -> tuple[str, str]:
    """返回 (大洲中文, 区域名)。"""
    if not slug:
        return "", ""
    with Session(engine) as s:
        r = s.get(CatalogRegion, slug)
    if not r:
        return "", slug
    return continent_cn(r.continent), r.name or slug


def _plan_lookup(region: str, slug: str) -> Optional[CatalogPlan]:
    if not slug or slug == "*":
        return None
    with Session(engine) as s:
        return s.exec(select(CatalogPlan).where(
            CatalogPlan.region == region, CatalogPlan.slug == slug)).first()


def _price_lookup(region: str, slug: str) -> Optional[float]:
    p = _plan_lookup(region, slug)
    return p.price_monthly if p else None


def product_line(region: str, slug: str, name: str, price, *, with_url: bool = True,
                 specs: Optional[dict[str, Any]] = None) -> str:
    """大洲 | 区域名 | 型号 | 规格(含流量) | $价格/月 | 网址"""
    cont, rname = _region_info(region)
    if specs is None:
        p = _plan_lookup(region, slug)
        specs = {k: getattr(p, k) for k in ("vcores", "memory", "disk", "transfer")} if p else {}
    spec_txt = fmt_specs(specs.get("vcores"), specs.get("memory"), specs.get("disk"), specs.get("transfer"))
    price_txt = fmt_price(price) if price is not None else ""
    parts = [p for p in (cont, rname, (name or slug).upper(), spec_txt, price_txt) if p]
    line = " | ".join(parts)
    if with_url and slug and slug != "*":
        line += " | " + order_url(region, slug)
    return line


def format_event_message(event: dict[str, Any], task_name: Optional[str] = None) -> str:
    etype = event["type"]
    region = event.get("region", "")
    slug = event.get("plan_slug", "")
    snap = event.get("new") or event.get("old") or {}
    name = snap.get("name") or slug
    price = snap.get("price_monthly")
    if price is None and slug:
        price = _price_lookup(region, slug)

    lines = [f"{_HEAD.get(etype, etype)} | Misaka"]
    if task_name:
        lines.append(f"任务：{task_name}")
    lines.append("")

    if slug and slug != "*":
        specs = snap if snap.get("vcores") is not None else None  # 事件快照自带规格（含已下架套餐）
        lines.append(product_line(region, slug, name, price, specs=specs))
    elif region:  # 区域级事件
        cont, rname = _region_info(region)
        lines.append(f"{cont} | {rname} | {region.lower()}")

    if event.get("detail"):
        lines.append(f"📝 {event['detail']}")
    lines.append(f"🕐 {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}")
    return "\n".join(lines)


_SUMMARY_HEADS = {
    "start": "📋 已开始监控",
    "resume": "▶️ 已恢复监控",
    "status": "📋 当前库存状态",
}


def format_status_summary(task_name: str, statuses: list[dict[str, Any]], *, kind: str = "start",
                          missing: Optional[list[dict[str, Any]]] = None) -> str:
    """任务首次运行 / 恢复启用 / 手动查询 的状态汇总消息。"""
    head = _SUMMARY_HEADS.get(kind, _SUMMARY_HEADS["status"])
    lines = [f"{head} | Misaka", f"任务：{task_name}", ""]
    for st in statuses[:20]:
        icon = "🟢" if st.get("available") else "🔴"
        lines.append(f"{icon} " + product_line(
            st.get("region", ""), st.get("slug", ""), st.get("name", ""), st.get("price")))
    if len(statuses) > 20:
        lines.append(f"…等共 {len(statuses)} 个目标")
    for m in (missing or [])[:10]:
        lines.append(f"⚪ {m.get('region', '').lower()}/{m.get('slug', '')} 本次未返回（可能已下架）")
    avail = sum(1 for s in statuses if s.get("available"))
    lines.append("")
    miss = f" · {len(missing)} 个未返回" if missing else ""
    lines.append(f"合计 {avail}/{len(statuses)} 有货{miss} · "
                 f"{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}")
    return "\n".join(lines)


def format_target_validity(task_name: str, invalid: list[dict[str, Any]],
                           restored: list[dict[str, Any]]) -> str:
    """同步检测到监控目标失效（已下架）/ 恢复上架时的提醒。

    invalid 项：{region, slug, snap}，snap 为下架前的套餐快照（可能为空）。"""
    if invalid and not restored:
        head = "⚠️ 监控目标失效"
    elif restored and not invalid:
        head = "✅ 监控目标恢复"
    else:
        head = "🔁 监控目标变化"
    lines = [f"{head} | Misaka", f"任务：{task_name}", ""]
    if invalid:
        lines.append(f"以下 {len(invalid)} 个目标已从 Misaka 目录下架，任务将无法再检测其库存：")
        for t in invalid:
            snap = t.get("snap") or {}
            specs = snap if snap.get("vcores") is not None else {}
            lines.append("❌ " + product_line(t["region"], t["slug"], snap.get("name") or t["slug"],
                                             snap.get("price_monthly"), with_url=False, specs=specs))
        lines.append("建议在任务中移除失效目标，或改为整区监控。")
    if restored:
        if invalid:
            lines.append("")
        lines.append(f"以下 {len(restored)} 个目标已重新上架：")
        for t in restored:
            lines.append("✅ " + product_line(t["region"], t["slug"], t.get("name") or t["slug"], t.get("price")))
    lines.append("")
    lines.append(f"🕐 {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}")
    return "\n".join(lines)
