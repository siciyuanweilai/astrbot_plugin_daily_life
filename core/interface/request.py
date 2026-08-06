import datetime
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CommandRequest:
    parts: list[str]
    action: str
    param1: str
    param2: str
    param_full: str
    now: datetime.datetime
    today_str: str
    yesterday_str: str
    period: str
    period_cn: str
    target_date_str: str
    commitment_target_date: str = ""


@dataclass(slots=True)
class DailyResetPlan:
    progress: str
    target_hour: int | None
    target_period: str
    extra_instruction: str | None
    keep_schedule: bool


CommandHandler = Callable[[Any, CommandRequest], AsyncIterator[Any]]
