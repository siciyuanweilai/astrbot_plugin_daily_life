from .air import ProactiveAirMixin
from .decide import ProactiveJudgeMixin
from .gesture import ProactiveGestureMixin
from .procontext import ProactiveContextMixin
from .revisit import ProactiveRevisitMixin
from .scope import ProactiveScopeMixin
from .segment import ProactiveSegmentMixin
from .send import ProactiveSendMixin


class ProactiveMixin(
    ProactiveAirMixin,
    ProactiveContextMixin,
    ProactiveGestureMixin,
    ProactiveSegmentMixin,
    ProactiveScopeMixin,
    ProactiveSendMixin,
    ProactiveJudgeMixin,
    ProactiveRevisitMixin,
):
    pass


__all__ = ["ProactiveMixin"]
