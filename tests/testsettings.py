import json
import unittest

from support import (
    LifeSettings,
    PLUGIN_ROOT,
)
from core.prompts import DEFAULT_WEB_TODAY_PROMPT


class LifeSettingsTest(unittest.TestCase):
    def test_config_parsing_tolerates_bad_types(self):
        config = LifeSettings.from_dict(
            {
                "bot_identity_aliases": ["小助手", " @助手 ", None],
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
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {
                    "collect_chat_emojis": "off",
                    "max_ready": "9999",
                    "replace_when_full": "false",
                    "max_size_mb": "999",
                    "send_candidate_limit": "999",
                    "review_batch_size": "99",
                    "inactive_record_keep_days": "999",
                    "orphan_cache_grace_hours": "999",
                },
                "sight_config": {
                    "summary_provider": "summary-model",
                    "frame_provider": "frame-model",
                    "total_timeout_seconds": "9999",
                    "max_transcript_chars": "99999",
                    "max_frames": "99",
                    "video_download_max_mb": "9999",
                    "video_download_timeout_seconds": "9999",
                    "video_cache_ttl_hours": "999",
                    "video_cache_max_items": "9999",
                    "sight_cache_keep_days": "99",
                    "audio_transcript_mode": "local",
                    "local_asr_timeout_seconds": "99999",
                    "audio_timeout_seconds": "9999",
                    "note_max_transcript_chars": "999999",
                    "bili_auto_summary": "false",
                    "bili_resolve_timeout_seconds": "99",
                },
                "image_generation_config": {
                    "enabled": "yes",
                    "prompt_rewrite_provider": "rewrite-model",
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay-a.example/",
                            "api_key": "relay-key",
                            "model": "gemini-relay-a",
                            "resolution": "",
                            "aspect_ratio": "",
                            "timeout_seconds": "1",
                        },
                        "bad-line",
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay-c.example",
                            "api_key": "",
                        },
                    ],
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://relay-b.example",
                            "api_key": "relay-key-b",
                            "model": "",
                            "resolution": "2k",
                            "aspect_ratio": "16:9",
                            "timeout_seconds": "9999",
                        },
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay-d.example",
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
                    "today_prompt": "今日 {keyword}",
                },
                "chat_style_config": {
                    "casual_short_prompt": " 自然短一点，认真事先给结论。 ",
                    "casual_max_chars": "999",
                    "group_casual_max_chars": "1",
                    "private_casual_max_chars": "999",
                    "proactive_max_chars": "999",
                },
            }
        )

        self.assertEqual(config.bot_identity_aliases, ["小助手", "@助手"])
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
        self.assertEqual(config.vision.provider, "vision-model")
        self.assertFalse(config.emoji.collect_chat_emojis)
        self.assertEqual(config.emoji.max_ready, 300)
        self.assertFalse(config.emoji.replace_when_full)
        self.assertEqual(config.emoji.max_size_mb, 20.0)
        self.assertEqual(config.emoji.send_candidate_limit, 50)
        self.assertEqual(config.emoji.review_batch_size, 10)
        self.assertEqual(config.emoji.inactive_record_keep_days, 90)
        self.assertEqual(config.emoji.orphan_cache_grace_hours, 168)
        self.assertEqual(config.sight.summary_provider, "summary-model")
        self.assertEqual(config.sight.frame_provider, "frame-model")
        self.assertEqual(config.sight.total_timeout_seconds, 1800)
        self.assertEqual(config.sight.max_transcript_chars, 30000)
        self.assertEqual(config.sight.max_frames, 24)
        self.assertEqual(config.sight.video_download_max_mb, 1024)
        self.assertEqual(config.sight.video_download_timeout_seconds, 600)
        self.assertEqual(config.sight.video_cache_ttl_hours, 168)
        self.assertEqual(config.sight.video_cache_max_items, 500)
        self.assertEqual(config.sight.sight_cache_keep_days, 30)
        self.assertEqual(config.sight.audio_transcript_mode, "local")
        self.assertFalse(hasattr(config.sight, "local_asr_batch_size_s"))
        self.assertEqual(config.sight.local_asr_timeout_seconds, 3600)
        self.assertTrue(config.image_generation.enabled)
        self.assertEqual(config.image_generation.prompt_rewrite_provider, "rewrite-model")
        self.assertEqual(len(config.image_generation.text_channels), 1)
        self.assertEqual(config.image_generation.text_channels[0].api_url, "https://relay-a.example")
        self.assertEqual(config.image_generation.text_channels[0].api_key, "relay-key")
        self.assertEqual(config.image_generation.text_channels[0].model, "gemini-relay-a")
        self.assertEqual(config.image_generation.text_channels[0].protocol, "gemini")
        self.assertEqual(config.image_generation.text_channels[0].resolution, "4K")
        self.assertEqual(config.image_generation.text_channels[0].aspect_ratio, "1:1")
        self.assertEqual(config.image_generation.text_channels[0].timeout_seconds, 10)
        self.assertEqual(len(config.image_generation.edit_channels), 1)
        self.assertEqual(config.image_generation.edit_channels[0].api_url, "https://relay-b.example")
        self.assertEqual(config.image_generation.edit_channels[0].api_key, "relay-key-b")
        self.assertEqual(config.image_generation.edit_channels[0].model, "gpt-image-2")
        self.assertEqual(config.image_generation.edit_channels[0].protocol, "openai")
        self.assertEqual(config.image_generation.edit_channels[0].resolution, "2K")
        self.assertEqual(config.image_generation.edit_channels[0].aspect_ratio, "16:9")
        self.assertEqual(config.image_generation.edit_channels[0].timeout_seconds, 600)
        self.assertEqual(config.image_generation.character_reference_images[0]["path"], "D:/ref/role.png")
        self.assertEqual(config.image_generation.character_reference_images[0]["name"], "正面参考.png")
        self.assertEqual(config.image_generation.character_reference_policy, "off")
        self.assertEqual(config.image_generation.reference_max_count, 12)
        self.assertTrue(config.video_generation.enabled)
        self.assertEqual(config.video_generation.base_url, "")
        self.assertEqual(config.video_generation.api_keys, ["xai-key"])
        self.assertEqual(config.video_generation.model, "grok-imagine-video-1.5-preview")
        self.assertEqual(config.video_generation.duration, 15)
        self.assertEqual(config.video_generation.aspect_ratio, "1:1")
        self.assertEqual(config.video_generation.resolution, "720p")
        self.assertEqual(config.video_generation.timeout_seconds, 30)
        self.assertEqual(config.video_generation.request_timeout_seconds, 10)
        self.assertEqual(config.video_generation.poll_interval_seconds, 1.0)
        self.assertTrue(config.voice_generation.enabled)
        self.assertFalse(config.voice_generation.smart_switch_enabled)
        self.assertEqual(config.voice_generation.smart_switch_probability, 100.0)
        self.assertTrue(config.voice_generation.proactive_enabled)
        self.assertEqual(config.voice_generation.proactive_probability, 100.0)
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
        self.assertEqual(config.web_inspiration.today_prompt, "今日 {keyword}")
        self.assertEqual(config.chat_style.casual_short_prompt, "自然短一点，认真事先给结论。")
        self.assertFalse(hasattr(config.chat_style, "natural_segmented_reply"))
        self.assertFalse(hasattr(config.chat_style, "natural_segment_max_segments"))
        self.assertFalse(hasattr(config.chat_style, "natural_segment_delay_range"))
        self.assertFalse(hasattr(config.chat_style, "natural_segment_pattern"))
        self.assertEqual(config.chat_style.casual_max_chars, 50)
        self.assertEqual(config.chat_style.group_casual_max_chars, 10)
        self.assertEqual(config.chat_style.private_casual_max_chars, 30)
        self.assertEqual(config.chat_style.proactive_max_chars, 30)
        self.assertFalse(hasattr(config.chat_style, "fact_check_query_prompt"))
        self.assertFalse(config.sight.bili_auto_summary)

    def test_chat_style_accepts_compact_limits(self):
        config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 15,
                    "group_casual_max_chars": 15,
                    "private_casual_max_chars": 15,
                    "proactive_max_chars": 15,
                }
            }
        )

        self.assertEqual(config.chat_style.casual_max_chars, 15)
        self.assertEqual(config.chat_style.group_casual_max_chars, 15)
        self.assertEqual(config.chat_style.private_casual_max_chars, 15)
        self.assertEqual(config.chat_style.proactive_max_chars, 15)

    def test_video_ratio_follows_primary_image_channel(self):
        config = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://image.example",
                            "api_key": "image-key",
                            "aspect_ratio": "2:3",
                        }
                    ],
                    "edit_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://edit.example",
                            "api_key": "edit-key",
                            "aspect_ratio": "16:9",
                        }
                    ],
                },
                "video_generation_config": {
                    "enabled": True,
                    "aspect_ratio": "16:9",
                },
            }
        )

        self.assertEqual(config.video_generation.aspect_ratio, "2:3")

    def test_video_ratio_uses_edit_image_channel_when_text_channel_missing(self):
        config = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "edit_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://edit.example",
                            "api_key": "edit-key",
                            "aspect_ratio": "3:4",
                        }
                    ],
                },
                "video_generation_config": {
                    "enabled": True,
                    "aspect_ratio": "16:9",
                },
            }
        )

        self.assertEqual(config.video_generation.aspect_ratio, "3:4")

    def test_memos_defaults_keep_slow_sync_separate_from_chat_injection(self):
        config = LifeSettings.from_dict({})

        self.assertEqual(config.memos.timeout_seconds, 15.0)
        self.assertEqual(config.memos.injection_timeout_seconds, 0.8)
        self.assertEqual(config.state.quiet_hours, "00:00-06:30")
        self.assertEqual(config.sight.video_download_max_mb, 500)
        self.assertEqual(config.sight.video_download_timeout_seconds, 240)
        self.assertEqual(config.sight.audio_transcript_mode, "local")
        self.assertTrue(config.emoji.collect_chat_emojis)
        self.assertEqual(config.emoji.max_ready, 128)
        self.assertTrue(config.emoji.replace_when_full)
        self.assertEqual(config.emoji.max_size_mb, 5.0)
        self.assertEqual(config.emoji.send_candidate_limit, 30)
        self.assertEqual(config.emoji.review_batch_size, 3)
        self.assertEqual(config.emoji.inactive_record_keep_days, 30)
        self.assertEqual(config.emoji.orphan_cache_grace_hours, 24)

    def test_sight_video_download_limit_can_be_disabled(self):
        config = LifeSettings.from_dict({"sight_config": {"video_download_max_mb": 0}})

        self.assertEqual(config.sight.video_download_max_mb, 0)

    def test_conf_schema_no_longer_exposes_catalog_workshop(self):
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertNotIn("imagination_config", schema)
        self.assertIn("bot_identity_aliases", schema)
        self.assertEqual(schema["bot_identity_aliases"]["description"], "角色身份别名")
        self.assertIn("唤醒", schema["bot_identity_aliases"]["hint"])
        self.assertIn("角色名、昵称或短称", schema["bot_identity_aliases"]["hint"])
        self.assertEqual(schema["relationship_aliases"]["description"], "本地用户称呼映射")
        self.assertIn("聊天记忆、邀约、备忘录、参考私聊、MemOS 和面板展示", schema["relationship_aliases"]["hint"])
        self.assertNotIn("自定义邀约用户称呼", schema["relationship_aliases"]["description"])
        self.assertNotIn("仅用于覆盖邀约", schema["relationship_aliases"]["hint"])
        commitment_items = schema["commitment_config"]["items"]
        self.assertNotIn("enabled", commitment_items)
        self.assertNotIn("auto_extract", commitment_items)
        for section in ("outfit_config", "invite_config", "vision_config"):
            self.assertEqual(schema[section]["items"]["provider"]["_special"], "select_provider")
        self.assertNotIn("material_config", schema)
        self.assertIn("image_generation_config", schema)
        self.assertIn("video_generation_config", schema)
        self.assertIn("voice_generation_config", schema)
        self.assertIn("sight_config", schema)
        sight_items = schema["sight_config"]["items"]
        self.assertEqual(schema["sight_config"]["description"], "视频理解")
        self.assertNotIn("provider", sight_items)
        self.assertEqual(sight_items["summary_provider"]["_special"], "select_provider")
        self.assertEqual(sight_items["frame_provider"]["_special"], "select_provider")
        self.assertIn("使用当前默认模型", sight_items["summary_provider"]["hint"])
        self.assertIn("使用视觉信息模型", sight_items["frame_provider"]["hint"])
        self.assertEqual(sight_items["total_timeout_seconds"]["default"], 300)
        self.assertEqual(sight_items["max_transcript_chars"]["default"], 8000)
        self.assertEqual(sight_items["max_frames"]["default"], 8)
        self.assertEqual(sight_items["video_download_max_mb"]["default"], 500)
        self.assertEqual(sight_items["video_download_max_mb"]["slider"]["min"], 0)
        self.assertEqual(sight_items["video_download_max_mb"]["slider"]["max"], 1024)
        self.assertEqual(sight_items["video_download_max_mb"]["slider"]["step"], 1)
        self.assertIn("填 0 表示不限制大小", sight_items["video_download_max_mb"]["hint"])
        self.assertEqual(sight_items["video_download_timeout_seconds"]["default"], 240)
        self.assertEqual(sight_items["video_download_timeout_seconds"]["slider"]["min"], 30)
        self.assertEqual(sight_items["video_download_timeout_seconds"]["slider"]["max"], 600)
        self.assertEqual(sight_items["video_download_timeout_seconds"]["slider"]["step"], 30)
        self.assertIn("只影响画面抽帧", sight_items["video_download_timeout_seconds"]["hint"])
        self.assertEqual(sight_items["video_cache_ttl_hours"]["default"], 2)
        self.assertEqual(sight_items["video_cache_max_items"]["default"], 60)
        self.assertEqual(sight_items["video_cache_max_items"]["slider"]["min"], 8)
        self.assertEqual(sight_items["video_cache_max_items"]["slider"]["max"], 500)
        self.assertEqual(sight_items["sight_cache_keep_days"]["default"], 7)
        self.assertEqual(
            sight_items["sight_cache_keep_days"]["hint"],
            "抽帧、下载视频、音频、转写音频和转写结果的保留天数。",
        )
        self.assertEqual(sight_items["audio_transcript_mode"]["default"], "local")
        self.assertEqual(sight_items["audio_transcript_mode"]["options"], ["bcut", "local"])
        self.assertEqual(
            sight_items["audio_transcript_mode"]["option_labels"],
            {"bcut": "必剪", "local": "本地ASR"},
        )
        self.assertIn("默认优先使用本地ASR", sight_items["audio_transcript_mode"]["hint"])
        self.assertIn("自动切换到必剪", sight_items["audio_transcript_mode"]["hint"])
        self.assertNotIn("local_asr_device", sight_items)
        self.assertNotIn("local_asr_max_concurrent", sight_items)
        self.assertNotIn("local_asr_batch_size_s", sight_items)
        self.assertEqual(sight_items["local_asr_timeout_seconds"]["default"], 900)
        self.assertNotIn("local_asr_auto_prepare", sight_items)
        self.assertNotIn("local_asr_model_dir", sight_items)
        self.assertEqual(sight_items["note_max_transcript_chars"]["default"], 20000)
        self.assertTrue(sight_items["bili_auto_summary"]["default"])
        self.assertEqual(sight_items["bili_auto_summary"]["type"], "bool")
        self.assertNotIn("timeout_seconds", sight_items)
        self.assertNotIn("audio_timeout_seconds", sight_items)
        self.assertNotIn("bili_resolve_timeout_seconds", sight_items)
        self.assertNotIn("BiliNote", schema["sight_config"]["hint"])
        self.assertNotIn("bilinote_base_url", sight_items)
        self.assertIn("memos_config", schema)
        self.assertIn("response_gate_config", schema)
        storage_items = schema["storage_config"]["items"]
        self.assertEqual(storage_items["generated_media_keep_days"]["default"], 30)
        self.assertEqual(storage_items["generated_media_keep_days"]["slider"]["min"], 0)
        self.assertIn("generated/images", storage_items["generated_media_keep_days"]["hint"])
        self.assertEqual(storage_items["reverse_cache_keep_days"]["default"], 7)
        self.assertIn("图片反推原图缓存", storage_items["reverse_cache_keep_days"]["hint"])
        response_gate_items = schema["response_gate_config"]["items"]
        self.assertTrue(response_gate_items["enabled"]["default"])
        self.assertEqual(response_gate_items["group_talk_frequency"]["default"], 0.45)
        self.assertEqual(response_gate_items["private_talk_frequency"]["default"], 0.65)
        self.assertIn("不回复时仍会记录上下文", schema["response_gate_config"]["hint"])
        self.assertIn("chat_style_config", schema)
        chat_style_items = schema["chat_style_config"]["items"]
        self.assertEqual(schema["chat_style_config"]["description"], "聊天表达")
        self.assertEqual(schema["chat_style_config"]["type"], "object")
        self.assertNotIn("natural_segmented_reply", chat_style_items)
        self.assertNotIn("natural_segment_max_segments", chat_style_items)
        self.assertNotIn("natural_segment_delay_range", chat_style_items)
        self.assertNotIn("natural_segment_pattern", chat_style_items)
        self.assertEqual(chat_style_items["casual_max_chars"]["default"], 50)
        self.assertEqual(chat_style_items["group_casual_max_chars"]["default"], 30)
        self.assertEqual(chat_style_items["private_casual_max_chars"]["default"], 15)
        self.assertEqual(chat_style_items["proactive_max_chars"]["default"], 15)
        for key in (
            "enabled",
            "reply_postprocess_enabled",
            "fact_check_hint_enabled",
            "fact_check_search_enabled",
            "style_trace_enabled",
        ):
            self.assertNotIn(key, chat_style_items)
        self.assertNotIn("fact_check_query_prompt", chat_style_items)
        self.assertEqual(chat_style_items["casual_max_chars"]["slider"]["min"], 10)
        self.assertEqual(chat_style_items["group_casual_max_chars"]["slider"]["min"], 10)
        self.assertEqual(chat_style_items["private_casual_max_chars"]["slider"]["min"], 10)
        self.assertEqual(chat_style_items["proactive_max_chars"]["slider"]["min"], 10)
        self.assertEqual(chat_style_items["casual_max_chars"]["slider"]["max"], 50)
        self.assertEqual(chat_style_items["group_casual_max_chars"]["slider"]["max"], 30)
        self.assertEqual(chat_style_items["private_casual_max_chars"]["slider"]["max"], 30)
        self.assertEqual(chat_style_items["proactive_max_chars"]["slider"]["max"], 30)
        self.assertIn("全局和场景两项里较小的数值", chat_style_items["casual_max_chars"]["hint"])
        self.assertIn("不硬截断", chat_style_items["casual_max_chars"]["hint"])
        self.assertNotIn("LLM", chat_style_items["casual_max_chars"]["hint"])
        self.assertNotIn("后处理", chat_style_items["group_casual_max_chars"]["hint"])
        self.assertNotIn("硬上限", chat_style_items["group_casual_max_chars"]["hint"])
        self.assertNotIn("硬上限", chat_style_items["private_casual_max_chars"]["hint"])
        self.assertIn("不要写具体场景示例", chat_style_items["casual_short_prompt"]["hint"])
        self.assertNotIn("比如", chat_style_items["casual_short_prompt"]["hint"])
        self.assertNotIn("例如", chat_style_items["casual_short_prompt"]["hint"])
        self.assertFalse(schema["memos_config"]["items"]["enabled"]["default"])
        self.assertEqual(schema["memos_config"]["items"]["base_url"]["default"], "https://memos.memtensor.cn/api/openmem/v1")
        self.assertNotIn("user_id_mode", schema["memos_config"]["items"])
        self.assertNotIn("custom_user_id", schema["memos_config"]["items"])
        self.assertNotIn("include_tool_memory", schema["memos_config"]["items"])
        self.assertNotIn("allow_public", schema["memos_config"]["items"])
        memory_items = schema["memory_config"]["items"]
        self.assertNotIn("enabled", memory_items)
        self.assertNotIn("auto_summarize", memory_items)
        self.assertNotIn("weekly_theme_config", schema)
        story_items = schema["story_engine_config"]["items"]
        self.assertNotIn("outfit_morning", story_items)
        self.assertNotIn("outfit_daytime", story_items)
        self.assertNotIn("outfit_night", story_items)
        self.assertFalse(schema["memos_config"]["items"]["sync_selected_memory"]["default"])
        self.assertTrue(schema["memos_config"]["items"]["sync_corrections"]["default"])
        self.assertEqual(schema["memos_config"]["items"]["timeout_seconds"]["default"], 15)
        self.assertEqual(schema["memos_config"]["items"]["timeout_seconds"]["slider"]["max"], 30)
        self.assertEqual(schema["memos_config"]["items"]["injection_timeout_seconds"]["default"], 0.8)
        self.assertIn("聊天回复前的注入等待由下方单独控制", schema["memos_config"]["items"]["timeout_seconds"]["hint"])
        self.assertIn("MemOS 托管服务", schema["memos_config"]["hint"])
        self.assertEqual(schema["storage_config"]["description"], "数据管理")
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
        self.assertNotIn("channels", image_items)
        self.assertIn("prompt_rewrite_provider", image_items)
        self.assertEqual(image_items["prompt_rewrite_provider"]["description"], "轻量润色")
        self.assertEqual(image_items["prompt_rewrite_provider"]["_special"], "select_provider")
        self.assertIn("安全拒绝", image_items["prompt_rewrite_provider"]["hint"])
        self.assertIn("text_channels", image_items)
        self.assertIn("edit_channels", image_items)
        self.assertEqual(image_items["text_channels"]["description"], "文生图接口通道")
        self.assertEqual(image_items["edit_channels"]["description"], "图生图接口通道")
        self.assertEqual(image_items["text_channels"]["type"], "template_list")
        self.assertEqual(image_items["edit_channels"]["type"], "template_list")
        self.assertIn("无参考图", image_items["text_channels"]["hint"])
        self.assertIn("引用图片", image_items["edit_channels"]["hint"])
        channel_items = image_items["text_channels"]["templates"]["gemini"]["items"]
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
        openai_channel_items = image_items["edit_channels"]["templates"]["openai"]["items"]
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
            list(image_items).index("prompt_rewrite_provider"),
            list(image_items).index("character_reference_policy"),
        )
        self.assertLess(
            list(image_items).index("character_reference_policy"),
            list(image_items).index("character_reference_images"),
        )
        self.assertLess(list(image_items).index("reference_max_count"), list(image_items).index("text_channels"))
        self.assertLess(list(image_items).index("text_channels"), list(image_items).index("edit_channels"))
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
        video_items = schema["video_generation_config"]["items"]
        self.assertNotIn("aspect_ratio", video_items)
        self.assertEqual(video_items["model"]["default"], "grok-imagine-video-1.5-preview")
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
        self.assertNotIn("emotion_keywords", schema["voice_generation_config"]["items"])
        self.assertIn("emotion_voice_map", schema["voice_generation_config"]["items"])
        self.assertIn("emotion_speed_map", schema["voice_generation_config"]["items"])
        self.assertNotIn("format", schema["voice_generation_config"]["items"])
        self.assertNotIn("speed", schema["voice_generation_config"]["items"])
        self.assertNotIn("gain", schema["voice_generation_config"]["items"])
        self.assertNotIn("sample_rate", schema["voice_generation_config"]["items"])
        self.assertEqual(schema["vision_config"]["items"]["provider"]["description"], "视觉信息")
        self.assertIn("emoji_config", schema)
        emoji_items = schema["emoji_config"]["items"]
        self.assertEqual(schema["emoji_config"]["description"], "表情库")
        self.assertEqual(emoji_items["collect_chat_emojis"]["description"], "自动收集表情")
        self.assertEqual(emoji_items["max_ready"]["description"], "表情数量上限")
        self.assertEqual(emoji_items["max_size_mb"]["description"], "表情大小上限（MB）")
        self.assertEqual(emoji_items["send_candidate_limit"]["description"], "表情匹配候选数")
        self.assertTrue(emoji_items["collect_chat_emojis"]["default"])
        self.assertEqual(emoji_items["max_ready"]["default"], 128)
        self.assertEqual(emoji_items["max_ready"]["slider"]["max"], 300)
        self.assertTrue(emoji_items["replace_when_full"]["default"])
        self.assertEqual(emoji_items["max_size_mb"]["default"], 5)
        self.assertEqual(emoji_items["max_size_mb"]["slider"]["min"], 1)
        self.assertEqual(emoji_items["max_size_mb"]["slider"]["max"], 20)
        self.assertEqual(emoji_items["send_candidate_limit"]["default"], 30)
        self.assertEqual(emoji_items["send_candidate_limit"]["slider"]["max"], 50)
        self.assertEqual(emoji_items["review_batch_size"]["default"], 3)
        self.assertEqual(emoji_items["inactive_record_keep_days"]["default"], 30)
        self.assertEqual(emoji_items["inactive_record_keep_days"]["slider"]["max"], 90)
        self.assertEqual(emoji_items["orphan_cache_grace_hours"]["default"], 24)
        self.assertIn("前台手动导入", emoji_items["collect_chat_emojis"]["hint"])
        self.assertIn("不代表一次发送这么多", emoji_items["send_candidate_limit"]["hint"])
        self.assertEqual(
            emoji_items["max_size_mb"]["hint"],
            "自动收集、前台导入和备份还原时允许的单个表情文件大小，需要较大 GIF 时可手动调高。",
        )
        self.assertNotIn("不限制", emoji_items["max_size_mb"]["hint"])
        self.assertIn("已就绪且可发送", emoji_items["max_ready"]["hint"])
        self.assertIn("待识别、待审核、识别失败、已拒绝、文件缺失", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("ready", emoji_items["max_ready"]["hint"])
        self.assertNotIn("pending", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("reviewing", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("failed", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("rejected", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("missing", emoji_items["inactive_record_keep_days"]["hint"])
        self.assertNotIn("auto_update_outfit", schema["rhythm_config"]["items"])
        self.assertIn("夜间复盘", schema["state_config"]["hint"])
        self.assertIn("偏好参考上限", schema["state_config"]["hint"])
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
        self.assertNotIn("material_prompt", web_items)
        self.assertNotIn("outfit_prompt", web_items)
        self.assertNotIn("week_template_prompt", web_items)
        self.assertIn("today_prompt", web_items)
        self.assertIn("AI", schema["web_inspiration_config"]["hint"])
        self.assertNotIn("素材工坊", schema["web_inspiration_config"]["hint"])
        self.assertEqual(web_items["today_prompt"]["default"], DEFAULT_WEB_TODAY_PROMPT)
        self.assertIn("季节天气", web_items["today_prompt"]["default"])
        self.assertIn("出行或居家状态", web_items["today_prompt"]["default"])
        self.assertNotIn("拥抱阳光的元气出游", web_items["today_prompt"]["default"])

    def test_state_refresh_minutes_has_lower_bound(self):
        config = LifeSettings.from_dict({"state_config": {"refresh_minutes": "1"}})

        self.assertEqual(config.state.refresh_minutes, 5)

    def test_default_chat_prompt_avoids_unfounded_gender_guessing(self):
        config = LifeSettings.from_dict({})

        self.assertIn("人物称呼、性别、亲疏和关系以明确资料为准", config.chat_prompt)
        self.assertIn("优先使用最新且最具体的参考对象线索或用户自述", config.chat_prompt)
        self.assertIn("昵称、头像、平台、语气、表情或刻板印象不能单独作为依据", config.chat_prompt)
        self.assertIn("证据不足时", config.chat_prompt)

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
        self.assertFalse(hasattr(config, "outfit_prompts"))

    def test_storage_settings_parse_retention_days(self):
        config = LifeSettings.from_dict(
            {
                "storage_config": {
                    "daily_keep_days": "45",
                    "review_keep_days": "bad",
                    "memory_keep_days": "365",
                    "planning_keep_days": "-1",
                    "generated_media_keep_days": "99999",
                    "reverse_cache_keep_days": "bad",
                }
            }
        )

        self.assertEqual(config.storage.daily_keep_days, 45)
        self.assertEqual(config.storage.review_keep_days, 120)
        self.assertEqual(config.storage.memory_keep_days, 365)
        self.assertEqual(config.storage.planning_keep_days, 0)
        self.assertEqual(config.storage.generated_media_keep_days, 3650)
        self.assertEqual(config.storage.reverse_cache_keep_days, 7)


