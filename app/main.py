"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, col, func, select
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import settings
from .db import engine, init_db
from .deps import NotAuthenticated, PasswordChangeRequired, require_user
from .models import CatalogPlan, Channel, Event, MonitorTask
from .services import events as ev
from .services import scheduler as sched
from .templating import render
from .api import auth as auth_api
from .api import catalog as catalog_api
from .api import channels as channels_api
from .api import logs as logs_api
from .api import settings as settings_api
from .api import tasks as tasks_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched.start()
    sched.setup_jobs()
    try:
        yield
    finally:
        sched.shutdown()


app = FastAPI(title="Misaka 监控", version=__version__, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=14 * 24 * 3600)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")

app.include_router(auth_api.router)
app.include_router(catalog_api.router)
app.include_router(channels_api.router)
app.include_router(tasks_api.router)
app.include_router(logs_api.router)
app.include_router(settings_api.router)


@app.exception_handler(NotAuthenticated)
async def _redirect_login(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def _redirect_setup(request: Request, exc: PasswordChangeRequired):
    return RedirectResponse("/account/setup", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__, "scheduler": sched.scheduler.running}


@app.get("/")
def dashboard(request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        stats = {
            "tasks": s.exec(select(func.count()).select_from(MonitorTask)).one(),
            "tasks_on": s.exec(select(func.count()).select_from(MonitorTask).where(MonitorTask.enabled == True)).one(),  # noqa: E712
            "channels": s.exec(select(func.count()).select_from(Channel)).one(),
            "plans": s.exec(select(func.count()).select_from(CatalogPlan)).one(),
            "plans_avail": s.exec(select(func.count()).select_from(CatalogPlan).where(CatalogPlan.available == True)).one(),  # noqa: E712
            "events": s.exec(select(func.count()).select_from(Event).where(col(Event.task_id).is_not(None))).one(),
            "sync_events": s.exec(select(func.count()).select_from(Event).where(col(Event.task_id).is_(None))).one(),
        }
        recent = s.exec(select(Event).where(col(Event.task_id).is_not(None))
                        .order_by(Event.ts.desc()).limit(10)).all()
        task_names = {t.id: t.name for t in s.exec(select(MonitorTask)).all()}
    return render(request, "dashboard.html", user=user, stats=stats, recent=recent,
                  label=ev.label, last_sync=catalog_api._last_sync(), task_names=task_names)
