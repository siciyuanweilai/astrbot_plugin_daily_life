from .parse import HistoryParseMixin
from .scene import HistorySceneMixin
from .watch import HistoryWatchMixin
from .write import HistoryWriteMixin


class TrailMixin(
    HistoryParseMixin,
    HistorySceneMixin,
    HistoryWatchMixin,
    HistoryWriteMixin,
):
    pass


__all__ = ["TrailMixin"]
