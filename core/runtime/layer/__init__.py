from .chain import LayerChainMixin
from .text import LayerTextMixin


class LayerMixin(
    LayerTextMixin,
    LayerChainMixin,
):
    pass


__all__ = ["LayerMixin"]
