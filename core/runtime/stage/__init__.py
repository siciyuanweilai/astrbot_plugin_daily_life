from .album import StageAlbumMixin
from .frame import StageFrameMixin
from .figure import StageFigureMixin
from .lens import StageLensMixin
from .reel import StageReelMixin
from .speech import StageVoiceMixin


class RuntimeMediaDirectorMixin(
    StageFrameMixin,
    StageFigureMixin,
    StageLensMixin,
    StageReelMixin,
    StageVoiceMixin,
    StageAlbumMixin,
):
    pass


__all__ = ["RuntimeMediaDirectorMixin"]
