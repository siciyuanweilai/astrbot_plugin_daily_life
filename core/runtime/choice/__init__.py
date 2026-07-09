from .apply import ResponseGateApplyMixin
from .incoming import ResponseGateEventMixin
from .score import ResponseGateScoreMixin
from .sense import ResponseGateSenseMixin
from .state import ResponseGateStateMixin


class ResponseGateMixin(
    ResponseGateStateMixin,
    ResponseGateSenseMixin,
    ResponseGateEventMixin,
    ResponseGateScoreMixin,
    ResponseGateApplyMixin,
):
    pass


__all__ = ["ResponseGateMixin"]
