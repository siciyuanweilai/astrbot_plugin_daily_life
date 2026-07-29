from .tune import PacketMixin, ProfileMixin, TargetMixin


class CaptureCalibrationMixin(
    PacketMixin,
    TargetMixin,
    ProfileMixin,
):
    pass


__all__ = ["CaptureCalibrationMixin"]
