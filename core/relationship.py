"""有证据的长期关系；语义由模型判断，数值与来源由程序约束。"""

from __future__ import annotations

import math
from typing import Any

DIMENSIONS = ("familiarity", "trust", "affinity")
BASELINE = {"familiarity": 0.0, "trust": 0.5, "affinity": 0.5}
LABELS = {"familiarity": "熟悉", "trust": "信任", "affinity": "好感"}
MAX_CHANGE = 0.08

# 一份跨聊天、回访和分享共用的语义契约，不按场景或分数编排台词。
RELATIONSHIP_RULES = (
    "关系记录是自己的长期主观了解，当前心情独立于长期关系。"
    "所有维度都以聊天中的assistant为感受主体、profile_id对应的用户为对象："
    "熟悉表示我对这个人的了解，信任表示我对这个人可靠性与尊重边界的信心，好感表示我对这个人的亲近意愿。"
    "用户对我的评价属于对方的反馈，不能反向当作我对其可靠性的判断；分别理解，允许不变。"
    "变化必须来自目标用户参与的真实互动，引用其消息编号并说明经历；"
    "自己的发言、发送成功、继续聊天、等待或暂未回复都不能单独证明用户态度。"
    "同一经历的重述沿用已有 change_id，不重新计分；纠正旧判断时填写 supersedes_change_id。"
    "纠正使用 correction_mode：retract 撤销错误归属或无效经历，由程序恢复旧影响；"
    "replace 以重新理解的同一经历替换旧判断，delta填写该经历本身的完整影响，不填写抵消旧分数的差额。"
    "关系和回忆帮助选择分享对象、渠道、表达深度与时机，不产生固定台词或亲密权限；"
    "相处偏好、公开边界、当前生活和联系节奏仍须共同参与决定。"
)

ASSESSMENT_SCHEMA = {
    "profile_id": "本批次真实用户编号",
    "source_message_ids": ["该用户参与本次经历的真实消息编号"],
    "familiarity_delta": "-0.08到0.08之间的数值，无变化为0",
    "trust_delta": "-0.08到0.08之间的数值，无变化为0",
    "affinity_delta": "-0.08到0.08之间的数值，无变化为0",
    "confidence": 0.0,
    "reason": "具体经历怎样改变了自己的了解；无变化可不输出",
    "change_id": "新经历必须为0；重述只能填写输入recent_changes中已有的id",
    "supersedes_change_id": "纠正只能填写输入recent_changes中已有的id；其他情况为0",
    "correction_mode": "纠正时为retract（撤销）或replace（替换）；其他情况为空字符串",
}


def finite_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def relationship_summary(value: Any, *, include_reason: bool = False) -> str:
    """未知维度不伪装成已有好感；关系描述不泄露原始证据。"""
    state = value if isinstance(value, dict) else {}
    known = state.get("observed_dimensions", [])
    if not known:
        return "长期关系尚无足够证据，沿用已知相处偏好自然交流"
    parts = [f"{LABELS[key]} {finite_number(state.get(key), BASELINE[key]):.2f}"
             for key in DIMENSIONS if key in known]
    result = "；".join(parts) + "（独立长期维度，范围 0–1，不代表当前心情或行为许可）"
    if include_reason and state.get("last_reason"):
        result += "；最近变化：" + str(state["last_reason"])[:240]
    return result


def response_bias(value: Any) -> float:
    """小幅连续调整普通接话意愿；不覆盖定向呼叫与硬性打断边界。"""
    state = value if isinstance(value, dict) else {}
    known = state.get("observed_dimensions", [])
    weights = {"familiarity": 0.04, "trust": 0.04, "affinity": 0.06}
    return sum(weights[key] * (finite_number(state.get(key), BASELINE[key]) - BASELINE[key])
               for key in DIMENSIONS if key in known)


def normalize_assessment(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    profile_id = str(raw.get("profile_id") or "").strip()
    reason = str(raw.get("reason") or "").strip()[:800]
    refs = raw.get("source_message_ids")
    if not profile_id or not reason or not isinstance(refs, list) or not refs:
        return None
    supersedes = max(0, int(finite_number(raw.get("supersedes_change_id"))))
    change_id = max(0, int(finite_number(raw.get("change_id"))))
    correction_mode = str(raw.get("correction_mode") or "retract") if supersedes else ""
    if supersedes and (change_id or correction_mode not in {"retract", "replace"}):
        return None
    return {
        "profile_id": profile_id, "reason": reason,
        "source_message_ids": list(dict.fromkeys(str(x).strip() for x in refs if str(x).strip()))[:12],
        "confidence": max(0.0, min(1.0, finite_number(raw.get("confidence")))),
        "deltas": {key: 0.0 if correction_mode == "retract" else max(-MAX_CHANGE, min(MAX_CHANGE, finite_number(raw.get(key + "_delta"))))
                   for key in DIMENSIONS},
        "change_id": change_id,
        "supersedes_change_id": supersedes,
        "correction_mode": correction_mode,
    }
