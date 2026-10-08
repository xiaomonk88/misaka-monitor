"""Misaka 公开 API 异步客户端。

数据源（无需认证）：
  GET /api/mc2/regions                     -> 区域列表（含区域级 available）
  GET /api/mc2/regions/{region}/plans      -> 该区域套餐（含 available 库存标志）
响应实时无缓存。需带浏览器 UA，否则根路径可能 403。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import settings

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}

# 同区域请求的短期缓存，避免多个任务同时打同一接口
_PLANS_CACHE_TTL = 4.0  # 秒
_plans_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class MisakaClient:
    def __init__(self, timeout: float = 20.0):
        self.base = settings.misaka_api_base
        self.timeout = timeout

    async def _get_json(self, path: str) -> Any:
        url = f"{self.base}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=_HEADERS) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    async def fetch_regions(self) -> list[dict[str, Any]]:
        data = await self._get_json("/mc2/regions")
        return data if isinstance(data, list) else []

    async def fetch_region_plans(self, region: str, use_cache: bool = True) -> list[dict[str, Any]]:
        now = time.monotonic()
        if use_cache:
            hit = _plans_cache.get(region)
            if hit and now - hit[0] < _PLANS_CACHE_TTL:
                return hit[1]
        data = await self._get_json(f"/mc2/regions/{region}/plans")
        plans = data if isinstance(data, list) else []
        _plans_cache[region] = (now, plans)
        return plans

    async def fetch_all(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """全量抓取：返回 (regions, {region_slug: [plans]})。供 24h 目录同步使用。"""
        regions = await self.fetch_regions()
        plans_by_region: dict[str, list[dict[str, Any]]] = {}
        for r in regions:
            slug = r.get("slug")
            if not slug:
                continue
            try:
                plans_by_region[slug] = await self.fetch_region_plans(slug, use_cache=False)
            except httpx.HTTPError:
                plans_by_region[slug] = []
        return regions, plans_by_region


client = MisakaClient()
