from .emoji import EmojiAssetMixin
from .batch import ChatMemoryBatchMixin
from .event import CaptureEventMixin
from .imprint import CaptureImprintMixin
from .payload import CapturePayloadMixin
from .relation import CaptureRelationMixin


class CaptureMixin(
    ChatMemoryBatchMixin,
    CaptureEventMixin,
    CapturePayloadMixin,
    CaptureRelationMixin,
    CaptureImprintMixin,
    EmojiAssetMixin,
):
    pass


__all__ = ["CaptureMixin"]
