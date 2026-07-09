from .awareness import AwarenessMixin
from .deposit import ImprintDepositMixin
from .harvest import ImprintHarvestMixin
from .portrait import ImprintPortraitMixin
from .script import ImprintScriptMixin


class CaptureImprintMixin(
    AwarenessMixin,
    ImprintDepositMixin,
    ImprintPortraitMixin,
    ImprintScriptMixin,
    ImprintHarvestMixin,
):
    pass


__all__ = ["CaptureImprintMixin"]
