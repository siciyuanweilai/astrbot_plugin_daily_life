from .album import StageAlbumMixin
from .frame import StageFrameMixin
from .lens import StageLensMixin
from .reel import StageReelMixin
from .speech import StageVoiceMixin
from .visual import StageVisualMixin


class RuntimeMediaDirectorMixin(
    StageFrameMixin,
    StageLensMixin,
    StageVisualMixin,
    StageReelMixin,
    StageVoiceMixin,
    StageAlbumMixin,
):
    pass


__all__ = ["RuntimeMediaDirectorMixin"]
