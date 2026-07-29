from typing import Any

from .shade import HiddenExperienceMixin


class InjectVeilMixin(HiddenExperienceMixin):
    @staticmethod
    def _hidden_text(value: Any, limit: int = 120) -> str:
        return " ".join(str(value or "").strip().split())[:limit]
