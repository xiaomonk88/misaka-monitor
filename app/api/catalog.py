"""产品目录：级联三栏（区域 → 分类 → 产品），HTMX 局部加载。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..db import engine, get_setting
from ..deps import require_user
from ..models import CatalogPlan, CatalogRegion
from ..templating import flash, render
from ..services.poller import run_global_sync

router = APIRouter()

# 大洲展示顺序（亚洲优先）
CONTINENT_ORDER = ["Asia", "Europe", "North America", "South America", "Africa", "Oceania", ""]
CONTINENT_CN = {
    "Asia": "亚洲", "Europe": "欧洲", "North America": "北美",
    "South America": "南美", "Africa": "非洲", "Oceania": "大洋洲", "": "其他",
}


def _last_sync() -> datetime | None:
    raw = get_setting("last_catalog_sync")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


@router.get("/catalog")
def catalog_page(request: Request, user=Depends(require_user), q: str = ""):
    q_low = q.strip().lower()
    with Session(engine) as s:
        regions = s.exec(select(CatalogRegion)).all()
        plan_rows = s.exec(select(CatalogPlan.region, CatalogPlan.available)).all()
    # 每区库存计数
    avail_by_region: dict[str, int] = {}
    total_by_region: dict[str, int] = {}
    for region, available in plan_rows:
        total_by_region[region] = total_by_region.get(region, 0) + 1
        if available:
            avail_by_region[region] = avail_by_region.get(region, 0) + 1

    def match(r: CatalogRegion) -> bool:
        if not q_low:
            return True
        return q_low in f"{r.slug} {r.name} {r.country} {r.continent}".lower()

    # 按大洲分组
    groups: dict[str, list] = {}
    for r in regions:
        if not match(r):
            continue
        groups.setdefault(r.continent or "", []).append(r)
    ordered_groups = []
    for cont in CONTINENT_ORDER:
        if cont in groups:
            rs = sorted(groups[cont], key=lambda x: x.name)
            ordered_groups.append((CONTINENT_CN.get(cont, cont), rs))
    # 兜底：未在顺序表中的大洲
    for cont, rs in groups.items():
        if cont not in CONTINENT_ORDER:
            ordered_groups.append((CONTINENT_CN.get(cont, cont), sorted(rs, key=lambda x: x.name)))

    totals = {"regions": len(regions), "plans": len(plan_rows),
              "avail": sum(avail_by_region.values())}
    return render(request, "catalog.html", user=user, groups=ordered_groups,
                  avail_by_region=avail_by_region, total_by_region=total_by_region,
                  q=q, totals=totals, last_sync=_last_sync())


def _categories(plans: list[CatalogPlan]) -> list[dict]:
    """从套餐列表汇总分类（名称、数量、是否有货）。"""
    order: list[str] = []
    info: dict[str, dict] = {}
    for p in plans:
        c = p.category or "其他"
        if c not in info:
            info[c] = {"name": c, "count": 0, "avail": 0}
            order.append(c)
        info[c]["count"] += 1
        if p.available:
            info[c]["avail"] += 1
    return [info[c] for c in order]


@router.get("/catalog/r/{region}")
def catalog_region_detail(region: str, request: Request, user=Depends(require_user), cat: str = ""):
    with Session(engine) as s:
        region_obj = s.get(CatalogRegion, region)
        plans = s.exec(select(CatalogPlan).where(CatalogPlan.region == region)
                       .order_by(CatalogPlan.category, CatalogPlan.price_monthly)).all()
    cats = _categories(plans)
    active = cat if any(c["name"] == cat for c in cats) else (cats[0]["name"] if cats else "")
    cat_plans = [p for p in plans if (p.category or "其他") == active]
    return render(request, "_region_detail.html", user=user, region=region,
                  region_obj=region_obj, categories=cats, active=active, plans=cat_plans)


@router.post("/catalog/refresh")
async def catalog_refresh(request: Request, user=Depends(require_user)):
    try:
        summary = await run_global_sync()
        tg = summary.get("targets") or {}
        extra = "".join([f"；{tg['invalid']} 个监控目标失效" if tg.get("invalid") else "",
                         f"；{tg['restored']} 个监控目标恢复" if tg.get("restored") else ""])
        flash(request, f"目录已刷新：{summary['plans']} 套餐 / {summary['regions']} 区域，"
                       f"检测到 {summary['events']} 个变更"
                       f"{'（首次同步建立基线）' if summary['initial'] else ''}{extra}", "success")
    except Exception as e:  # noqa: BLE001
        flash(request, f"刷新失败：{e}", "error")
    return RedirectResponse("/catalog", status_code=303)
