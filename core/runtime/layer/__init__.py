from .chain import LayerChainMixin
from .text import LayerTextMixin
from .prefix import RequestPrefixMixin


class LayerMixin(
    LayerTextMixin,
    LayerChainMixin,
    RequestPrefixMixin,
):
    pass


__all__ = ["LayerMixin"]
