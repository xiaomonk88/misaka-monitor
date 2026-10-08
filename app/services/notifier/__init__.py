"""通知扇出：把一条消息发送到指定的多个渠道，并记录结果。"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from ...db import engine
from ...models import Channel, NotificationLog
from .telegram import send_telegram


async def _send_one(ch: Channel, text: str) -> tuple[bool, str]:
    if ch.type == "telegram":
        return await send_telegram(ch.bot_token, ch.chat_id, text)
    return False, f"不支持的渠道类型：{ch.type}"


async def notify_channels(channel_ids: list[int], text: str, *,
                          task_id: Optional[int] = None,
                          event_id: Optional[int] = None) -> list[tuple[int, bool, str]]:
    """向 channel_ids 中所有启用的渠道发送 text，写入 NotificationLog。"""
    with Session(engine) as s:
        channels = [s.get(Channel, cid) for cid in channel_ids]

    results: list[tuple[int, bool, str]] = []
    for ch in channels:
        if not ch or not ch.enabled:
            continue
        ok, err = await _send_one(ch, text)
        with Session(engine) as s:
            s.add(NotificationLog(channel_id=ch.id, channel_name=ch.name, task_id=task_id,
                                  event_id=event_id, status="ok" if ok else "failed", error=err))
            s.commit()
        results.append((ch.id, ok, err))
    return results


async def test_channel(channel_id: int, text: str) -> tuple[bool, str]:
    with Session(engine) as s:
        ch = s.get(Channel, channel_id)
    if not ch:
        return False, "渠道不存在"
    ok, err = await _send_one(ch, text)
    with Session(engine) as s:
        s.add(NotificationLog(channel_id=ch.id, channel_name=ch.name, status="ok" if ok else "failed",
                              error=err or "（测试）"))
        s.commit()
    return ok, err
