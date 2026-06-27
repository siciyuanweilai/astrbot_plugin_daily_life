import asyncio
import math
import random
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain

from ..markers import LOG_PREFIX


class ProactiveSegmentMixin:
    @staticmethod
    def _config_get(config: Any, key: str, default: Any = None) -> Any:
        if hasattr(config, "get"):
            try:
                return config.get(key, default)
            except TypeError:
                pass
        return getattr(config, key, default)

    def _has_segmented_reply_config(self, config: Any) -> bool:
        platform_settings = self._config_get(config, "platform_settings", None)
        segmented_config = self._config_get(platform_settings, "segmented_reply", None)
        return segmented_config is not None

    def _framework_config(self, target_scope: str) -> Any:
        get_config = getattr(self.context, "get_config", None)
        if not callable(get_config):
            return {}
        try:
            config = get_config(target_scope)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取会话框架配置失败，闲时回复不使用分段发送：{exc}")
            return {}
        return config if self._has_segmented_reply_config(config) else {}

    def _segmented_reply_config(self, target_scope: str) -> Any:
        config = self._framework_config(target_scope)
        platform_settings = self._config_get(config, "platform_settings", {})
        return self._config_get(platform_settings, "segmented_reply", {})

    def _target_platform_name(self, target_scope: str) -> str:
        platform_id = str(target_scope or "").split(":", 1)[0].strip()
        platform_manager = getattr(self.context, "platform_manager", None)
        instances = getattr(platform_manager, "platform_insts", None)
        if instances is None and hasattr(platform_manager, "get_insts"):
            try:
                instances = platform_manager.get_insts()
            except Exception:
                instances = None
        for instance in instances or []:
            meta = getattr(instance, "meta", None)
            if not callable(meta):
                continue
            info = meta()
            if str(getattr(info, "id", "") or "") == platform_id:
                return str(getattr(info, "name", "") or platform_id)
        return platform_id

    def _proactive_segmented_reply_enabled(self, target_scope: str) -> bool:
        if self._target_platform_name(target_scope) in {
            "qq_official_webhook",
            "weixin_official_account",
            "dingtalk",
        }:
            return False
        segmented_config = self._segmented_reply_config(target_scope)
        enabled = bool(self._config_get(segmented_config, "enable", False))
        if not enabled:
            logger.debug(f"{LOG_PREFIX} 闲时回复未启用框架分段发送：未读取到启用的分段回复配置")
        return enabled

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _split_proactive_text_by_words(self, text: str, split_words: Any) -> list[str]:
        if not isinstance(split_words, (list, tuple)):
            return [text]
        words = [str(word) for word in split_words if str(word)]
        if not words:
            return [text]

        pattern = re.compile(
            f"(.*?({'|'.join(sorted([re.escape(word) for word in words], key=len, reverse=True))})|.+$)",
            re.DOTALL,
        )
        segments: list[str] = []
        for segment in pattern.findall(text):
            content = segment[0] if isinstance(segment, tuple) else segment
            if not isinstance(content, str):
                continue
            for word in words:
                if content.endswith(word):
                    content = content[: -len(word)]
                    break
            if content.strip():
                segments.append(content)
        return segments or [text]

    def _split_proactive_text_naturally(self, text: str) -> list[str]:
        text = str(text or "")
        if not text.strip():
            return [text]
        segments: list[str] = []
        current: list[str] = []
        punctuation = {"。", "！", "？", "!", "?", "~", "～", "…"}
        for char in text:
            current.append(char)
            if char == "\n" or char in punctuation:
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
        tail = "".join(current).strip()
        if tail:
            segments.append(tail)
        return segments or [text]

    def _split_proactive_text(self, text: str, segmented_config: Any) -> list[str]:
        threshold = self._as_int(self._config_get(segmented_config, "words_count_threshold", 150), 150)
        if len(text) > threshold:
            return [text]

        split_mode = str(self._config_get(segmented_config, "split_mode", "natural") or "natural")
        if split_mode == "words":
            split_words = self._config_get(segmented_config, "split_words", ["。", "？", "！", "~", "…"])
            raw_segments = self._split_proactive_text_by_words(text, split_words)
        elif split_mode == "regex":
            regex = str(self._config_get(segmented_config, "regex", r".*?[。？！~…]+|.+$") or r".*?[。？！~…]+|.+$")
            try:
                raw_segments = re.findall(regex, text, re.DOTALL | re.MULTILINE)
            except re.error as exc:
                logger.warning(f"{LOG_PREFIX} 框架分段回复规则无效，闲时回复改为单条发送：{exc}")
                raw_segments = [text]
        else:
            raw_segments = self._split_proactive_text_naturally(text)

        cleanup_rule = str(self._config_get(segmented_config, "content_cleanup_rule", "") or "")
        segments: list[str] = []
        for segment in raw_segments:
            if isinstance(segment, tuple):
                segment = "".join(str(part) for part in segment if part)
            segment = str(segment or "").strip()
            if not segment:
                continue
            if cleanup_rule:
                try:
                    segment = re.sub(cleanup_rule, "", segment).strip()
                except re.error as exc:
                    logger.warning(f"{LOG_PREFIX} 框架分段回复清理规则无效，已跳过清理：{exc}")
                    cleanup_rule = ""
            if segment:
                segments.append(segment)
        return segments or [text]

    @staticmethod
    def _word_count(text: str) -> int:
        if all(ord(char) < 128 for char in text):
            return len(text.split())
        return len([char for char in text if char.isalnum()])

    def _segment_interval(self, text: str, segmented_config: Any) -> float:
        if str(self._config_get(segmented_config, "interval_method", "random") or "random") == "log":
            log_base = max(1.01, self._as_float(self._config_get(segmented_config, "log_base", 2.6), 2.6))
            start = math.log(self._word_count(text) + 1, log_base)
            return random.uniform(start, start + 0.5)

        interval_text = str(self._config_get(segmented_config, "interval", "1.5,3.5") or "1.5,3.5")
        parts = [self._as_float(part, 0.0) for part in interval_text.replace(" ", "").split(",")[:2]]
        if len(parts) < 2:
            parts = [1.5, 3.5]
        start, end = sorted(max(0.0, part) for part in parts)
        return random.uniform(start, end)

    async def _send_segmented_proactive_message(
        self,
        target_scope: str,
        reply_text: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        segmented_config = self._segmented_reply_config(target_scope)
        segments = self._split_proactive_text(reply_text, segmented_config)
        logger.debug(f"{LOG_PREFIX} 闲时回复按框架分段配置发送：{len(segments)} 段")
        for segment in segments:
            interval = self._segment_interval(segment, segmented_config)
            if interval > 0:
                await asyncio.sleep(interval)
            if not await self.send_message_if_not_recalled(
                target_scope,
                MessageChain().message(segment),
                source_event=source_event,
                source_message_id=source_message_id,
            ):
                return False
            self.note_structured_bot_message(target_scope, segment)
        return True

    def _proactive_send_delay_seconds(self, payload: dict[str, Any] | None) -> float:
        if not isinstance(payload, dict):
            return 0.0
        timing = payload.get("send_timing") if isinstance(payload.get("send_timing"), dict) else {}
        value = timing.get("delay_seconds", payload.get("delay_seconds"))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(delay, 12.0))

    async def _apply_proactive_send_timing(self, payload: dict[str, Any] | None) -> None:
        delay = self._proactive_send_delay_seconds(payload)
        if delay <= 0:
            return
        reason = ""
        if isinstance(payload, dict) and isinstance(payload.get("send_timing"), dict):
            reason = str(payload["send_timing"].get("reason") or "").strip()
        logger.debug(f"{LOG_PREFIX} 闲时回复发送节奏等待 {delay:.1f} 秒" + (f"：{reason}" if reason else ""))
        await asyncio.sleep(delay)
