from .acquaintances import AcquaintanceArchiveMixin, _pack_tags, _unpack_tags
from .longterm import LongTermMemoryArchiveMixin
from .perception import PerceptionArchiveMixin
from .places import PlaceArchiveMixin
from .summaries import SummaryArchiveMixin


class MemoryArchiveMixin(
    PlaceArchiveMixin,
    AcquaintanceArchiveMixin,
    SummaryArchiveMixin,
    PerceptionArchiveMixin,
    LongTermMemoryArchiveMixin,
):
    pass


__all__ = ["MemoryArchiveMixin", "_pack_tags", "_unpack_tags"]
