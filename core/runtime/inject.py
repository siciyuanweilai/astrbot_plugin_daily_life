from ..clock import now as life_now
from .expression import ExpressionHintMixin
from .layer import LayerMixin
from .snapshot import SnapshotMixin
from .veil import InjectVeilMixin


class InjectMixin(SnapshotMixin, ExpressionHintMixin, InjectVeilMixin, LayerMixin):
    def _life_injection_now(self):
        return life_now()
