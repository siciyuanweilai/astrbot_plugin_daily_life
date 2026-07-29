from __future__ import annotations

from typing import Any


class VoiceSwitchJudgeMixin:
    _VOICE_SWITCH_MIN_NATURAL_SCORE = 0.58

    def _judge_voice_switch_channel(
        self, event: Any, reply_text: str
    ) -> dict[str, Any]:
        text = str(reply_text or "").strip()
        if not text:
            return self._voice_switch_text_decision(
                "我还没整理出要说的话，先不发语音。", 0.0
            )

        structural_block = self._voice_switch_structural_text_reason(text)
        if structural_block:
            return self._voice_switch_text_decision(structural_block, 0.96)

        plan_getter = getattr(self, "_semantic_expression_plan_from_event", None)
        plan = plan_getter(event) if callable(plan_getter) else None
        if plan is None or str(getattr(plan, "channel", "text")) != "voice":
            return self._voice_switch_text_decision(
                str(getattr(plan, "reason", "") or "这轮没有明确的语音表达需要，保留文字更自然。"),
                float(getattr(plan, "confidence", 0.0) or 0.0),
            )
        score = max(0.0, min(float(getattr(plan, "confidence", 0.0) or 0.0), 1.0))
        if score < self._VOICE_SWITCH_MIN_NATURAL_SCORE:
            return self._voice_switch_text_decision(
                "语音表达倾向不够明确，保留文字发送。", score
            )
        emotion_category = str(getattr(plan, "emotion_category", "neutral") or "neutral")
        emotion = str(getattr(plan, "emotion", "") or "平常口吻")
        reason = str(getattr(plan, "reason", "") or "整轮语义更适合直接说出来。")
        return {
            "channel": "voice",
            "reason": reason,
            "emotion": emotion,
            "emotion_category": emotion_category,
            "confidence": round(score, 2),
        }

    @staticmethod
    def _voice_switch_text_decision(reason: str, confidence: float) -> dict[str, Any]:
        return {
            "channel": "text",
            "reason": reason,
            "emotion": "",
            "emotion_category": "",
            "confidence": round(max(0.0, min(float(confidence or 0.0), 1.0)), 2),
        }

    def _voice_switch_structural_text_reason(self, text: str) -> str:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        normalized = str(text or "").strip()
        if "```" in normalized or "`" in normalized:
            return "我这轮带着代码或命令痕迹，打字留给对方看更稳。"
        if "://" in normalized or normalized.startswith("www."):
            return "我这轮带了链接，直接发文字更方便对方点开。"
        if self._voice_switch_has_path_shape(normalized):
            return "我这轮带了路径或位置写法，文字更不容易听错。"
        if len(normalized) > self._VOICE_SWITCH_LONG_TEXT_LIMIT:
            return "我这轮内容偏长，打字留下来更清楚。"
        if len(lines) > self._VOICE_SWITCH_MAX_SHORT_LINES:
            return "我这轮分了好几行，打字更方便对方回看。"
        if any(len(line) > self._VOICE_SWITCH_LONG_LINE_LIMIT for line in lines):
            return "我这轮单句太长，说出来容易散，文字更清楚。"
        if self._voice_switch_has_list_shape(lines):
            return "我这轮像是在列步骤或清单，文字更合适。"
        if self._voice_switch_has_dense_foreign_shape(normalized):
            return "我这轮有不少英文名词或参数，打字更不容易误会。"
        if self._voice_switch_has_dense_number_shape(normalized):
            return "我这轮数字信息偏多，文字更方便核对。"
        if self._voice_switch_text_too_dense_for_voice(normalized):
            return "我这轮信息密度有点高，打字更稳。"
        return ""

    @staticmethod
    def _voice_switch_has_path_shape(text: str) -> bool:
        separators = text.count("/") + text.count("\\")
        return ":" in text and separators >= 1 or separators >= 3

    @staticmethod
    def _voice_switch_has_list_shape(lines: list[str]) -> bool:
        if len(lines) < 2:
            return False
        starters = 0
        for line in lines:
            head = line[:3].strip()
            if not head:
                continue
            first = head[0]
            if first in {"-", "*", "+", "1", "2", "3", "4", "5"}:
                starters += 1
        return starters >= 2

    @staticmethod
    def _voice_switch_has_dense_foreign_shape(text: str) -> bool:
        ascii_runs = 0
        current = 0
        for char in text:
            if char.isascii() and (
                char.isalpha() or char.isdigit() or char in "_+#./-"
            ):
                current += 1
                continue
            if current >= 3:
                ascii_runs += 1
            current = 0
        if current >= 3:
            ascii_runs += 1
        return ascii_runs >= 3

    @staticmethod
    def _voice_switch_has_dense_number_shape(text: str) -> bool:
        groups = 0
        reading = False
        for char in text:
            if char.isdigit():
                if not reading:
                    groups += 1
                    reading = True
                continue
            if char not in {".", ":", "/", "-"}:
                reading = False
        return groups >= 4
