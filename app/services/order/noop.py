"""Noop 下单执行器：仅记录「将会下单」，不执行任何真实操作。"""
from __future__ import annotations

import logging
from typing import Any

from .base import OrderExecutor, OrderResult

_logger = logging.getLogger("misaka.order")


class NoopOrderExecutor(OrderExecutor):
    async def place_order(self, *, task_id: int, task_name: str, region: str, slug: str,
                          plan_snapshot: dict[str, Any], order_params: dict[str, Any]) -> OrderResult:
        _logger.info("[auto-order:noop] task=%s %s/%s 将下单（预留，未执行）", task_id, region, slug)
        return OrderResult(ok=False, message="自动下单为预留功能，尚未启用（已记录意图）")
