from .dock import StructuredDockMixin
from .forge import StructuredForgeMixin
from .glance import StructuredGlanceMixin
from .grain import StructuredBaseMixin, StructuredMessage, StructuredTarget
from .route import StructuredRouteMixin


class StructuredContextMixin(
    StructuredBaseMixin,
    StructuredDockMixin,
    StructuredRouteMixin,
    StructuredForgeMixin,
    StructuredGlanceMixin,
):
    pass


__all__ = ["StructuredContextMixin", "StructuredMessage", "StructuredTarget"]
