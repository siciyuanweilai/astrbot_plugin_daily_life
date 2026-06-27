from .anchor import EmojiBaseMixin
from .cache import EmojiCacheMixin
from .gather import EmojiGatherMixin
from .sweep import EmojiSweepMixin
from .vision import EmojiVisionMixin


class EmojiAssetMixin(
    EmojiBaseMixin,
    EmojiCacheMixin,
    EmojiSweepMixin,
    EmojiVisionMixin,
    EmojiGatherMixin,
):
    pass


__all__ = ["EmojiAssetMixin"]
