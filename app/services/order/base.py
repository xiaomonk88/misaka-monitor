"""自动下单执行器抽象接口（预留）。

当前仅 Noop 实现。未来真正落地需要：
  1) 账号登录态（Bearer Token，登录路由已存在）；
  2) 下单 POST 接口与必填参数（镜像/SSH Key/计费周期/数量/区域）；
  3) 付款（Stripe）或账户余额——有余额可免交互下单。
凭据将加密存储；属自动化下单，需自担服务商 ToS 风险。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class OrderResult:
    ok: bool
    message: str
    detail: Optional[dict[str, Any]] = None


class OrderExecutor(ABC):
    @abstractmethod
    async def place_order(self, *, task_id: int, task_name: str, region: str, slug: str,
                          plan_snapshot: dict[str, Any], order_params: dict[str, Any]) -> OrderResult:
        ...
