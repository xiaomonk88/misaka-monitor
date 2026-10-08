"""下单执行器选择：当前固定 Noop。未来可按设置切换真实实现。"""
from __future__ import annotations

from .base import OrderExecutor, OrderResult
from .noop import NoopOrderExecutor

_executor: OrderExecutor = NoopOrderExecutor()


def get_executor() -> OrderExecutor:
    return _executor


__all__ = ["OrderExecutor", "OrderResult", "get_executor"]
