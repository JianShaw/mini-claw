"""Cron 表达式解析与调度时间计算。

支持标准 5 字段 cron 格式: 分 时 日 月 周
字段支持: *(任意), */N(步长), 1,2,3(枚举), 1-5(范围)

典型调用链:
    seconds_until_next_cron("*/5 * * * *")
    → cron_matches 逐分钟扫描，直到找到第一个匹配时刻
    → cron_field_matches 对每个字段独立判断
"""
from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from claw.scheduler.types import TaskResult


def import_callable(dotted_path: str) -> Callable[..., Awaitable[TaskResult]]:
    """根据 dotted path 动态导入并返回异步可调用对象。

    Args:
        dotted_path: 模块路径+函数名，如 "claw.scheduler.executor.run_task"

    Returns:
        该路径指向的异步函数对象（签名须为 async(...) -> TaskResult）

    Raises:
        ImportError: 模块不存在
        AttributeError: 模块中无指定函数
    """
    module_path, _, func_name = dotted_path.rpartition(".")
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    return func


def seconds_until_next_cron(expression: str) -> float:
    """计算从当前时刻到下一个 cron 匹配时间的秒数。

    从当前时间的下一个整分钟开始，逐分钟扫描，最多搜索一年（525960 分钟）。
    找到第一个匹配时刻即返回时间差（秒）；一年内未命中则返回 86400（一天）作为兜底。

    Args:
        expression: 标准 5 字段 cron 表达式，如 "0 15 * * *"（每天15:00）

    Returns:
        距下一个匹配时刻的秒数，兜底返回 86400.0
    """
    parts = expression.strip().split()
    now = datetime.now()
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(525960):
        if cron_matches(parts, candidate):
            return (candidate - now).total_seconds()
        candidate += timedelta(minutes=1)
    return 86400.0


def cron_field_matches(field: str, value: int) -> bool:
    """判断 cron 单个字段是否匹配给定的时间值。

    支持格式:
        "*"       → 任意值都匹配
        "*/N"     → 值能被 N 整除时匹配
        "1,3,5"   → 枚举中包含该值时匹配
        "1-5"     → 值在 [lo, hi] 闭区间内时匹配
    """
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    for item in field.split(","):
        if "-" in item:
            lo, hi = item.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        else:
            if value == int(item):
                return True
    return False


def cron_matches(parts: list[str], dt: datetime) -> bool:
    """判断 datetime 是否匹配 5 字段 cron 表达式（分 时 日 月 周）。

    将 dt 的 minute/hour/day/month/weekday 提取后，与 parts 逐字段调用
    cron_field_matches，全部匹配才返回 True。
    """
    values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
    return all(cron_field_matches(p, v) for p, v in zip(parts, values))
