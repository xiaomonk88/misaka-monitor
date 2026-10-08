"""通用工具：区域/规格 slug 规范化与下单链接。

经验规律（来自 Misaka API）：区域 slug 为大写（MOW01），套餐 slug 为小写（s2n-1c1g）。
对外下单 URL 用小写区域（与站点 URL 一致，API 大小写不敏感）。
"""
from __future__ import annotations


def canon_region(region: str) -> str:
    return (region or "").strip().upper()


def canon_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def order_url(region: str, slug: str) -> str:
    return f"https://app.misaka.io/iaas/vm/create/{(region or '').lower()}/{(slug or '').lower()}"


# 大洲英文 -> 中文
CONTINENT_CN = {
    "Asia": "亚洲", "Europe": "欧洲", "North America": "北美",
    "South America": "南美", "Africa": "非洲", "Oceania": "大洋洲", "": "其他",
}


def continent_cn(name: str) -> str:
    return CONTINENT_CN.get(name or "", name or "其他")


def fmt_price(p) -> str:
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        return "-"
    return f"${v:.0f}/月" if abs(v - round(v)) < 1e-9 else f"${v:.2f}/月"


def fmt_mem(mib) -> str:
    mib = int(mib or 0)
    if not mib:
        return "-"
    if mib < 1024:
        return f"{mib} MB"
    g = mib / 1024
    return f"{g:.0f} GB" if abs(g - round(g)) < 1e-9 else f"{g:.1f} GB"


def fmt_gb(mib) -> str:
    mib = int(mib or 0)
    if not mib:
        return "-"
    g = mib / 1024
    return f"{g:.0f} GB" if abs(g - round(g)) < 1e-9 else f"{g:.1f} GB"


def fmt_transfer(gb) -> str:
    """月流量（API 单位 GB）：不足 1 TB 显示 GB，否则显示 TB。"""
    gb = int(gb or 0)
    if not gb:
        return "-"
    if gb < 1024:
        return f"{gb} GB"
    t = gb / 1024
    return f"{t:.0f} TB" if abs(t - round(t)) < 0.05 else f"{t:.1f} TB"


def fmt_specs(vcores, memory, disk, transfer) -> str:
    """紧凑规格串：1C / 1 GB / 16 GB / 流量 1 TB"""
    parts = []
    if vcores:
        parts.append(f"{int(vcores)}C")
    for v, f in ((memory, fmt_mem), (disk, fmt_gb)):
        if v:
            parts.append(f(v))
    if transfer:
        parts.append(f"流量 {fmt_transfer(transfer)}")
    return " / ".join(parts)
