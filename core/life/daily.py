from .assembly import DailyAssemblyMixin
from .briefing import DailyBriefingMixin
from .climate import DailyClimateMixin
from .contract import DailyContractMixin
from .draft import DailyDraftMixin
from .engine import DailyEngineMixin
from .record import DailyRecordMixin


class DailyMixin(
    DailyContractMixin,
    DailyDraftMixin,
    DailyBriefingMixin,
    DailyClimateMixin,
    DailyAssemblyMixin,
    DailyRecordMixin,
    DailyEngineMixin,
):
    pass


__all__ = ["DailyMixin"]
