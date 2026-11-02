"""生活续话与日常分享共用的联系节奏，依据真实收发回执计算。"""

from __future__ import annotations

import math

from ..clock import local_timestamp
from ..relationship import response_bias
from .sharepacing import merge
from .bonding import relationship_context


async def contact_rhythm(runtime, scope, now, *, shared=True):
    proactive = getattr(getattr(runtime, "config", None), "proactive", None)
    willingness = getattr(proactive, "talk_frequency", 0.35) if ":GroupMessage:" in scope else getattr(proactive, "private_talk_frequency", 0.55)
    if not isinstance(willingness, (int, float)) or not math.isfinite(willingness):
        willingness = 0.55
    result = {"willingness": round(max(0.0, min(1.0, willingness)), 2)}
    # 公开分享只传递角色的主动意愿，不暴露任何私聊的等待状态。
    if scope in {"global", "qzone_broadcast", "bilibili_broadcast"} or ":GroupMessage:" in scope:
        return await merge(runtime, scope, result) if shared and scope != "global" else result
    result["relationship_context"] = await relationship_context(getattr(runtime, "archive", None), scope)
    if len(result["relationship_context"]) == 1:
        bias = response_bias(result["relationship_context"][0]["state"])
        result["willingness"] = round(max(0.0, min(1.0, willingness + bias)), 3)
    reader = getattr(getattr(runtime, "archive", None), "get_conversation_contact_state", None)
    if not callable(reader):
        return await merge(runtime, scope, result) if shared else result
    state = await reader(scope, local_timestamp(now))
    receipts = state["receipts"]
    rounds = 0
    previous = None
    for stamp in receipts:
        # 连续分段、文字与媒体回执视为同一轮联系。
        if previous is None or stamp - previous > 120:
            rounds += 1
        previous = stamp
    cooldown = max(120, int(getattr(proactive, "private_cooldown_minutes", 8)) * 60)
    interval = min(7200, cooldown * 2 ** min(5, max(0, rounds - 1)))
    sent_at = state["last_sent_at"]
    remaining = max(0, math.ceil(sent_at + interval - local_timestamp(now))) if sent_at else 0
    result.update(
        ready=remaining == 0,
        retry_after=remaining,
        unanswered_contacts=rounds,
        last_received_at=state["last_received_at"],
        last_sent_at=sent_at,
    )
    return await merge(runtime, scope, result) if shared else result
