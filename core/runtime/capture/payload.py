from typing import Any


class CapturePayloadMixin:
    def _relationship_contact_payload(self, meta: dict[str, str]) -> dict[str, Any]:
        platform = meta.get("platform", "")
        user_id = meta.get("user_id", "")
        if meta.get("is_group") == "true":
            return {
                "contact_type": "group_member",
                "target_scope": "",
                "group_id": meta.get("group_id", ""),
                "group_name": meta.get("group_name", ""),
                "is_reachable": True,
                "blocked_reason": "",
            }
        target_scope = meta.get("session_id", "")
        if not target_scope and platform and user_id:
            target_scope = f"{platform}:FriendMessage:{user_id}"
        return {
            "contact_type": "friend",
            "target_scope": target_scope,
            "group_id": "",
            "group_name": "",
            "is_reachable": True,
            "blocked_reason": "",
        }

    @staticmethod
    def _list_payload(value: Any) -> list:
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _str_payload(value: Any, default: str = "") -> str:
        return str(value if value is not None else default).strip()

    @staticmethod
    def _int_payload(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_payload(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool_payload(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        return text in {"true", "yes", "y", "1", "是"}

    @classmethod
    def _score_payload(cls, value: Any, default: int = 0) -> int:
        return max(0, min(cls._int_payload(value, default), 100))

    @staticmethod
    def _compact_log(value: Any, limit: int = 80) -> str:
        text = str(value or "").strip()
        text = " ".join(text.split())
        return text[:limit]
