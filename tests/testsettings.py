import json
import unittest

from support import (
    DEFAULT_DAILY_THEMES,
    DEFAULT_MOOD_COLORS,
    DEFAULT_NIGHT_HAIRSTYLES,
    DEFAULT_OUTFIT_STYLES,
    DEFAULT_SCHEDULE_TYPES,
    DEFAULT_SLEEP_STYLES,
    DEFAULT_STYLE_TO_HAIR_MAP,
    LifeSettings,
    PLUGIN_ROOT,
)
from core.prompts import (
    DEFAULT_WEB_MATERIAL_PROMPT,
    DEFAULT_WEB_OUTFIT_PROMPT,
    DEFAULT_WEB_TODAY_PROMPT,
    DEFAULT_WEB_WEEK_TEMPLATE_PROMPT,
)


class LifeSettingsTest(unittest.TestCase):
    def test_config_parsing_tolerates_bad_types(self):
        config = LifeSettings.from_dict(
            {
                "rhythm_config": {
                    "schedule_time": "bad",
                    "llm_provider": "generation-model",
                    "history_days": "bad",
                    "history_hours": "bad",
                    "history_max_count": "0",
                    "reference_groups": 123456,
                    "reference_users": ["654321", None],
                },
                "weather_awareness": {"aware_outfit": "false", "aware_activity": "yes"},
                "state_config": {
                    "enabled": "false",
                    "provider": "state-model",
                    "refresh_minutes": "999",
                    "quiet_hours": "0:00-6:30",
                },
                "outfit_config": {"provider": "outfit-model"},
                "invite_config": {"provider": "invite-model"},
                "memory_config": {
                    "provider": "memory-model",
                    "min_message_length": "0",
                    "max_generation_items": "999",
                    "max_injection_items": "-1",
                },
                "memos_config": {
                    "enabled": "yes",
                    "base_url": "",
                    "api_key": "memos-key",
                    "timeout_seconds": "0.1",
                    "memory_limit_number": "99",
                    "preference_limit_number": "-1",
                    "max_context_items": "99",
                    "max_context_chars": "10",
                    "include_preference": "off",
                    "sync_selected_memory": "yes",
                    "sync_corrections": "off",
                },
                "lifecycle_config": {"provider": "review-model"},
                "material_config": {"provider": "material-model"},
                "vision_config": {"provider": "vision-model"},
                "image_generation_config": {
                    "enabled": "yes",
                    "channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay-a.example/",
                            "api_key": "relay-key",
                            "model": "gemini-relay-a",
                            "resolution": "",
                            "aspect_ratio": "",
                            "timeout_seconds": "1",
                        },
                        {
                            "__template_key": "openai",
                            "api_url": "https://relay-b.example",
                            "api_key": "relay-key-b",
                            "model": "",
                            "resolution": "2k",
                            "aspect_ratio": "16:9",
                            "timeout_seconds": "9999",
                        },
                        "bad-line",
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay-c.example",
                            "api_key": "",
                        },
                    ],
                    "character_reference_images": [
                        {"path": "D:/ref/role.png", "name": "正面参考.png", "mime": "image/png", "size": 1234},
                        {"name": "缺路径"},
                    ],
                    "character_reference_policy": "bad-policy",
                    "reference_max_count": "99",
                },
                "video_generation_config": {
                    "enabled": "true",
                    "base_url": "",
                    "api_keys": "xai-key",
                    "model": "",
                    "duration": "99",
                    "aspect_ratio": "",
                    "resolution": "",
                    "timeout_seconds": "1",
                    "request_timeout_seconds": "1",
                    "poll_interval_seconds": "0",
                },
                "voice_generation_config": {
                    "enabled": "true",
                    "smart_switch_enabled": "false",
                    "smart_switch_probability": "135",
                    "proactive_enabled": "yes",
                    "proactive_probability": "135",
                    "group_whitelist": ["10001", None],
                    "group_blacklist": "10002",
                    "private_whitelist": ["123456", ""],
                    "private_blacklist": "654321",
                    "api_url": "",
                    "api_key": "sf-key",
                    "model": "",
                    "voice": "voice-1",
                    "emotion_voice_map": "neutral: voice-neutral\nhappy: voice-happy",
                    "emotion_speed_map": "neutral: 1.05\nhappy: 1.25\nsad: bad",
                    "sample_rate": "999999",
                    "timeout_seconds": "1",
                    "max_retries": "99",
                },
                "web_inspiration_config": {
                    "enabled": "yes",
                    "max_results": "99",
                    "timeout_seconds": "1",
                    "material_prompt": "素材 {keyword}",
                    "outfit_prompt": "穿搭 {keyword}",
                    "week_template_prompt": "周模板 {keyword}",
                    "today_prompt": "今日 {keyword}",
                },
                "weekly_theme_config": {
                    "generation_day": "noday",
                    "generation_time": "99:99",
                    "weight_regular": "bad",
                    "weight_sprint": "-1",
                },
            }
        )

        self.assertEqual(config.schedule_time, "07:00")
        self.assertEqual(config.reference_history_days, 3)
        self.assertEqual(config.history_hours, 24)
        self.assertEqual(config.history_max_count, 1)
        self.assertEqual(config.reference_groups, ["123456"])
        self.assertEqual(config.reference_users, ["654321"])
        self.assertEqual(config.llm_provider, "generation-model")
        self.assertFalse(config.weather.aware_outfit)
        self.assertTrue(config.weather.aware_activity)
        self.assertFalse(config.state.enabled)
        self.assertEqual(config.state.provider, "state-model")
        self.assertEqual(config.state.refresh_minutes, 240)
        self.assertEqual(config.state.quiet_hours, "00:00-06:30")
        self.assertEqual(config.outfit.provider, "outfit-model")
        self.assertEqual(config.invite.provider, "invite-model")
        self.assertEqual(config.memory.provider, "memory-model")
        self.assertEqual(config.memory.min_message_length, 1)
        self.assertEqual(config.memory.max_generation_items, 30)
        self.assertEqual(config.memory.max_injection_items, 0)
        self.assertTrue(config.memos.enabled)
        self.assertEqual(config.memos.base_url, "https://memos.memtensor.cn/api/openmem/v1")
        self.assertEqual(config.memos.api_key, "memos-key")
        self.assertEqual(config.memos.timeout_seconds, 0.5)
        self.assertEqual(config.memos.injection_timeout_seconds, 0.8)
        self.assertEqual(config.memos.memory_limit_number, 20)
        self.assertEqual(config.memos.preference_limit_number, 0)
        self.assertEqual(config.memos.max_context_items, 20)
        self.assertEqual(config.memos.max_context_chars, 120)
        self.assertFalse(config.memos.include_preference)
        self.assertTrue(config.memos.sync_selected_memory)
        self.assertFalse(config.memos.sync_corrections)
        self.assertEqual(config.lifecycle.provider, "review-model")
        self.assertEqual(config.material.provider, "material-model")
        self.assertEqual(config.vision.provider, "vision-model")
        self.assertTrue(config.image_generation.enabled)
        self.assertEqual(len(config.image_generation.channels), 2)
        self.assertEqual(config.image_generation.channels[0].api_url, "https://relay-a.example")
        self.assertEqual(config.image_generation.channels[0].api_key, "relay-key")
        self.assertEqual(config.image_generation.channels[0].model, "gemini-relay-a")
        self.assertEqual(config.image_generation.channels[0].protocol, "gemini")
        self.assertEqual(config.image_generation.channels[0].resolution, "4K")
        self.assertEqual(config.image_generation.channels[0].aspect_ratio, "1:1")
        self.assertEqual(config.image_generation.channels[0].timeout_seconds, 10)
        self.assertEqual(config.image_generation.channels[1].api_url, "https://relay-b.example")
        self.assertEqual(config.image_generation.channels[1].api_key, "relay-key-b")
        self.assertEqual(config.image_generation.channels[1].model, "gpt-image-2")
        self.assertEqual(config.image_generation.channels[1].protocol, "openai")
        self.assertEqual(config.image_generation.channels[1].resolution, "2K")
        self.assertEqual(config.image_generation.channels[1].aspect_ratio, "16:9")
        self.assertEqual(config.image_generation.channels[1].timeout_seconds, 600)
        self.assertEqual(config.image_generation.character_reference_images[0]["path"], "D:/ref/role.png")
        self.assertEqual(config.image_generation.character_reference_images[0]["name"], "正面参考.png")
        self.assertEqual(config.image_generation.character_reference_policy, "off")
        self.assertEqual(config.image_generation.reference_max_count, 12)
        self.assertTrue(config.video_generation.enabled)
        self.assertEqual(config.video_generation.base_url, "")
        self.assertEqual(config.video_generation.api_keys, ["xai-key"])
        self.assertEqual(config.video_generation.model, "grok-imagine-video-1.5-preview")
        self.assertEqual(config.video_generation.duration, 15)
        self.assertEqual(config.video_generation.aspect_ratio, "16:9")
        self.assertEqual(config.video_generation.resolution, "720p")
        self.assertEqual(config.video_generation.timeout_seconds, 30)
        self.assertEqual(config.video_generation.request_timeout_seconds, 10)
        self.assertEqual(config.video_generation.poll_interval_seconds, 1.0)
        self.assertTrue(config.voice_generation.enabled)
        self.assertFalse(config.voice_generation.smart_switch_enabled)
        self.assertEqual(config.voice_generation.smart_switch_probability, 100.0)
        self.assertTrue(config.voice_generation.proactive_enabled)
        self.assertEqual(config.voice_generation.proactive_probability, 100.0)
        self.assertEqual(config.voice_generation.group_whitelist, ["10001"])
        self.assertEqual(config.voice_generation.group_blacklist, ["10002"])
        self.assertEqual(config.voice_generation.private_whitelist, ["123456"])
        self.assertEqual(config.voice_generation.private_blacklist, ["654321"])
        self.assertEqual(config.voice_generation.api_url, "https://api.siliconflow.cn/v1")
        self.assertEqual(config.voice_generation.api_key, "sf-key")
        self.assertEqual(config.voice_generation.model, "FunAudioLLM/CosyVoice2-0.5B")
        self.assertEqual(config.voice_generation.voice, "voice-1")
        self.assertEqual(config.voice_generation.emotion_voice_map["happy"], "voice-happy")
        self.assertEqual(config.voice_generation.emotion_speed_map["happy"], 1.25)
        self.assertEqual(config.voice_generation.emotion_speed_map["sad"], 0.9)
        self.assertEqual(config.voice_generation.timeout_seconds, 5)
        self.assertEqual(config.voice_generation.max_retries, 5)
        self.assertTrue(config.web_inspiration.enabled)
        self.assertEqual(config.web_inspiration.max_results, 8)
        self.assertEqual(config.web_inspiration.timeout_seconds, 3)
        self.assertEqual(config.web_inspiration.material_prompt, "素材 {keyword}")
        self.assertEqual(config.web_inspiration.outfit_prompt, "穿搭 {keyword}")
        self.assertEqual(config.web_inspiration.week_template_prompt, "周模板 {keyword}")
        self.assertEqual(config.web_inspiration.today_prompt, "今日 {keyword}")
        self.assertEqual(config.week_plan_day, "monday")
        self.assertEqual(config.week_plan_time, "06:00")
        self.assertEqual(config.week_template_weights["regular"], 0.3)
        self.assertEqual(config.week_template_weights["sprint"], 0.0)

    def test_memos_defaults_keep_slow_sync_separate_from_chat_injection(self):
        config = LifeSettings.from_dict({})

        self.assertEqual(config.memos.timeout_seconds, 15.0)
        self.assertEqual(config.memos.injection_timeout_seconds, 0.8)
        self.assertEqual(config.state.quiet_hours, "00:00-06:30")

    def test_conf_schema_uses_catalog_workshop_instead_of_content_pool(self):
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertNotIn("imagination_config", schema)
        self.assertEqual(schema["relationship_aliases"]["description"], "本地用户称呼映射")
        self.assertIn("聊天记忆、邀约、备忘录、参考私聊、MemOS 和面板展示", schema["relationship_aliases"]["hint"])
        self.assertNotIn("自定义邀约用户称呼", schema["relationship_aliases"]["description"])
        self.assertNotIn("仅用于覆盖邀约", schema["relationship_aliases"]["hint"])
        commitment_items = schema["commitment_config"]["items"]
        self.assertNotIn("enabled", commitment_items)
        self.assertNotIn("auto_extract", commitment_items)
        for section in ("outfit_config", "invite_config", "material_config", "vision_config"):
            self.assertEqual(schema[section]["items"]["provider"]["_special"], "select_provider")
        self.assertIn("image_generation_config", schema)
        self.assertIn("video_generation_config", schema)
        self.assertIn("voice_generation_config", schema)
        self.assertIn("memos_config", schema)
        self.assertIn("response_gate_config", schema)
        response_gate_items = schema["response_gate_config"]["items"]
        self.assertTrue(response_gate_items["enabled"]["default"])
        self.assertEqual(response_gate_items["group_talk_frequency"]["default"], 0.45)
        self.assertEqual(response_gate_items["private_talk_frequency"]["default"], 0.65)
        self.assertIn("不回复时仍会记录上下文", schema["response_gate_config"]["hint"])
        self.assertFalse(schema["memos_config"]["items"]["enabled"]["default"])
        self.assertEqual(schema["memos_config"]["items"]["base_url"]["default"], "https://memos.memtensor.cn/api/openmem/v1")
        self.assertNotIn("user_id_mode", schema["memos_config"]["items"])
        self.assertNotIn("custom_user_id", schema["memos_config"]["items"])
        self.assertNotIn("include_tool_memory", schema["memos_config"]["items"])
        self.assertNotIn("allow_public", schema["memos_config"]["items"])
        memory_items = schema["memory_config"]["items"]
        self.assertNotIn("enabled", memory_items)
        self.assertNotIn("auto_summarize", memory_items)
        weekly_items = schema["weekly_theme_config"]["items"]
        self.assertNotIn("enabled", weekly_items)
        self.assertIn("generation_day", weekly_items)
        self.assertIn("generation_time", weekly_items)
        self.assertFalse(schema["memos_config"]["items"]["sync_selected_memory"]["default"])
        self.assertTrue(schema["memos_config"]["items"]["sync_corrections"]["default"])
        self.assertEqual(schema["memos_config"]["items"]["timeout_seconds"]["default"], 15)
        self.assertEqual(schema["memos_config"]["items"]["timeout_seconds"]["slider"]["max"], 30)
        self.assertEqual(schema["memos_config"]["items"]["injection_timeout_seconds"]["default"], 0.8)
        self.assertIn("聊天回复前的注入等待由下方单独控制", schema["memos_config"]["items"]["timeout_seconds"]["hint"])
        self.assertIn("MemOS 托管服务", schema["memos_config"]["hint"])
        self.assertFalse(schema["image_generation_config"]["items"]["enabled"]["default"])
        self.assertFalse(schema["video_generation_config"]["items"]["enabled"]["default"])
        self.assertFalse(schema["voice_generation_config"]["items"]["enabled"]["default"])
        image_items = schema["image_generation_config"]["items"]
        self.assertNotIn("api_urls", image_items)
        self.assertNotIn("api_url", image_items)
        self.assertNotIn("api_keys", image_items)
        self.assertNotIn("protocol", image_items)
        self.assertNotIn("model", image_items)
        self.assertNotIn("resolution", image_items)
        self.assertNotIn("aspect_ratio", image_items)
        self.assertNotIn("timeout_seconds", image_items)
        self.assertIn("channels", image_items)
        self.assertEqual(image_items["channels"]["type"], "template_list")
        channel_items = image_items["channels"]["templates"]["gemini"]["items"]
        self.assertNotIn("name", channel_items)
        self.assertNotIn("protocol", channel_items)
        self.assertIn("api_url", channel_items)
        self.assertIn("api_key", channel_items)
        self.assertIn("model", channel_items)
        self.assertEqual(channel_items["model"]["default"], "gemini-3-pro-image-preview")
        self.assertEqual(channel_items["resolution"]["default"], "4K")
        self.assertEqual(channel_items["aspect_ratio"]["default"], "1:1")
        self.assertIn("9:16", channel_items["aspect_ratio"]["options"])
        self.assertIn("16:9", channel_items["aspect_ratio"]["options"])
        self.assertEqual(channel_items["timeout_seconds"]["default"], 120)
        self.assertLess(list(channel_items).index("model"), list(channel_items).index("resolution"))
        self.assertLess(list(channel_items).index("resolution"), list(channel_items).index("aspect_ratio"))
        self.assertLess(list(channel_items).index("aspect_ratio"), list(channel_items).index("timeout_seconds"))
        openai_channel_items = image_items["channels"]["templates"]["openai"]["items"]
        self.assertNotIn("protocol", openai_channel_items)
        self.assertEqual(openai_channel_items["api_url"]["default"], "")
        self.assertEqual(openai_channel_items["model"]["default"], "gpt-image-2")
        self.assertEqual(openai_channel_items["resolution"]["default"], "4K")
        self.assertEqual(openai_channel_items["aspect_ratio"]["default"], "1:1")
        self.assertEqual(openai_channel_items["timeout_seconds"]["default"], 120)
        self.assertNotIn("character_reference_enabled", schema["image_generation_config"]["items"])
        self.assertNotIn("character_reference_image", schema["image_generation_config"]["items"])
        self.assertIn("character_reference_images", schema["image_generation_config"]["items"])
        self.assertEqual(
            schema["image_generation_config"]["items"]["character_reference_images"]["_special"],
            "character_reference_gallery",
        )
        self.assertIn("character_reference_policy", schema["image_generation_config"]["items"])
        self.assertLess(
            list(image_items).index("character_reference_policy"),
            list(image_items).index("character_reference_images"),
        )
        self.assertLess(list(image_items).index("reference_max_count"), list(image_items).index("channels"))
        self.assertEqual(
            schema["image_generation_config"]["items"]["character_reference_policy"]["description"],
            "角色形象参考",
        )
        self.assertIn(
            "图片生成会把下方角色形象参考图提供给模型",
            schema["image_generation_config"]["items"]["character_reference_policy"]["hint"],
        )
        self.assertEqual(schema["image_generation_config"]["items"]["character_reference_policy"]["default"], "off")
        self.assertEqual(
            schema["image_generation_config"]["items"]["character_reference_policy"]["options"],
            ["off", "auto", "always"],
        )
        self.assertEqual(
            schema["image_generation_config"]["items"]["character_reference_policy"]["option_labels"],
            {"off": "不使用", "auto": "智能判断", "always": "始终参考"},
        )
        self.assertNotIn("reference_max_mb", schema["image_generation_config"]["items"])
        self.assertNotIn("use_proxy", schema["image_generation_config"]["items"])
        self.assertNotIn("proxy_url", schema["image_generation_config"]["items"])
        self.assertIn("reference_max_count", schema["image_generation_config"]["items"])
        self.assertEqual(schema["video_generation_config"]["items"]["model"]["default"], "grok-imagine-video-1.5-preview")
        self.assertEqual(schema["voice_generation_config"]["items"]["model"]["default"], "FunAudioLLM/CosyVoice2-0.5B")
        self.assertIn("smart_switch_enabled", schema["voice_generation_config"]["items"])
        self.assertTrue(schema["voice_generation_config"]["items"]["smart_switch_enabled"]["default"])
        self.assertIn("smart_switch_probability", schema["voice_generation_config"]["items"])
        self.assertEqual(schema["voice_generation_config"]["items"]["smart_switch_probability"]["default"], 35)
        self.assertEqual(schema["voice_generation_config"]["items"]["smart_switch_probability"]["slider"]["max"], 100)
        self.assertIn("proactive_probability", schema["voice_generation_config"]["items"])
        self.assertEqual(
            schema["voice_generation_config"]["items"]["proactive_enabled"]["description"],
            "闲时消息优先语音",
        )
        self.assertIn(
            "闲时回复和私聊回访",
            schema["voice_generation_config"]["items"]["proactive_enabled"]["hint"],
        )
        self.assertEqual(schema["voice_generation_config"]["items"]["proactive_probability"]["default"], 100)
        self.assertEqual(schema["voice_generation_config"]["items"]["proactive_probability"]["slider"]["max"], 100)
        self.assertIn(
            "闲时回复或私聊回访",
            schema["voice_generation_config"]["items"]["proactive_probability"]["hint"],
        )
        for key in ("group_whitelist", "group_blacklist", "private_whitelist", "private_blacklist"):
            self.assertIn(key, schema["voice_generation_config"]["items"])
            self.assertEqual(schema["voice_generation_config"]["items"][key]["type"], "list")
        self.assertNotIn("emotion_keywords", schema["voice_generation_config"]["items"])
        self.assertIn("emotion_voice_map", schema["voice_generation_config"]["items"])
        self.assertIn("emotion_speed_map", schema["voice_generation_config"]["items"])
        self.assertNotIn("format", schema["voice_generation_config"]["items"])
        self.assertNotIn("speed", schema["voice_generation_config"]["items"])
        self.assertNotIn("gain", schema["voice_generation_config"]["items"])
        self.assertNotIn("sample_rate", schema["voice_generation_config"]["items"])
        self.assertEqual(schema["vision_config"]["items"]["provider"]["description"], "视觉信息")
        self.assertNotIn("auto_update_outfit", schema["rhythm_config"]["items"])
        self.assertIn("后台状态与穿搭巡检", schema["state_config"]["hint"])
        self.assertEqual(schema["state_config"]["items"]["enabled"]["description"], "启用实时状态")
        self.assertNotIn("update_on_chat", schema["state_config"]["items"])
        self.assertNotIn("update_on_invite", schema["state_config"]["items"])
        self.assertIn("quiet_hours", schema["state_config"]["items"])
        self.assertEqual(schema["state_config"]["items"]["quiet_hours"]["description"], "静默时段")
        self.assertEqual(schema["state_config"]["items"]["quiet_hours"]["default"], "00:00-06:30")
        self.assertIn("00:00-06:30", schema["state_config"]["items"]["quiet_hours"]["hint"])
        lifecycle_items = schema["lifecycle_config"]["items"]
        self.assertNotIn("enabled", lifecycle_items)
        self.assertNotIn("review_enabled", lifecycle_items)
        self.assertNotIn("preference_learning", lifecycle_items)
        self.assertNotIn("life_events_enabled", lifecycle_items)
        self.assertNotIn("sleep_debt_enabled", lifecycle_items)
        self.assertEqual(lifecycle_items["provider"]["_special"], "select_provider")
        web_items = schema["web_inspiration_config"]["items"]
        self.assertIn("material_prompt", web_items)
        self.assertIn("outfit_prompt", web_items)
        self.assertIn("week_template_prompt", web_items)
        self.assertIn("today_prompt", web_items)
        self.assertIn("普通配置-AI配置-网页搜索配置", schema["web_inspiration_config"]["hint"])
        self.assertEqual(web_items["material_prompt"]["default"], DEFAULT_WEB_MATERIAL_PROMPT)
        self.assertEqual(web_items["outfit_prompt"]["default"], DEFAULT_WEB_OUTFIT_PROMPT)
        self.assertEqual(web_items["week_template_prompt"]["default"], DEFAULT_WEB_WEEK_TEMPLATE_PROMPT)
        self.assertEqual(web_items["today_prompt"]["default"], DEFAULT_WEB_TODAY_PROMPT)
        self.assertIn("生活场景", web_items["material_prompt"]["default"])
        self.assertNotIn("像猫咪一样的宅家日", web_items["material_prompt"]["default"])
        self.assertIn("活动场景", web_items["outfit_prompt"]["default"])
        self.assertNotIn("元气休闲风", web_items["outfit_prompt"]["default"])
        self.assertIn("工作日与周末节奏", web_items["week_template_prompt"]["default"])
        self.assertNotIn("软绵绵治愈周", web_items["week_template_prompt"]["default"])
        self.assertIn("季节天气", web_items["today_prompt"]["default"])
        self.assertIn("出行或居家状态", web_items["today_prompt"]["default"])
        self.assertNotIn("拥抱阳光的元气出游", web_items["today_prompt"]["default"])

    def test_catalog_settings_use_builtin_materials(self):
        config = LifeSettings.from_dict({})

        self.assertEqual(config.catalog.daily_themes, DEFAULT_DAILY_THEMES)
        self.assertEqual(config.catalog.mood_colors, DEFAULT_MOOD_COLORS)
        self.assertEqual(config.catalog.outfit_styles, DEFAULT_OUTFIT_STYLES)
        self.assertEqual(config.catalog.sleep_styles, DEFAULT_SLEEP_STYLES)
        self.assertEqual(config.catalog.schedule_types, DEFAULT_SCHEDULE_TYPES)
        self.assertEqual(config.catalog.night_hairstyles, DEFAULT_NIGHT_HAIRSTYLES)
        self.assertEqual(config.catalog.style_to_hair_map, DEFAULT_STYLE_TO_HAIR_MAP)

    def test_state_refresh_minutes_has_lower_bound(self):
        config = LifeSettings.from_dict({"state_config": {"refresh_minutes": "1"}})

        self.assertEqual(config.state.refresh_minutes, 5)

    def test_default_chat_prompt_avoids_unfounded_gender_guessing(self):
        config = LifeSettings.from_dict({})

        self.assertIn("人物称呼、性别和关系必须以人设线索为准", config.chat_prompt)
        self.assertIn("从当前角色人设中提取到的对方线索与已保存关系叙事", config.chat_prompt)
        self.assertIn("不要根据昵称、头像、平台标识、语气、表情、刻板印象或上下文习惯猜测性别", config.chat_prompt)
        self.assertIn("没有明确性别依据时", config.chat_prompt)

    def test_story_engine_rules_are_configurable(self):
        config = LifeSettings.from_dict(
            {
                "story_engine_config": {
                    "state_rules": "状态要偏疲惫",
                    "timeline_rules": "时间轴要更宅家",
                    "world_rules": "地点只记录真实去过的地方",
                    "chat_rules": "男性朋友不能写成闺蜜",
                }
            }
        )

        self.assertEqual(config.state_prompt, "状态要偏疲惫")
        self.assertEqual(config.timeline_prompt, "时间轴要更宅家")
        self.assertEqual(config.world_prompt, "地点只记录真实去过的地方")
        self.assertEqual(config.chat_prompt, "男性朋友不能写成闺蜜")

    def test_storage_settings_parse_retention_days(self):
        config = LifeSettings.from_dict(
            {
                "storage_config": {
                    "daily_keep_days": "45",
                    "review_keep_days": "bad",
                    "memory_keep_days": "365",
                    "planning_keep_days": "-1",
                    "workshop_keep_days": "99999",
                }
            }
        )

        self.assertEqual(config.storage.daily_keep_days, 45)
        self.assertEqual(config.storage.review_keep_days, 120)
        self.assertEqual(config.storage.memory_keep_days, 365)
        self.assertEqual(config.storage.planning_keep_days, 0)
        self.assertEqual(config.storage.workshop_keep_days, 3650)

