from __future__ import annotations

from typing import Any

from ..media.picture import routes


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
            bool(image_config and getattr(image_config, "enabled", False) and routes.has_channel(image_config)),
            bool(video_config and getattr(video_config, "enabled", False)),
        )

    def build_hidden_expression_channel_hint(self, event: Any = None) -> str:
        voice_config = getattr(self.config, "voice_generation", None)
        voice_enabled = bool(self._voice_expression_channel_enabled(event))
        image_enabled, video_enabled = self._media_expression_channel_enabled()
        if not (voice_enabled or image_enabled or video_enabled):
            return ""
        parts = [
            "\n\n<expression_channel>",
            "\n[HiddenExpressionChannel] 当前会话允许我按聊天语境选择文字、语音、图片或视频表达；文字始终是默认表达。",
        ]
        if voice_enabled:
            probability = getattr(voice_config, "smart_switch_probability", 35.0)
            cadence_hint = self._hidden_voice_cadence_hint(event, probability)
            parts.extend(
                [
                    "\n- 普通聊天时我只需要正常输出最终文字回复；插件会在发送前用本地节奏算法判断是否转成语音。",
                    "\n- 语音只在简短问候、撒娇、安慰、轻松吐槽、临场感强，或语气/情绪比信息结构更重要时偶尔使用。",
                    "\n- 刚发过语音后，如果仍是同一段短促、情绪连贯的口语回应，可以偶尔连发；同一串可能一条就停，也可能两三条后停，不要形成固定规律。",
                    "\n- 如果本轮包含步骤说明、链接、代码、清单、较长解释、需要回看或容易听错的信息，应使用文字回复。",
                    "\n- 只有用户明确要求发语音、录一句、说给他听时，才调用 life_voice_generate，并附上自然 emotion 和第一人称 decision_reason。",
                    "\n- 决定普通文字回复时，直接正常输出文字；不要为了说明不用语音而调用工具。",
                    "\n- 用户没有明确要求语音时，不要主动调用 life_voice_generate，也不要输出“我要发语音/我改用文字”这类说明。",
                    "\n- 用户明确要求语音且工具调用成功后，不要再用文字重复同一句。",
                    "\n- 如果语音工具失败，再自然改用文字回复。",
                    f"\n[HiddenVoiceChance] 普通聊天语音概率参考为 {probability}%，仍以当下表达是否自然和聊天节奏为准。",
                    f"\n[HiddenVoiceCadence] {cadence_hint}",
                ]
            )
        if image_enabled or video_enabled:
            cadence_hint = self._hidden_media_cadence_hint(event)
            parts.extend(
                [
                    "\n[HiddenMediaExpression] 当前会话允许我在合适时用生活图片或短视频展示此刻状态、穿搭、环境、动作或照片/视频效果。",
                    "\n- 判断重点是对话意图、当下状态和表达自然度；不要靠固定词触发，也不要为了展示而硬发。",
                    "\n- 如果直拍不自然，就换成更生活化的视角，比如环境、动作细节、手边物件或氛围画面；如果文字更自然，就直接文字回复。",
                    f"\n[HiddenMediaCadence] {cadence_hint}",
                ]
            )
            if image_enabled:
                parts.extend(
                    [
                        "\n- 图片适合静态画面更有表达力的场合：展示此刻生活状态、穿搭、环境、食物、天气氛围、房间角落、路上见闻，或把用户想看的画面变成照片感结果。",
                        "\n- 用户意图是在看一张图、拍一张、生成生活照或把内容变成图时，调用 life_image_generate；用户发图或引用图并希望改图、延展图、换风格时，调用 edit_life_image。",
                        "\n- 发图工具成功后，可以再自然补一句短文字，但不要重复解释提示词或工具流程。",
                    ]
                )
            else:
                parts.append("\n- 图片生成当前未启用；不要调用 life_image_generate 或 edit_life_image。")
            if video_enabled:
                parts.extend(
                    [
                        "\n- 视频生成慢且成本高，必须更谨慎；优先只在用户明确要视频、录一段、动起来、转视频，或强烈需要动态镜头时调用 life_video_generate。",
                        "\n- 当前消息或引用消息带图片，且用户想把这张图做成视频/动起来时，life_video_generate 会自动把那张图作为首帧/参考图；不要先调用 life_image_generate。",
                        "\n- 普通“看看现在、发张照片、在干嘛”优先图片或文字，不要升级成视频。",
                    ]
                )
                if image_enabled:
                    parts.append("\n- 没有当前图片时，只有动作过程、镜头移动、动态氛围等非常强的场景需求，才允许自动先生成首帧再生成视频。")
                else:
                    parts.append("\n- 没有当前消息或引用图片时，不要主动调用 life_video_generate，因为首帧图片生成不可用。")
            elif not video_enabled:
                parts.append("\n- 视频生成当前未启用；不要调用 life_video_generate。")
        parts.append("\n</expression_channel>")
        return "".join(parts)
