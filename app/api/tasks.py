"""监控任务：从目录勾选创建、列表管理、编辑、启停、立即运行。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..db import engine
from ..deps import require_user
from ..models import Channel, MonitorTask, TaskChannel, TaskTarget, CatalogPlan, utcnow
from ..templating import flash, render
from ..util import canon_region, canon_slug
from ..services import events as ev
from ..services import scheduler as sched
from ..services.poller import run_task

router = APIRouter()

EVENT_OPTIONS = [(e, ev.label(e)) for e in ev.ALL_EVENT_TYPES]
DEFAULT_EVENTS = [ev.RESTOCK, ev.SOLD_OUT]


def _targets_display(pairs: list[tuple[str, str]]) -> list[dict]:
    """把 (region, slug) 解析为带名称/库存的展示项。"""
    out = []
    with Session(engine) as s:
        for region, slug in pairs:
            if slug == "*":
                out.append({"region": region, "slug": "*", "name": "（整个区域所有规格）", "available": None})
                continue
            p = s.exec(select(CatalogPlan).where(CatalogPlan.region == region,
                                                 CatalogPlan.slug == slug)).first()
            out.append({"region": region, "slug": slug,
                        "name": p.name if p else slug,
                        "available": p.available if p else None,
                        "plan": p})
    return out


def _target_status_list(s: Session, task_id: int) -> list[dict]:
    """任务各目标的实时库存（读目录快照），用于列表展示。"""
    out = []
    for t in s.exec(select(TaskTarget).where(TaskTarget.task_id == task_id)).all():
        if t.region == "*":
            out.append({"region": "*", "slug": t.plan_slug, "label": "全站通配", "available": None, "detail": ""})
        elif t.plan_slug == "*":
            ps = s.exec(select(CatalogPlan).where(CatalogPlan.region == t.region)).all()
            a = sum(1 for p in ps if p.available)
            out.append({"region": t.region, "slug": "*", "label": "整区", "available": a > 0,
                        "detail": f"{a}/{len(ps)} 有货"})
        else:
            p = s.exec(select(CatalogPlan).where(CatalogPlan.region == t.region,
                                                 CatalogPlan.slug == t.plan_slug)).first()
            out.append({"region": t.region, "slug": t.plan_slug,
                        "label": p.name if p else "未收录（可能已下架）",
                        "available": (p.available if p else None), "detail": ""})
    return out


def _create_status_text(targets: list[tuple[str, str]]) -> str:
    """创建任务后的即时状态文本（读目录快照）。"""
    parts = []
    with Session(engine) as s:
        for region, slug in targets[:6]:
            if slug == "*":
                ps = s.exec(select(CatalogPlan).where(CatalogPlan.region == region)).all()
                a = sum(1 for p in ps if p.available)
                parts.append(f"{region.lower()}整区 {a}/{len(ps)}有货")
            else:
                p = s.exec(select(CatalogPlan).where(CatalogPlan.region == region,
                                                     CatalogPlan.slug == slug)).first()
                state = "有货" if (p and p.available) else ("缺货" if p else "未收录")
                parts.append(f"{region.lower()}/{slug}：{state}")
    more = f" 等共{len(targets)}个" if len(targets) > 6 else ""
    return "；".join(parts) + more


@router.get("/tasks")
def tasks_page(request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        tasks = s.exec(select(MonitorTask).order_by(MonitorTask.id.desc())).all()
        data = []
        for t in tasks:
            tcount = len(s.exec(select(TaskTarget).where(TaskTarget.task_id == t.id)).all())
            chans = [s.get(Channel, tc.channel_id) for tc in
                     s.exec(select(TaskChannel).where(TaskChannel.task_id == t.id)).all()]
            data.append({"task": t, "targets": tcount,
                         "channels": [c.name for c in chans if c],
                         "status": _target_status_list(s, t.id)})
    return render(request, "tasks.html", user=user, rows=data)


@router.post("/tasks/new")
async def tasks_new(request: Request, user=Depends(require_user)):
    """从产品目录勾选后进入创建表单（也支持空白创建）。"""
    form = await request.form()
    raw = form.getlist("target")  # ["REGION|slug", ...]
    pairs = []
    for r in raw:
        if "|" in r:
            region, slug = r.split("|", 1)
            pairs.append((canon_region(region), canon_slug(slug) if slug != "*" else "*"))
    if not pairs:
        flash(request, "未选择任何产品，可在下方手动添加，或回目录勾选。", "info")
    with Session(engine) as s:
        channels = s.exec(select(Channel).order_by(Channel.id)).all()
    return render(request, "task_form.html", user=user, mode="create", task=None,
                  targets=_targets_display(pairs), channels=channels,
                  selected_channels=set(), event_options=EVENT_OPTIONS,
                  selected_events=set(DEFAULT_EVENTS), default_interval=60)


@router.post("/tasks/create")
async def tasks_create(request: Request, user=Depends(require_user)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    interval = int(form.get("interval_seconds") or 60)
    event_types = [e for e in form.getlist("event_types") if e in ev.ALL_EVENT_TYPES]
    channel_ids = [int(x) for x in form.getlist("channel_ids")]
    targets = []
    for r in form.getlist("target"):
        if "|" in r:
            region, slug = r.split("|", 1)
            targets.append((canon_region(region), canon_slug(slug) if slug != "*" else "*"))

    if not name:
        flash(request, "请填写任务名称", "error")
        return RedirectResponse("/tasks", status_code=303)
    if not targets:
        flash(request, "任务至少需要一个监控目标", "error")
        return RedirectResponse("/tasks", status_code=303)
    if not event_types:
        event_types = list(DEFAULT_EVENTS)

    with Session(engine) as s:
        task = MonitorTask(name=name, enabled=True, interval_seconds=interval,
                           event_types=event_types, notify_policy="once",
                           auto_order=(form.get("auto_order") == "on"))
        s.add(task)
        s.commit()
        s.refresh(task)
        for region, slug in targets:
            s.add(TaskTarget(task_id=task.id, region=region, plan_slug=slug))
        for cid in channel_ids:
            s.add(TaskChannel(task_id=task.id, channel_id=cid))
        s.commit()
        s.refresh(task)
        sched.add_task_job(task)

    flash(request, f"任务「{name}」已创建并启动（{len(targets)} 个目标）。当前：{_create_status_text(targets)}", "success")
    return RedirectResponse("/tasks", status_code=303)


@router.get("/tasks/{tid}/edit")
def tasks_edit(tid: int, request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        task = s.get(MonitorTask, tid)
        if not task:
            flash(request, "任务不存在", "error")
            return RedirectResponse("/tasks", status_code=303)
        pairs = [(t.region, t.plan_slug) for t in
                 s.exec(select(TaskTarget).where(TaskTarget.task_id == tid)).all()]
        sel_ch = {tc.channel_id for tc in
                  s.exec(select(TaskChannel).where(TaskChannel.task_id == tid)).all()}
        channels = s.exec(select(Channel).order_by(Channel.id)).all()
    return render(request, "task_form.html", user=user, mode="edit", task=task,
                  targets=_targets_display(pairs), channels=channels,
                  selected_channels=sel_ch, event_options=EVENT_OPTIONS,
                  selected_events=set(task.event_types or []), default_interval=task.interval_seconds)


@router.post("/tasks/{tid}/update")
async def tasks_update(tid: int, request: Request, user=Depends(require_user)):
    form = await request.form()
    with Session(engine) as s:
        task = s.get(MonitorTask, tid)
        if not task:
            flash(request, "任务不存在", "error")
            return RedirectResponse("/tasks", status_code=303)
        task.name = (form.get("name") or task.name).strip()
        task.interval_seconds = int(form.get("interval_seconds") or task.interval_seconds)
        et = [e for e in form.getlist("event_types") if e in ev.ALL_EVENT_TYPES]
        task.event_types = et or list(DEFAULT_EVENTS)
        task.auto_order = form.get("auto_order") == "on"
        task.updated_at = utcnow()
        # 重设渠道
        for tc in s.exec(select(TaskChannel).where(TaskChannel.task_id == tid)).all():
            s.delete(tc)
        s.flush()  # 先落实删除，否则重新插入相同 (task_id, channel_id) 会触发唯一约束
        for cid in [int(x) for x in form.getlist("channel_ids")]:
            s.add(TaskChannel(task_id=tid, channel_id=cid))
        # 重设目标（保留已有目标的失效标记，避免编辑后重复推送失效提醒）
        missing = {}
        for tt in s.exec(select(TaskTarget).where(TaskTarget.task_id == tid)).all():
            missing[(tt.region, tt.plan_slug)] = tt.missing_since
            s.delete(tt)
        s.flush()
        for r in form.getlist("target"):
            if "|" in r:
                region, slug = r.split("|", 1)
                key = (canon_region(region), canon_slug(slug) if slug != "*" else "*")
                s.add(TaskTarget(task_id=tid, region=key[0], plan_slug=key[1],
                                 missing_since=missing.get(key)))
        s.add(task)
        s.commit()
        s.refresh(task)
        sched.reschedule_task(task)
    flash(request, "任务已更新", "success")
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{tid}/toggle")
async def tasks_toggle(tid: int, request: Request, user=Depends(require_user)):
    enabling = False
    with Session(engine) as s:
        task = s.get(MonitorTask, tid)
        if not task:
            flash(request, "任务不存在", "error")
            return RedirectResponse("/tasks", status_code=303)
        enabling = not task.enabled
        task.enabled = not task.enabled
        s.add(task)
        s.commit()
        s.refresh(task)
        sched.reschedule_task(task)
    if enabling:
        # 停用 → 启用：立即运行一次并推送「已恢复监控」当前状态报告
        result = await run_task(tid, force_status_push=True, status_kind="resume")
        statuses = result.get("target_status", [])
        if statuses:
            avail = sum(1 for st in statuses if st["available"])
            tg = "，已推送恢复报告到 Telegram" if result.get("status_pushed") else ""
            flash(request, f"任务已启用 · 当前 {avail}/{len(statuses)} 有货{tg}", "success")
        else:
            flash(request, "任务已启用", "success")
    else:
        flash(request, "任务已停用", "success")
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{tid}/delete")
def tasks_delete(tid: int, request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        task = s.get(MonitorTask, tid)
        if task:
            for tt in s.exec(select(TaskTarget).where(TaskTarget.task_id == tid)).all():
                s.delete(tt)
            for tc in s.exec(select(TaskChannel).where(TaskChannel.task_id == tid)).all():
                s.delete(tc)
            s.delete(task)
            s.commit()
            sched.remove_task_job(tid)
    flash(request, "任务已删除", "success")
    return RedirectResponse("/tasks", status_code=303)


def _probe_rows(s: Session, tid: int, result: dict) -> list[dict]:
    """探测结果：每个监控目标至少一行（整区目标展开为该区返回的全部套餐），
    并用刷新后的目录快照补全规格参数。state: ok / no / missing / failed / global。"""
    statuses = result.get("target_status", [])
    missing = {(m["region"], m["slug"]) for m in result.get("missing") or []}
    failed = {e.split(":", 1)[0] for e in result.get("errors") or []}

    def row(region, slug, state, name="", price=None):
        p = None if slug == "*" else s.exec(select(CatalogPlan).where(
            CatalogPlan.region == region, CatalogPlan.slug == slug)).first()
        return {"region": region, "slug": slug, "state": state,
                "name": (p.name if p else "") or name or slug,
                "category": p.category if p else "",
                "vcores": p.vcores if p else None, "memory": p.memory if p else None,
                "disk": p.disk if p else None, "transfer": p.transfer if p else None,
                "nvme": p.nvme if p else False,
                "price": p.price_monthly if p else price}

    rows, seen = [], set()
    for st in statuses:
        key = (st["region"], st["slug"])
        if key not in seen:
            seen.add(key)
            rows.append(row(*key, "ok" if st["available"] else "no", st.get("name", ""), st.get("price")))
    for t in s.exec(select(TaskTarget).where(TaskTarget.task_id == tid)).all():
        region = canon_region(t.region)
        slug = "*" if t.plan_slug == "*" else canon_slug(t.plan_slug)
        if t.region == "*":
            rows.append(row("*", slug, "global", "全站通配"))
        elif region in failed:
            if (region, slug) not in seen:
                seen.add((region, slug))
                rows.append(row(region, slug, "failed"))
        elif (region, slug) in missing:
            rows.append(row(region, slug, "missing"))
    order = {"ok": 0, "no": 1, "missing": 2, "failed": 3, "global": 4}
    rows.sort(key=lambda r: (order[r["state"]], r["region"], r["price"] or 0))
    return rows


@router.post("/tasks/{tid}/run")
async def tasks_run(tid: int, request: Request, user=Depends(require_user)):
    result = await run_task(tid, force_status_push=True)
    statuses = result.get("target_status", [])
    if request.headers.get("HX-Request"):
        # 列表页内联探测：返回结果表格片段，并带外更新任务状态行
        with Session(engine) as s:
            task = s.get(MonitorTask, tid)
            rows = _probe_rows(s, tid, result) if task and task.enabled else []
        return render(request, "_probe_result.html", user=user, task=task, rows=rows,
                      avail=sum(1 for r in rows if r["state"] == "ok"),
                      counted=sum(1 for r in rows if r["state"] in ("ok", "no")),
                      errors=result.get("errors") or [], hits=result.get("hits", 0),
                      pushed=result.get("status_pushed", False))
    if statuses:
        parts = [f"{s['region'].lower()}/{s['slug']}：{'有货' if s['available'] else '缺货'}"
                 for s in statuses[:6]]
        more = f" 等{len(statuses)}个" if len(statuses) > 6 else ""
        avail = sum(1 for s in statuses if s["available"])
        tg = "（已推送状态到 Telegram）" if result.get("status_pushed") else ""
        miss = f"；另有 {len(result['missing'])} 个目标未返回（可能已下架）" if result.get("missing") else ""
        flash(request, f"已运行 · {avail}/{len(statuses)} 有货 → " + "；".join(parts) + more + miss + tg, "success")
    else:
        flash(request, "已运行，但该任务暂无有效目标（可能目标未收录或区域抓取失败）", "info")
    return RedirectResponse("/tasks", status_code=303)
