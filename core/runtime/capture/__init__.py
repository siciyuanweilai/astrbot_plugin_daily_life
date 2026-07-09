from .emoji import EmojiAssetMixin
from .event import CaptureEventMixin
from .imprint import CaptureImprintMixin
from .payload import CapturePayloadMixin
from .pledge import CapturePledgeMixin
from .relation import CaptureRelationMixin


class CaptureMixin(
    CaptureEventMixin,
    CapturePayloadMixin,
    CaptureRelationMixin,
    CaptureImprintMixin,
    CapturePledgeMixin,
    EmojiAssetMixin,
):
    pass


__all__ = ["CaptureMixin"]
