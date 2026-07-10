from .awareness import AwarenessMixin
from .deposit import ImprintDepositMixin
from .harvest import ImprintHarvestMixin
from .portrait import ImprintPortraitMixin


class CaptureImprintMixin(
    AwarenessMixin,
    ImprintDepositMixin,
    ImprintPortraitMixin,
    ImprintHarvestMixin,
):
    pass


__all__ = ["CaptureImprintMixin"]
