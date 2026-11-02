"""浏览机会的事实版本与独立重评时钟，不从自然语言猜测意愿。"""
from __future__ import annotations

import hashlib
import json


def observation(world: dict, materials: list[dict], live=None) -> str:
    run = world.get("run") or {}
    facts = {
        "run": [(run.get("action") or {}).get("action_id"), run.get("status")],
        "body": {key: int(float(value) // 15)
                 for key, value in (world.get("body") or {}).items()
                 if key in {"energy", "fatigue", "sleep_pressure"}},
        # 观看进度增长不等于每分钟都产生一次新分享机会。
        "materials": sorted((str(row.get("id")), row.get("status", "")) for row in materials),
        "live": (live or {}).get("id") if isinstance(live, dict) else None,
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def due(state: dict, channel: str, context_key: str, now: float) -> bool:
    review = (state.get("reviews") or {}).get(channel) or {}
    # 旧版的一次 wait 只约束对外交流，不继承为所有账号活动的等待。
    deadline = review.get("after", state.get("review_after", 0) if channel == "interaction" else 0)
    return now >= float(deadline or 0) or bool(
        review and review.get("context_key") != context_key
        and now - float(review.get("at") or 0) >= 300
    )


def review_patch(state: dict, channel: str, decision: dict, *, now: float,
                 after: float, context_key: str) -> dict:
    reviews = dict(state.get("reviews") or {})
    reviews[channel] = {"at": now, "after": after, "context_key": context_key,
                        "action": decision.get("action", "wait"),
                        "reason": str(decision.get("reason") or "")[:200]}
    return {"reviews": reviews, "last_reason": reviews[channel]["reason"]}
