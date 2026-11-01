"""邀约的持久状态回执；回复和重放共用实际保存结果。"""
from __future__ import annotations

import json

from ...life.actionchain import actions, dependency_issue, execution_evidence
from ...outcome import ToolResultText


def invite_receipt(commitment, day, *, decision=None, reason="") -> ToolResultText:
    decision = decision or {}
    planned = [item for item in actions(day)
               if item.payload.get("schedule_commitment_id") == commitment.id] if day else []
    indexes = sorted({item.timeline_index for item in planned
                      if type(item.timeline_index) is int and 0 <= item.timeline_index < len(day.timeline)})
    scheduled = commitment.status == "scheduled"
    active = commitment.status in {"active", "scheduled"}
    waiting = [{"action_id": item.action_id, "target": item.target,
                "reason": dependency_issue(day, item)} for item in planned if dependency_issue(day, item)]
    fallback = ("约定和日程已经记下，实际行动还要按准备进度推进。" if scheduled
                else "约定已记下，日程还没有落实。" if active
                else "这项约定已经结束或取消，没有重新安排。")
    payload = {
        "decision": "accept" if active else commitment.status,
        "person": "、".join(commitment.people),
        "activity": commitment.content,
        "reason": reason,
        "commitment_id": commitment.id,
        "commitment_status": commitment.status,
        "schedule_saved": scheduled,
        "schedule": [day.timeline[index].as_dict() for index in indexes],
        "waiting": waiting,
        "execution": execution_evidence(day) if day else {},
        "response_stance": str(decision.get("response_stance") or "依据实际排期和准备进度自然确认"),
        "response_tone": str(decision.get("response_tone") or "符合双方关系和当前语境"),
    }
    return ToolResultText(json.dumps(payload, ensure_ascii=False), status="scheduled" if scheduled else commitment.status,
                          facts={**payload, "fallback_text": fallback})
