"""事件类型常量与中文标签。"""
from __future__ import annotations

# 事件类型
RESTOCK = "restock"
SOLD_OUT = "sold_out"
PRICE_CHANGE = "price_change"
SPEC_CHANGE = "spec_change"
NEW_PLAN = "new_plan"
REMOVED_PLAN = "removed_plan"
NEW_REGION = "new_region"
REMOVED_REGION = "removed_region"
REGION_AVAILABILITY = "region_availability"
# 系统事件：同步时对监控目标的有效性检查（不可订阅，总是记录到监控事件）
TARGET_INVALID = "target_invalid"
TARGET_RESTORED = "target_restored"
SYSTEM_EVENTS = [TARGET_INVALID, TARGET_RESTORED]

# 可被任务订阅的事件类型（按层归类）
AVAILABILITY_EVENTS = [RESTOCK, SOLD_OUT]
CATALOG_EVENTS = [
    PRICE_CHANGE, SPEC_CHANGE, NEW_PLAN, REMOVED_PLAN,
    NEW_REGION, REMOVED_REGION, REGION_AVAILABILITY,
]
ALL_EVENT_TYPES = AVAILABILITY_EVENTS + CATALOG_EVENTS

LABELS: dict[str, str] = {
    RESTOCK: "补货",
    SOLD_OUT: "售罄",
    PRICE_CHANGE: "价格变更",
    SPEC_CHANGE: "规格变更",
    NEW_PLAN: "新规格上线",
    REMOVED_PLAN: "规格下架",
    NEW_REGION: "新区域上线",
    REMOVED_REGION: "区域下架",
    REGION_AVAILABILITY: "区域可用性变化",
    TARGET_INVALID: "目标失效",
    TARGET_RESTORED: "目标恢复",
}

# 日志分类（页面筛选用）
CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("stock", "库存", [RESTOCK, SOLD_OUT]),
    ("price_spec", "价格 / 规格", [PRICE_CHANGE, SPEC_CHANGE]),
    ("listing", "上下架", [NEW_PLAN, REMOVED_PLAN, NEW_REGION, REMOVED_REGION, REGION_AVAILABILITY,
                         TARGET_INVALID, TARGET_RESTORED]),
]
CATEGORY_TYPES: dict[str, list[str]] = {k: types for k, _, types in CATEGORIES}

# 日志来源：task = 监控任务产生（task_id 非空）；sync = 目录同步产生（task_id 为空）
# 各来源默认记录的事件类型（只影响日志记录，不影响通知推送）
DEFAULT_LOG_TYPES: dict[str, list[str]] = {
    "sync": CATALOG_EVENTS,
    "task": AVAILABILITY_EVENTS,
}

# （旧版）事件类型 -> 文件日志分项开关键，已由「按来源记录类型」取代，保留以兼容旧数据
# 事件类型 -> 对应的日志开关设置键
LOG_SETTING_KEY: dict[str, str] = {
    RESTOCK: "log_restock",
    SOLD_OUT: "log_sold_out",
    PRICE_CHANGE: "log_price_change",
    SPEC_CHANGE: "log_spec_change",
    NEW_PLAN: "log_new_plan",
    REMOVED_PLAN: "log_removed",
    NEW_REGION: "log_new_region",
    REMOVED_REGION: "log_removed",
    REGION_AVAILABILITY: "log_new_region",
}


def label(event_type: str) -> str:
    return LABELS.get(event_type, event_type)
