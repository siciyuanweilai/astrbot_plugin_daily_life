import datetime
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable


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


@dataclass(slots=True)
class DailyResetPlan:
    progress: str
    target_hour: int | None
    target_period: str
    extra_instruction: str | None
    keep_schedule: bool


CommandHandler = Callable[[Any, CommandRequest], AsyncIterator[Any]]
