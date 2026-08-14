from __future__ import annotations

import math
from typing import Any


def non_negative_number(value: Any) -> float | None:
    """把地图接口数值转换为有限的非负浮点数。

    Args:
        value: 地图接口返回的原始数值。

    Returns:
        合法的非负浮点数；无法转换时返回 ``None``。
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


__all__ = ["non_negative_number"]
