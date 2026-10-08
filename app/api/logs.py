"""日志：监控事件 / 同步日志分来源浏览、分类筛选、导出、清空 + 文件日志查看下载。"""
from __future__ import annotations

import csv
import io
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy import delete
from sqlmodel import Session, col, func, select

from ..db import engine, get_bool_setting, set_setting
from ..deps import require_user
from ..models import CatalogPlan, CatalogRegion, Event, MonitorTask
from ..templating import flash, fmt_dt, render
from ..services import events as ev
from ..services.logbook import clear_file_log, log_path, recorded_types

router = APIRouter()
PAGE_SIZE = 50
SOURCES = {"task": "监控事件", "sync": "同步日志"}
EVENT_OPTIONS = [(e, ev.label(e)) for e in ev.ALL_EVENT_TYPES]


def _source_filter(stmt, src: str):
    if src == "task":
        return stmt.where(col(Event.task_id).is_not(None))
    if src == "sync":
        return stmt.where(col(Event.task_id).is_(None))
    return stmt


def _parse_product(raw: str) -> tuple[str, str] | None:
    """产品筛选参数 "REGION|slug"（slug 可为空，表示区域级事件）。"""
    if "|" not in (raw or ""):
        return None
    region, slug = raw.split("|", 1)
    return (region.strip(), slug.strip()) if region.strip() else None


def _query(src: str, cat: str, type_: str, q: str, task: int | None, base=None,
           product: tuple[str, str] | None = None):
    stmt = _source_filter(base if base is not None else select(Event), src)
    if type_:
        stmt = stmt.where(Event.type == type_)
    elif cat in ev.CATEGORY_TYPES:
        stmt = stmt.where(col(Event.type).in_(ev.CATEGORY_TYPES[cat]))
    if task and src == "task":
        stmt = stmt.where(Event.task_id == task)
    if product:
        stmt = stmt.where(Event.region == product[0], Event.plan_slug == product[1])
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where((Event.region.like(like)) | (Event.plan_slug.like(like)) | (Event.detail.like(like)))
    return stmt


def _count(s: Session, stmt) -> int:
    return s.exec(stmt).one()


def _task_id(raw: str) -> int | None:
    return int(raw) if raw.strip().isdigit() else None


def _product_names(s: Session, keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """产品显示名：优先当前目录，其次事件快照（已下架的套餐），最后用 slug。"""
    names: dict[tuple[str, str], str] = {}
    regions = {r.slug: r.name for r in s.exec(select(CatalogRegion)).all()}
    plans = {(p.region, p.slug): p.name for p in s.exec(select(CatalogPlan)).all()}
    for region, slug in keys:
        if not slug:
            names[(region, slug)] = f"{regions.get(region, region)}（区域）"
            continue
        name = plans.get((region, slug))
        if not name:
            e = s.exec(select(Event).where(Event.region == region, Event.plan_slug == slug)
                       .order_by(Event.ts.desc())).first()
            snap = (e.new or e.old or {}) if e else {}
            name = snap.get("name") or slug.upper()
        names[(region, slug)] = f"{regions.get(region, region)} · {name}"
    return names


@router.get("/logs")
def logs_page(request: Request, user=Depends(require_user), src: str = "task", cat: str = "",
              type: str = "", q: str = "", task: str = "", product: str = "", view: str = "list",
              page: int = 0):
    src = src if src in SOURCES else "task"
    cat = cat if cat in ev.CATEGORY_TYPES else ""
    type = type if type in ev.LABELS else ""
    if type and cat and type not in ev.CATEGORY_TYPES[cat]:
        type = ""
    view = "product" if view == "product" else "list"
    task = _task_id(task)
    prod = _parse_product(product)
    page = max(0, page)
    counter = select(func.count()).select_from(Event)
    with Session(engine) as s:
        tasks = s.exec(select(MonitorTask).order_by(MonitorTask.id.desc())).all()
        src_counts = {k: _count(s, _source_filter(counter, k)) for k in SOURCES}
        cat_counts = {k: _count(s, _query(src, k, "", q, task, base=counter, product=prod))
                      for k in [""] + list(ev.CATEGORY_TYPES)}
        # 当前分类下（未选分类时为全部类型）各事件类型的数量
        type_keys = ev.CATEGORY_TYPES[cat] if cat else list(ev.LABELS)
        type_counts = {}
        for t in type_keys:
            n = _count(s, _query(src, "", t, q, task, base=counter, product=prod))
            if n or t == type or cat:
                type_counts[t] = n

        rows, groups = [], []
        if view == "product":
            # 按产品聚合：每个产品各类型次数 + 最近时间
            agg = s.exec(_query(src, cat, type, q, task, product=prod,
                                base=select(Event.region, Event.plan_slug, Event.type,
                                            func.count(), func.max(Event.ts)))
                         .group_by(Event.region, Event.plan_slug, Event.type)).all()
            by_key: dict[tuple[str, str], dict] = {}
            for region, slug, etype, n, last in agg:
                g = by_key.setdefault((region, slug or ""), {"region": region, "slug": slug or "",
                                                             "types": {}, "total": 0, "last": last})
                g["types"][etype] = n
                g["total"] += n
                g["last"] = max(g["last"], last)
            names = _product_names(s, set(by_key))
            for k, g in by_key.items():
                g["name"] = names[k]
                g["key"] = f"{k[0]}|{k[1]}"
            groups = sorted(by_key.values(), key=lambda g: g["last"], reverse=True)
        else:
            rows = s.exec(_query(src, cat, type, q, task, product=prod).order_by(Event.ts.desc())
                          .offset(page * PAGE_SIZE).limit(PAGE_SIZE)).all()
        names = _product_names(s, {(e.region, e.plan_slug or "") for e in rows} | ({prod} if prod else set()))
        # 选中单个产品时的概览：各类型次数、首末时间
        prod_summary = None
        if prod:
            agg = s.exec(_query(src, "", "", "", task, product=prod,
                                base=select(Event.type, func.count(), func.min(Event.ts), func.max(Event.ts)))
                         .group_by(Event.type)).all()
            prod_summary = {"name": names.get(prod, prod[1]), "region": prod[0], "slug": prod[1],
                            "types": {t: n for t, n, _, _ in agg},
                            "first": min((a for _, _, a, _ in agg), default=None),
                            "last": max((b for _, _, _, b in agg), default=None)}
    task_names = {t.id: t.name for t in tasks}
    return render(request, "logs.html", user=user, rows=rows, groups=groups, task_names=task_names, tasks=tasks,
                  src=src, sources=SOURCES, src_counts=src_counts,
                  cat=cat, categories=ev.CATEGORIES, cat_counts=cat_counts, type_counts=type_counts,
                  type=type, q=q, task=task, product=product if prod else "", prod_summary=prod_summary,
                  names=names, view=view, page=page, has_next=len(rows) == PAGE_SIZE, label=ev.label,
                  log_types={k: recorded_types(k) for k in SOURCES}, event_options=EVENT_OPTIONS,
                  stock_types=ev.AVAILABILITY_EVENTS, file_log=get_bool_setting("log_enabled", True))


@router.post("/logs/settings")
async def logs_settings_save(request: Request, user=Depends(require_user)):
    """日志记录规则：各来源记录的事件类型 + 文件日志开关（未勾选的 checkbox 不在表单中）。"""
    form = await request.form()
    for src in SOURCES:
        types = [t for t in form.getlist(f"{src}_types") if t in ev.ALL_EVENT_TYPES]
        set_setting(f"{src}_log_types", ",".join(types))
    set_setting("log_enabled", "1" if form.get("log_enabled") == "on" else "0")
    flash(request, "日志记录设置已保存，从下一次同步 / 轮询开始生效", "success")
    back = str(form.get("back") or "")
    return RedirectResponse(back if back.startswith("/logs") else "/logs", status_code=303)


@router.get("/logs/export.csv")
def logs_export(request: Request, user=Depends(require_user), src: str = "", cat: str = "",
                type: str = "", q: str = "", task: str = "", product: str = ""):
    task = _task_id(task)
    with Session(engine) as s:
        rows = s.exec(_query(src, cat, type, q, task, product=_parse_product(product))
                      .order_by(Event.ts.desc()).limit(5000)).all()
        task_names = {t.id: t.name for t in s.exec(select(MonitorTask)).all()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "source", "type", "region", "plan", "detail", "task_id", "task", "notified"])
    for e in rows:
        w.writerow([fmt_dt(e.ts), "task" if e.task_id else "sync", e.type, e.region, e.plan_slug,
                    e.detail, e.task_id or "", task_names.get(e.task_id, "") if e.task_id else "",
                    int(e.notified)])
    name = f"misaka_{src or 'all'}_events.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@router.post("/logs/clear")
def logs_clear(request: Request, user=Depends(require_user), scope: str = Form("task")):
    """scope: task 清空监控事件 / sync 清空同步日志 / all 清空全部事件日志与文件日志。"""
    with Session(engine) as s:
        stmt = delete(Event)
        if scope in ("task", "sync"):
            stmt = stmt.where(col(Event.task_id).is_not(None) if scope == "task" else col(Event.task_id).is_(None))
        n = s.execute(stmt).rowcount
        s.commit()
    if scope == "all":
        clear_file_log()
    what = {"task": "监控事件", "sync": "同步日志"}.get(scope, "全部事件日志与文件日志")
    flash(request, f"已清空{what}（{n} 条）", "success")
    back = scope if scope in SOURCES else "task"
    return RedirectResponse(f"/logs?src={back}", status_code=303)


@router.get("/logs/file", response_class=PlainTextResponse)
def logs_file(request: Request, user=Depends(require_user), lines: int = 200):
    path = log_path()
    if not os.path.exists(path):
        return PlainTextResponse("（暂无文件日志）")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.readlines()
    return PlainTextResponse("".join(content[-lines:]) or "（暂无文件日志）")


@router.get("/logs/file/download")
def logs_file_download(request: Request, user=Depends(require_user)):
    path = log_path()
    if not os.path.exists(path):
        return PlainTextResponse("（暂无文件日志）", status_code=404)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read()
    return Response(data, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=events.log"})
