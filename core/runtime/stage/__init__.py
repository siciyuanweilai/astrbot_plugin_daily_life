from .album import StageAlbumMixin
from .frame import StageFrameMixin
from .lens import StageLensMixin
from .reel import StageReelMixin
from .speech import StageVoiceMixin


class RuntimeMediaDirectorMixin(
    StageFrameMixin,
    StageLensMixin,
    StageReelMixin,
    StageVoiceMixin,
    StageAlbumMixin,
):
    pass


__all__ = ["RuntimeMediaDirectorMixin"]
