"""Telegram Bot 推送。"""
from __future__ import annotations

import httpx


async def send_telegram(token: str, chat_id: str, text: str, timeout: float = 20.0) -> tuple[bool, str]:
    """返回 (是否成功, 错误信息)。"""
    if not token or not chat_id:
        return False, "未配置 bot_token 或 chat_id"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("ok"):
            return True, ""
        return False, f"HTTP {r.status_code}: {data.get('description', r.text[:200])}"
    except httpx.HTTPError as e:
        return False, f"网络错误：{e}"
