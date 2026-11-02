"""主动续话与私聊回访共用的决策、表达协议。"""
from __future__ import annotations

import json

from ...prompts import CORE_PROACTIVE_CONTINUITY_RULES, CORE_PROACTIVE_VOICE_RULES


def proactive_decision_contract(*, include_target: bool = False, include_call: bool = False) -> str:
    schema = {
        "should_reply": False, "confidence": 0.0,
        "benefit": 0, "timeliness": 0, "continuity": 0, "disruption": 0, "uncertainty": 0,
        "decision": "reply|observe|wait|skip", "reason": "场景与判断依据",
        "reply_text": "适合开口时的完整自然表达，否则为空",
        "expression_review": {"passed": True, "risk": "", "suggestion": "", "reason": "本轮语境与已学习习惯的检查依据"},
        "expression_intent": {
            "channel": "text|voice", "confidence": 0.0, "emotion": "自然情绪或空",
            "emotion_category": "neutral|happy|sad|angry", "voice_style": "neutral|happy|light|soft|sad|angry",
            "emoji_intent": "", "action_intent": "", "send_emoji": False, "reason": "",
        },
    }
    if include_target:
        schema.update(target_message_id="承接的输入消息编号或空", target_topic="承接的话题或空")
    if include_call:
        schema["voice_call_intent"] = {"should_invite": False, "greeting": "接通后的自然开场或空", "reason": "邀请依据"}
    rules = """裁定方式：
- 根据本轮候选和关系、生活状态作决定。benefit、timeliness、continuity、disruption、uncertainty 各填 0 至 100 的整数，前三项是收益，后两项是风险；收益高于风险且确实想开口才 should_reply=true，否则 observe、wait 或 skip。
- reply_text 接住眼前话题，必要的意思说完整，口吻遵循角色和本轮表达设置。在同一次生成中复核话题承接、重复关心或追问、关系分寸和适用表达习惯；发现问题先修改，再填写 expression_review。自然与否不按关键词、句型或字数判断；仍不合适则 passed=false 并 observe。
- voice_style 根据整轮语义直接选择枚举值，不从 emotion 文本推导；无明显情绪用 neutral。
- reason 只写相对场景和判断依据，不复述具体日期、钟点或时间轴编号；具体时间保留在内部证据中。
- 近期消息按明确时间理解；当前地点、动作及完成状态必须有当前生活事实支持。旧资料和未来计划不能当作刚发生。
- 可见文字只写 reply_text。表情由执行层在文字送达后按素材独立尝试；不能声称本轮已经附带或补发尚未交付的媒体。
- 根据已核对的事项进度选择表达。需要交付文字结果时，reply_text 直接包含完整结果并保留必要格式；后续动作只能依托当前真实执行任务，不能用承诺代替执行。等待反馈不等于仍未处理，角色仍可自然分享新的生活经历。
- 只输出协议字段，不添加内部过程或发送控制字段。"""
    if include_call:
        rules += """
- voice_call_intent 默认关闭。仅私聊且通话能力可用、关系与打扰风险允许、双方未同处现场，并且角色确实想听对方声音时考虑邀请；近期已邀请则保持关闭。reply_text 自然表达通话意愿，执行层负责邀请链接；greeting 按本次语境填写，可留空。"""
    return "只输出 JSON：\n" + json.dumps(schema, ensure_ascii=False) + "\n" + rules + "\n" + CORE_PROACTIVE_VOICE_RULES + "\n" + CORE_PROACTIVE_CONTINUITY_RULES
