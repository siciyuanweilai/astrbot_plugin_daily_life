from .breeze import AirEffectMixin, AirMeterMixin


class ProactiveAirMixin(
    AirMeterMixin,
    AirEffectMixin,
):
    pass


__all__ = ["ProactiveAirMixin"]
