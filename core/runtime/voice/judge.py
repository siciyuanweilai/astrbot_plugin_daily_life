from __future__ import annotations

from typing import Any


class VoiceSwitchJudgeMixin:
    _VOICE_SWITCH_MIN_NATURAL_SCORE = 0.58

    def _judge_voice_switch_channel(self, event: Any, reply_text: str) -> dict[str, Any]:
        text = str(reply_text or "").strip()
        if not text:
            return self._voice_switch_text_decision("我还没整理出要说的话，先不发语音。", 0.0)

        structural_block = self._voice_switch_structural_text_reason(text)
        if structural_block:
            return self._voice_switch_text_decision(structural_block, 0.96)

        score, reasons = self._voice_switch_natural_score(event, text)
        if score < self._VOICE_SWITCH_MIN_NATURAL_SCORE:
            reason = "我这轮更适合打字发过去，留在屏幕上读起来更清楚。"
            if reasons:
                reason = f"{reason}（{reasons[0]}）"
            return self._voice_switch_text_decision(reason, score)

        tone = self._voice_switch_tone_shape(text)
        emotion_category = self._voice_switch_emotion_category(score, reasons, tone)
        emotion = self._voice_switch_emotion_label(text, emotion_category, reasons, tone)
        reason = self._voice_switch_voice_reason(reasons, emotion_category, tone)
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

    def _voice_switch_natural_score(self, event: Any, text: str) -> tuple[float, list[str]]:
        normalized = " ".join(str(text or "").split())
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        length = len(normalized)
        cadence = self._voice_switch_cadence_snapshot(event)
        reasons: list[str] = []
        score = 0.46

        if length <= 14:
            score += 0.24
            reasons.append("这句很短")
        elif length <= 36:
            score += 0.16
            reasons.append("这轮像一句顺口回应")
        elif length <= 72:
            score += 0.06
        else:
            score -= 0.18
            reasons.append("内容稍长")

        if len(lines) == 2:
            score += 0.05
        elif len(lines) >= 4:
            score -= 0.12

        if self._voice_switch_is_dense_text(normalized, lines):
            score -= 0.18
            reasons.append("留在屏幕上读更清楚")
        else:
            score += 0.06

        if self._voice_switch_has_light_speaking_shape(normalized, lines):
            score += 0.12
            reasons.append("更像顺口接话")

        if cadence.get("user_sent_voice"):
            score += 0.18
            reasons.append("对方刚发语音")
        if cadence.get("voice_chain_open"):
            score += 0.12
            reasons.append("还在自然语音串里")
        if cadence.get("cooldown_active"):
            score -= 0.22
            reasons.append("刚发过语音")
        if cadence.get("text_after_voice", 0) >= self._VOICE_SWITCH_TEXT_TURNS_AFTER_VOICE:
            score += 0.04
        if cadence.get("voice_chain_exhausted"):
            score -= 0.12
            reasons.append("连续语音已经够多")

        probability = max(0.0, min(float(cadence.get("probability", 35.0) or 35.0), 100.0))
        score *= 0.65 + probability / 200.0
        return max(0.0, min(score, 1.0)), reasons

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
            if char.isascii() and (char.isalpha() or char.isdigit() or char in "_+#./-"):
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

    @staticmethod
    def _voice_switch_is_dense_text(text: str, lines: list[str]) -> bool:
        if len(text) >= 60:
            return True
        if len(lines) >= 3 and sum(len(line) for line in lines) >= 42:
            return True
        punctuation = sum(1 for char in text if char in "{}[]()<>/_=:+-*#|")
        return punctuation >= 4

    @staticmethod
    def _voice_switch_has_light_speaking_shape(text: str, lines: list[str]) -> bool:
        if len(lines) > 2:
            return False
        if len(text) <= 24:
            return True
        sentence_breaks = sum(text.count(mark) for mark in "，。！？!?~～…")
        return sentence_breaks <= 3 and len(text) <= 42

    @staticmethod
    def _voice_switch_clause_lengths(text: str) -> list[int]:
        lengths: list[int] = []
        current: list[str] = []
        for char in str(text or ""):
            if char in {"，", "。", "！", "？", "!", "?", "~", "～", "…", "\n"}:
                segment = "".join(current).strip()
                if segment:
                    lengths.append(len(segment))
                current = []
                continue
            current.append(char)
        tail = "".join(current).strip()
        if tail:
            lengths.append(len(tail))
        return lengths

    def _voice_switch_tone_shape(self, text: str) -> dict[str, Any]:
        normalized = " ".join(str(text or "").split())
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        clauses = self._voice_switch_clause_lengths(normalized)
        strong_marks = sum(normalized.count(mark) for mark in "!?！？")
        pause_marks = sum(normalized.count(mark) for mark in "…~～")
        clipped = (
            len(lines) == 1
            and len(clauses) == 2
            and 8 <= sum(clauses) <= 18
            and all(3 <= length <= 10 for length in clauses[:2])
            and pause_marks == 0
        )
        drooping = (
            strong_marks == 0
            and (
                (pause_marks >= 1 and len(normalized) <= 28)
                or (pause_marks >= 2 and len(normalized) <= 36)
            )
        )
        return {
            "strong_marks": strong_marks,
            "pause_marks": pause_marks,
            "clipped": clipped,
            "drooping": drooping,
            "lines": lines,
            "clauses": clauses,
        }

    @staticmethod
    def _voice_switch_emotion_category(score: float, reasons: list[str], tone: dict[str, Any]) -> str:
        if "连续语音已经够多" in reasons:
            return "neutral"
        if "对方刚发语音" in reasons or "还在自然语音串里" in reasons:
            return "happy"
        if bool(tone.get("clipped")) or int(tone.get("strong_marks") or 0) >= 1:
            return "angry"
        if bool(tone.get("drooping")):
            return "sad"
        if score >= 0.78:
            return "happy"
        return "neutral"

    @staticmethod
    def _voice_switch_emotion_label(text: str, category: str, reasons: list[str], tone: dict[str, Any]) -> str:
        if "对方刚发语音" in reasons:
            return "自然接话"
        if "还在自然语音串里" in reasons:
            return "顺口续上"
        if category == "angry":
            if bool(tone.get("clipped")):
                return "带点不耐的快声"
            return "语气稍冲"
        if category == "sad":
            if int(tone.get("pause_marks") or 0) >= 1:
                return "低低慢声"
            return "轻轻落下"
        if category == "happy":
            return "轻松亲近"
        if len(str(text or "").strip()) <= 12:
            return "短句轻声"
        return "平常口吻"

    @staticmethod
    def _voice_switch_voice_reason(reasons: list[str], category: str, tone: dict[str, Any]) -> str:
        if "还在自然语音串里" in reasons:
            return "我刚才已经在用语音接话了，这句也短，顺着说出来更自然。"
        if "对方刚发语音" in reasons:
            return "对方刚用语音过来，我这句也像顺口接话，直接说出来更贴近当下。"
        if category == "angry":
            if bool(tone.get("clipped")):
                return "我这句收得比较紧，语气比字面更重要，直接说出来更贴近当下。"
            return "我这句有点顶着说的劲儿，用语音会比文字更自然。"
        if category == "sad":
            return "我这句带着停顿和往下落的气口，说出来会比打字更有当下感。"
        if "这句很短" in reasons or "这轮像一句顺口回应" in reasons:
            return "我这句很短，像顺口接的一句话，直接说出来更自然。"
        return "我这轮更像临场回应，用语音说出来会比文字更自然。"
