from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..media.picture import routes
from .markers import LOG_PREFIX


class ExpressionHintMixin:
    def _hidden_voice_cadence_hint(self, event: Any, probability: float) -> str:
        snapshot = getattr(self, "_voice_switch_cadence_snapshot", None)
        if not callable(snapshot) or event is None:
            return f"当前没有可用会话节奏记录；普通聊天语音概率参考为 {probability}%，仍以表达是否自然为准。"

        cadence = snapshot(event)
        seconds_since_voice = cadence.get("seconds_since_voice")
        text_after_voice = int(cadence.get("text_after_voice") or 0)
        consecutive_voice = int(cadence.get("consecutive_voice") or 0)
        max_consecutive_voice = int(cadence.get("max_consecutive_voice") or 3)
        if cadence.get("voice_chain_open"):
            return (
                f"刚刚发过 {consecutive_voice} 条语音；如果这轮仍是同一段短促、情绪连贯的口语回应，"
                f"可以自然接一条语音；这串语音本轮自然上限是 {max_consecutive_voice} 条，内容变长或转为说明时改用文字。"
            )
        if cadence.get("cooldown_active"):
            return (
                "刚刚已经发过一串语音，语音后文字回复还不够；这轮优先直接打字回复，"
                "不要调用 life_voice_generate 再让工具拦截，除非用户明确要求发语音。"
            )
        if consecutive_voice > 0:
            return (
                f"最近已经连续发过 {consecutive_voice} 次语音；如果还在同一段自然口语节奏里可以短促接上，"
                "否则优先文字，让聊天有呼吸感。"
            )
        if cadence.get("user_sent_voice"):
            return (
                f"对方这一轮发来语音；可以略微提高我用语音回应的意愿，但仍受 {probability}% "
                "概率、内容长度和自然度约束。"
            )
        if seconds_since_voice is None:
            return f"最近没有发过语音；普通聊天语音概率参考为 {probability}%，仍以当下表达是否自然为准。"
        minutes = max(1, int(seconds_since_voice) // 60)
        return (
            f"距离上次语音约 {minutes} 分钟，之后已经文字回复 {text_after_voice} 轮；"
            f"普通聊天语音概率参考为 {probability}%，仍以聊天节奏和表达自然度为准。"
        )

    def _voice_expression_channel_enabled(self, event: Any = None) -> bool:
        voice_config = getattr(self.config, "voice_generation", None)
        return bool(
            voice_config
            and getattr(voice_config, "enabled", False)
            and getattr(voice_config, "smart_switch_enabled", True)
            and self._voice_allowed_for_scope(event or "")
        )

    def _media_expression_channel_enabled(self) -> tuple[bool, bool]:
        image_config = getattr(self.config, "image_generation", None)
        video_config = getattr(self.config, "video_generation", None)
        return (
            bool(
                image_config
                and getattr(image_config, "enabled", False)
                and routes.has_channel(image_config)
            ),
            bool(video_config and getattr(video_config, "enabled", False)),
        )

    def _hidden_image_reference_hint(self, event: Any = None) -> str:
        if event is None:
            return "当前没有可判定的消息事件；除非工具参数显式提供 reference_image，否则按没有真实参考图处理。"
        resolver = getattr(self, "_resolve_life_image_reference", None)
        has_reference = False
        if callable(resolver):
            try:
                has_reference = bool(resolver(event))
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 图片参考状态解析失败：{type(exc).__name__}",
                    exc_info=True,
                )
                has_reference = False
        if has_reference:
            return "当前消息或引用消息有可用图片。"
        return "当前消息和引用消息没有可用图片。"

    def build_hidden_expression_channel_hint(self, event: Any = None) -> str:
        voice_config = getattr(self.config, "voice_generation", None)
        voice_enabled = bool(self._voice_expression_channel_enabled(event))
        image_enabled, video_enabled = self._media_expression_channel_enabled()
        if not (voice_enabled or image_enabled or video_enabled):
            return ""
        parts = [
            "\n\n<expression_channel>",
            "\n[HiddenExpressionChannel] 当前会话允许我按聊天语境选择文字、语音、图片、视频或已收藏表情表达；文字始终是默认表达。",
            "\n- 只在其他表达方式明显比文字更自然时使用对应工具；具体调用条件和参数以工具说明为准。",
            "\n- 已收藏表情只用于完成当前表达，没有合适素材就保持文字回复。",
        ]
        if voice_enabled:
            probability = getattr(voice_config, "smart_switch_probability", 35.0)
            cadence_hint = self._hidden_voice_cadence_hint(event, probability)
            parts.extend(
                [
                    "\n- 普通聊天时我只需要正常输出最终文字回复；插件会在发送前用本地节奏算法判断是否转成语音。",
                    f"\n[HiddenVoiceChance] 普通聊天语音概率参考为 {probability}%，仍以当下表达是否自然和聊天节奏为准。",
                    f"\n[HiddenVoiceCadence] {cadence_hint}",
                ]
            )
        if image_enabled or video_enabled:
            cadence_hint = self._hidden_media_cadence_hint(event)
            parts.extend(
                [
                    "\n[HiddenMediaExpression] 图片或视频是否合适只根据对话意图、当下状态和表达自然度判断；不要靠固定词触发，也不要为了展示而调用。",
                    f"\n[HiddenMediaCadence] {cadence_hint}",
                ]
            )
            if image_enabled:
                image_reference_hint = self._hidden_image_reference_hint(event)
                parts.append(f"\n- 当前图片参考状态：{image_reference_hint}")
            available = [
                label
                for enabled, label in ((image_enabled, "图片"), (video_enabled, "视频"))
                if enabled
            ]
            parts.append(f"\n- 当前可用媒体表达：{'、'.join(available)}。")
        parts.append("\n</expression_channel>")
        return "".join(parts)
