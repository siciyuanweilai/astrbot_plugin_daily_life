import asyncio
import base64
import datetime
import json
import os
import random
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from support import (
    AltProvider,
    Context,
    DataManager,
    Event,
    LifeSettings,
    LifeArchive,
    PersonaManager,
    Provider,
    ProviderRequest,
    DailyLifeRuntime,
    async_return,
)
from astrbot.core import html_renderer
from core.models import (
    BehaviorFeedbackRecord,
    BehaviorSceneRecord,
    BehaviorPatternRecord,
    CommitmentRecord,
    EmojiAssetRecord,
    EmotionArcRecord,
    ExpressionReviewRecord,
    ExpressionProfileRecord,
    FocusSlotRecord,
    LifeTermRecord,
    MemoryCorrectionRecord,
    PhysiologicalRhythmLogRecord,
    ReversePromptRecord,
    ReplyEffectRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
)
from core.models import DayRecord, LifeState, TimelineItem, WeatherInfo
from core.prompts import (
    CORE_INTERNAL_SYSTEM_PROMPT,
    CORE_REASONING_ANTI_PATTERN_RULE,
)
from core.life.autonomy import LifeAutonomyMixin
from core.media import GeminiImageService, SiliconFlowVoiceService
from core.memos import HostedMemOSService
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.director import MediaPromptExtractionError
from core.sight import SightClip, SightInsight, SightVault, TranscriptResult
from core.sight.bili import BiliMetadata, BiliTarget
from core.sight.brief import SightBrief
from core.sight.reader import SightTextResult


def image_generation_config(api_key: str = "image-key", **overrides):
    channel_overrides = {}
    for key in ("resolution", "aspect_ratio", "timeout_seconds"):
        if key in overrides:
            channel_overrides[key] = overrides.pop(key)
    channel = {
        "__template_key": "gemini",
        "api_url": "https://image.example",
        "api_key": api_key,
        "model": "gemini-3-pro-image-preview",
        **channel_overrides,
    }
    config = {
        "enabled": True,
        "text_channels": [dict(channel)],
        "edit_channels": [dict(channel)],
    }
    config.update(overrides)
    return config

class ResponseGateRuntimeMixin:
    def _response_gate_runtime(self, config=None, state=None):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(config or {})
        runtime.context = Context(Provider([]))
        runtime._init_response_gate_state()
        day = DayRecord(date="2026-05-24", state=state or LifeState())

        class Archive:
            async def get_day(self, date):
                return day

        runtime.archive = Archive()
        return runtime


class RuntimeAsyncHelperMixin:
    def _make_proactive_runtime(
        self,
        responses=(),
        *,
        provider_id="proactive-model",
        cooldown_minutes=10,
        mark_page_status_changed=None,
        context_config=None,
        persona_manager=None,
    ):
        provider = Provider(responses, provider_id=provider_id)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, providers={provider_id: provider}, config=context_config, persona_manager=persona_manager)
        runtime.config = LifeSettings.from_dict(
            {
                "rhythm_config": {"llm_provider": "default-model"},
                "proactive_config": {
                    "enabled": True,
                    "provider": provider_id,
                    "cooldown_minutes": cooldown_minutes,
                    "min_confidence": 0.7,
                    "max_reply_length": 30,
                },
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        context = runtime.context

        class Composer(LifeAutonomyMixin):
            def __init__(self, archive, config):
                self.archive = archive
                self.config = config

            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "completion_text", "")

            async def _cleanup_conversation(self, session_id):
                return None

            async def _get_persona(self, umo=""):
                manager = getattr(context, "persona_manager", None)
                getter = getattr(manager, "get_default_persona_v3", None)
                if callable(getter):
                    persona = await getter(umo)
                    if isinstance(persona, dict):
                        return str(persona.get("prompt") or persona.get("system_prompt") or persona.get("content") or "")
                    for attr in ("prompt", "system_prompt", "content"):
                        text = str(getattr(persona, attr, "") or "").strip()
                        if text:
                            return text
                return "一个喜欢看展的人"

        runtime.composer = Composer(runtime.archive, runtime.config)
        runtime._proactive_last_reply_at = {}
        runtime._proactive_idle_candidates = {}
        runtime._proactive_private_last_revisit_at = {}
        runtime._proactive_air_state = {}
        runtime._proactive_feedback_watch = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        if mark_page_status_changed:
            runtime.mark_page_status_changed = mark_page_status_changed
        return runtime, provider
    def _assert_last_assistant_history(self, runtime, scope, text):
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(history[-1], {"role": "assistant", "content": text})
        self.assertTrue(
            any(
                call[0] == "update_conversation" and call[1] == scope
                for call in runtime.context.conversation_manager.calls
            )
        )
    def _assert_user_history_has_image(self, item, image_path):
        self.assertEqual(item["role"], "user")
        content = item["content"]
        self.assertIsInstance(content, list)
        self.assertIn({"type": "text", "text": f"[Image Attachment: path {image_path}]"}, content)
        image_parts = [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertTrue(str(image_parts[0]["image_url"]["url"]).startswith(("data:image/", "http://", "https://")))
    def _stub_media_director(self, runtime):
        runtime._direct_life_image_payload = lambda event, prompt, **kwargs: async_return(
            types.SimpleNamespace(prompt=prompt, contains_character=False, needs_character_reference=False)
        )
        runtime._direct_life_video_prompt = lambda event, prompt: async_return(prompt)
