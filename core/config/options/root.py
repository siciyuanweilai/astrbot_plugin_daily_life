from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...prompts import (
    DEFAULT_CHAT_PROMPT,
    DEFAULT_STATE_PROMPT,
    DEFAULT_TIMELINE_PROMPT,
    DEFAULT_WORLD_PROMPT,
)
from .basis import (
    ChatStyleSettings,
    EmojiSettings,
    LifecycleSettings,
    OutfitSettings,
    ProactiveReplySettings,
    ResponseGateSettings,
    SightSettings,
    StateSettings,
    StorageSettings,
    TaskModelSettings,
    WeatherSettings,
    WebInspirationSettings,
)
from .generate import ImageGenerationSettings, VideoGenerationSettings, VoiceGenerationSettings
from .retention import CommitmentSettings, MemorySettings, MemOSSettings
from .cast import as_int, as_str, as_str_list, dict_section, normalize_time


@dataclass(slots=True)
class LifeSettings:
    bot_identity_aliases: list[str] = field(default_factory=list)
    schedule_time: str = "07:00"
    reference_history_days: int = 3
    reference_groups: list[str] = field(default_factory=list)
    reference_users: list[str] = field(default_factory=list)
    history_hours: int = 24
    history_max_count: int = 50
    llm_provider: str = ""
    weather: WeatherSettings = field(default_factory=WeatherSettings)
    state: StateSettings = field(default_factory=StateSettings)
    commitments: CommitmentSettings = field(default_factory=CommitmentSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    memos: MemOSSettings = field(default_factory=MemOSSettings)
    proactive: ProactiveReplySettings = field(default_factory=ProactiveReplySettings)
    response_gate: ResponseGateSettings = field(default_factory=ResponseGateSettings)
    chat_style: ChatStyleSettings = field(default_factory=ChatStyleSettings)
    lifecycle: LifecycleSettings = field(default_factory=LifecycleSettings)
    outfit: OutfitSettings = field(default_factory=OutfitSettings)
    invite: TaskModelSettings = field(default_factory=TaskModelSettings)
    vision: TaskModelSettings = field(default_factory=TaskModelSettings)
    emoji: EmojiSettings = field(default_factory=EmojiSettings)
    sight: SightSettings = field(default_factory=SightSettings)
    image_generation: ImageGenerationSettings = field(default_factory=ImageGenerationSettings)
    video_generation: VideoGenerationSettings = field(default_factory=VideoGenerationSettings)
    voice_generation: VoiceGenerationSettings = field(default_factory=VoiceGenerationSettings)
    web_inspiration: WebInspirationSettings = field(default_factory=WebInspirationSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    state_prompt: str = DEFAULT_STATE_PROMPT
    timeline_prompt: str = DEFAULT_TIMELINE_PROMPT
    world_prompt: str = DEFAULT_WORLD_PROMPT
    chat_prompt: str = DEFAULT_CHAT_PROMPT

    @staticmethod
    def from_dict(data: Any) -> "LifeSettings":
        config = LifeSettings()
        if not isinstance(data, dict):
            return config

        rhythm_conf = dict_section(data, "rhythm_config")
        weather_conf = dict_section(data, "weather_awareness")
        state_conf = dict_section(data, "state_config")
        commitment_conf = dict_section(data, "commitment_config")
        memory_conf = dict_section(data, "memory_config")
        memos_conf = dict_section(data, "memos_config")
        proactive_conf = dict_section(data, "proactive_config")
        response_gate_conf = dict_section(data, "response_gate_config")
        chat_style_conf = dict_section(data, "chat_style_config")
        lifecycle_conf = dict_section(data, "lifecycle_config")
        outfit_conf = dict_section(data, "outfit_config")
        invite_conf = dict_section(data, "invite_config")
        vision_conf = dict_section(data, "vision_config")
        emoji_conf = dict_section(data, "emoji_config")
        sight_conf = dict_section(data, "sight_config")
        image_generation_conf = dict_section(data, "image_generation_config")
        video_generation_conf = dict_section(data, "video_generation_config")
        voice_generation_conf = dict_section(data, "voice_generation_config")
        web_inspiration_conf = dict_section(data, "web_inspiration_config")
        storage_conf = dict_section(data, "storage_config")
        prompt_conf = dict_section(data, "story_engine_config")

        config.bot_identity_aliases = as_str_list(data.get("bot_identity_aliases", []))
        config.schedule_time = normalize_time(rhythm_conf.get("schedule_time", "07:00"), "07:00")
        config.reference_history_days = as_int(rhythm_conf.get("history_days", 3), 3, 0, 30)
        config.reference_groups = as_str_list(rhythm_conf.get("reference_groups", []))
        config.reference_users = as_str_list(rhythm_conf.get("reference_users", []))
        config.history_hours = as_int(rhythm_conf.get("history_hours", 24), 24, 1, 168)
        config.history_max_count = as_int(rhythm_conf.get("history_max_count", 50), 50, 1, 200)
        config.llm_provider = as_str(rhythm_conf.get("llm_provider", ""))

        config.weather = WeatherSettings.from_dict(weather_conf)
        config.state = StateSettings.from_dict(state_conf)
        config.commitments = CommitmentSettings.from_dict(commitment_conf)
        config.memory = MemorySettings.from_dict(memory_conf)
        config.memos = MemOSSettings.from_dict(memos_conf)
        config.proactive = ProactiveReplySettings.from_dict(proactive_conf)
        config.response_gate = ResponseGateSettings.from_dict(response_gate_conf)
        config.chat_style = ChatStyleSettings.from_dict(chat_style_conf)
        config.lifecycle = LifecycleSettings.from_dict(lifecycle_conf)
        config.outfit = OutfitSettings.from_dict(outfit_conf)
        config.invite = TaskModelSettings.from_dict(invite_conf)
        config.vision = TaskModelSettings.from_dict(vision_conf)
        config.emoji = EmojiSettings.from_dict(emoji_conf)
        config.sight = SightSettings.from_dict(sight_conf)
        config.image_generation = ImageGenerationSettings.from_dict(image_generation_conf)
        config.video_generation = VideoGenerationSettings.from_dict(video_generation_conf)
        config.video_generation.aspect_ratio = config.image_generation.primary_aspect_ratio()
        config.voice_generation = VoiceGenerationSettings.from_dict(voice_generation_conf)
        config.web_inspiration = WebInspirationSettings.from_dict(web_inspiration_conf)
        config.storage = StorageSettings.from_dict(storage_conf)

        config.state_prompt = as_str(prompt_conf.get("state_rules", DEFAULT_STATE_PROMPT), DEFAULT_STATE_PROMPT).strip() or DEFAULT_STATE_PROMPT
        config.timeline_prompt = as_str(prompt_conf.get("timeline_rules", DEFAULT_TIMELINE_PROMPT), DEFAULT_TIMELINE_PROMPT).strip() or DEFAULT_TIMELINE_PROMPT
        config.world_prompt = as_str(prompt_conf.get("world_rules", DEFAULT_WORLD_PROMPT), DEFAULT_WORLD_PROMPT).strip() or DEFAULT_WORLD_PROMPT
        config.chat_prompt = as_str(prompt_conf.get("chat_rules", DEFAULT_CHAT_PROMPT), DEFAULT_CHAT_PROMPT).strip() or DEFAULT_CHAT_PROMPT
        return config
