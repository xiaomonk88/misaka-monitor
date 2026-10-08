"""通知渠道管理（多 Telegram）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..db import engine
from ..deps import require_user
from ..models import Channel
from ..templating import flash, render
from ..services.notifier import test_channel

router = APIRouter()


@router.get("/channels")
def channels_page(request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        channels = s.exec(select(Channel).order_by(Channel.id)).all()
    return render(request, "channels.html", user=user, channels=channels)


@router.post("/channels")
def channel_create(request: Request, user=Depends(require_user),
                   name: str = Form(...), bot_token: str = Form(""), chat_id: str = Form("")):
    with Session(engine) as s:
        s.add(Channel(type="telegram", name=name.strip(),
                      bot_token=bot_token.strip(), chat_id=chat_id.strip(), enabled=True))
        s.commit()
    flash(request, f"已添加渠道「{name}」", "success")
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/{cid}/update")
def channel_update(cid: int, request: Request, user=Depends(require_user),
                   name: str = Form(...), bot_token: str = Form(""), chat_id: str = Form(""),
                   enabled: str = Form("")):
    with Session(engine) as s:
        ch = s.get(Channel, cid)
        if ch:
            ch.name = name.strip()
            ch.chat_id = chat_id.strip()
            if bot_token.strip():  # 留空表示不修改 token
                ch.bot_token = bot_token.strip()
            ch.enabled = enabled == "on"
            s.add(ch)
            s.commit()
    flash(request, "已保存", "success")
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/{cid}/delete")
def channel_delete(cid: int, request: Request, user=Depends(require_user)):
    with Session(engine) as s:
        ch = s.get(Channel, cid)
        if ch:
            s.delete(ch)
            s.commit()
    flash(request, "已删除渠道", "success")
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/{cid}/test")
async def channel_test(cid: int, request: Request, user=Depends(require_user)):
    text = f"✅ Misaka 监控测试消息\n渠道配置正常。\n{datetime.now():%Y-%m-%d %H:%M:%S}"
    ok, err = await test_channel(cid, text)
    if ok:
        flash(request, "测试消息已发送，请检查 Telegram", "success")
    else:
        flash(request, f"测试失败：{err}", "error")
    return RedirectResponse("/channels", status_code=303)
