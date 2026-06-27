from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...prompts import (
    DEFAULT_CHAT_PROMPT,
    DEFAULT_OUTFIT_PROMPTS,
    DEFAULT_STATE_PROMPT,
    DEFAULT_TIMELINE_PROMPT,
    DEFAULT_WORLD_PROMPT,
)
from ..vocab import WEEKDAY_NAMES
from .catalog import CatalogSettings
from .basis import (
    LifecycleSettings,
    ProactiveReplySettings,
    ResponseGateSettings,
    StateSettings,
    StorageSettings,
    TaskModelSettings,
    WeatherSettings,
    WebInspirationSettings,
)
from .generate import ImageGenerationSettings, VideoGenerationSettings, VoiceGenerationSettings
from .recall import CommitmentSettings, MemorySettings, MemOSSettings
from .parse import as_float, as_int, as_str, as_str_list, dict_section, normalize_time


@dataclass(slots=True)
class LifeSettings:
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
    lifecycle: LifecycleSettings = field(default_factory=LifecycleSettings)
    outfit: TaskModelSettings = field(default_factory=TaskModelSettings)
    invite: TaskModelSettings = field(default_factory=TaskModelSettings)
    material: TaskModelSettings = field(default_factory=TaskModelSettings)
    vision: TaskModelSettings = field(default_factory=TaskModelSettings)
    image_generation: ImageGenerationSettings = field(default_factory=ImageGenerationSettings)
    video_generation: VideoGenerationSettings = field(default_factory=VideoGenerationSettings)
    voice_generation: VoiceGenerationSettings = field(default_factory=VoiceGenerationSettings)
    web_inspiration: WebInspirationSettings = field(default_factory=WebInspirationSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    catalog: CatalogSettings = field(default_factory=CatalogSettings)
    week_plan_day: str = "monday"
    week_plan_time: str = "06:00"
    default_week_template: str = "random"
    week_template_weights: dict[str, float] = field(
        default_factory=lambda: {
            "regular": 0.3,
            "sprint": 0.15,
            "relax": 0.2,
            "social": 0.15,
            "recovery": 0.1,
            "holiday": 0.1,
            "study": 0.05,
            "gaming": 0.1,
        }
    )
    outfit_prompts: dict[str, str] = field(default_factory=lambda: DEFAULT_OUTFIT_PROMPTS.copy())
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
        lifecycle_conf = dict_section(data, "lifecycle_config")
        outfit_conf = dict_section(data, "outfit_config")
        invite_conf = dict_section(data, "invite_config")
        material_conf = dict_section(data, "material_config")
        vision_conf = dict_section(data, "vision_config")
        image_generation_conf = dict_section(data, "image_generation_config")
        video_generation_conf = dict_section(data, "video_generation_config")
        voice_generation_conf = dict_section(data, "voice_generation_config")
        web_inspiration_conf = dict_section(data, "web_inspiration_config")
        storage_conf = dict_section(data, "storage_config")
        week_conf = dict_section(data, "weekly_theme_config")
        prompt_conf = dict_section(data, "story_engine_config")

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
        config.lifecycle = LifecycleSettings.from_dict(lifecycle_conf)
        config.outfit = TaskModelSettings.from_dict(outfit_conf)
        config.invite = TaskModelSettings.from_dict(invite_conf)
        config.material = TaskModelSettings.from_dict(material_conf)
        config.vision = TaskModelSettings.from_dict(vision_conf)
        config.image_generation = ImageGenerationSettings.from_dict(image_generation_conf)
        config.video_generation = VideoGenerationSettings.from_dict(video_generation_conf)
        config.voice_generation = VoiceGenerationSettings.from_dict(voice_generation_conf)
        config.web_inspiration = WebInspirationSettings.from_dict(web_inspiration_conf)
        config.storage = StorageSettings.from_dict(storage_conf)

        week_day = as_str(week_conf.get("generation_day", "monday"), "monday").lower()
        config.week_plan_day = week_day if week_day in WEEKDAY_NAMES else "monday"
        config.week_plan_time = normalize_time(week_conf.get("generation_time", "06:00"), "06:00")
        config.default_week_template = as_str(week_conf.get("default_template", "random"), "random").strip() or "random"
        config.week_template_weights = {
            "regular": as_float(week_conf.get("weight_regular", 0.3), 0.3, 0.0),
            "sprint": as_float(week_conf.get("weight_sprint", 0.15), 0.15, 0.0),
            "relax": as_float(week_conf.get("weight_relax", 0.2), 0.2, 0.0),
            "social": as_float(week_conf.get("weight_social", 0.15), 0.15, 0.0),
            "recovery": as_float(week_conf.get("weight_recovery", 0.1), 0.1, 0.0),
            "holiday": as_float(week_conf.get("weight_holiday", 0.1), 0.1, 0.0),
            "study": as_float(week_conf.get("weight_study", 0.05), 0.05, 0.0),
            "gaming": as_float(week_conf.get("weight_gaming", 0.1), 0.1, 0.0),
        }

        config.outfit_prompts = DEFAULT_OUTFIT_PROMPTS.copy()
        if "outfit_morning" in prompt_conf:
            config.outfit_prompts["morning"] = as_str(prompt_conf["outfit_morning"])
        if "outfit_daytime" in prompt_conf:
            config.outfit_prompts["daytime"] = as_str(prompt_conf["outfit_daytime"])
        if "outfit_night" in prompt_conf:
            config.outfit_prompts["night"] = as_str(prompt_conf["outfit_night"])
        config.state_prompt = as_str(prompt_conf.get("state_rules", DEFAULT_STATE_PROMPT), DEFAULT_STATE_PROMPT).strip() or DEFAULT_STATE_PROMPT
        config.timeline_prompt = as_str(prompt_conf.get("timeline_rules", DEFAULT_TIMELINE_PROMPT), DEFAULT_TIMELINE_PROMPT).strip() or DEFAULT_TIMELINE_PROMPT
        config.world_prompt = as_str(prompt_conf.get("world_rules", DEFAULT_WORLD_PROMPT), DEFAULT_WORLD_PROMPT).strip() or DEFAULT_WORLD_PROMPT
        config.chat_prompt = as_str(prompt_conf.get("chat_rules", DEFAULT_CHAT_PROMPT), DEFAULT_CHAT_PROMPT).strip() or DEFAULT_CHAT_PROMPT
        return config
