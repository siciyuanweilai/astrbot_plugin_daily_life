from .apply import ResponseGateApplyMixin
from .incoming import ResponseGateEventMixin
from .score import ResponseGateScoreMixin
from .state import ResponseGateStateMixin


class ResponseGateMixin(
    ResponseGateStateMixin,
    ResponseGateEventMixin,
    ResponseGateScoreMixin,
    ResponseGateApplyMixin,
):
    pass


__all__ = ["ResponseGateMixin"]
