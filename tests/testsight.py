import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from support import async_return

from core.sight import SightClip, SightInsight, TranscriptResult, TranscriptSegment
from core.sight import sample as sample_module
from core.sight.auth import enrich_bili_cookies
from core.sight.bili import (
    BiliTarget,
    fetch_bili_metadata,
    resolve_bili_target,
    target_from_text,
)
from core.sight.brief import SightBrief
from core.sight.cookie import BiliCookieJar
from core.sight.digest import frame_prompt
from core.sight.embed import embed_local_markdown_images
from core.sight.hearing import (
    AudioTranscriptError,
    _transcript_from_bcut,
    prepare_audio_source,
    transcribe_bcut_audio,
)
from core.sight.local import ensure_wav, transcribe_local_audio, transcript_from_payload
from core.sight.note import (
    PROFESSIONAL_NOTE_CACHE_SCHEMA,
    SightNote,
    SightNoteError,
    _payload_markdown,
    professional_note_prompt_key,
)
from core.sight.reader import SightReader
from core.sight.sample import (
    SightFrame,
    extract_video_frames,
    prepare_sample_video_source,
    resolve_sample_source,
    select_distinct_frames,
    select_frame_seconds,
)


class SightPipelineTest(unittest.TestCase):
    def test_professional_metrics_log_uses_chinese_labels(self):
        from core.sight.bridge import SightMixin

        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4"),
            transcript_source="本地ASR",
            metadata={
                "sight_metrics": {
                    "material_seconds": 1.25,
                    "frame_extract_seconds": 0.5,
                    "vision_seconds": 2.0,
                    "professional_note_seconds": 3.0,
                    "render_send_seconds": 0.75,
                    "vision_mode": "multi_image",
                }
            },
        )

        with patch("core.sight.bridge.logger.info") as log_info:
            SightMixin._log_sight_professional_metrics(insight)

        message = str(log_info.call_args.args[0])
        self.assertIn("文字=本地语音识别", message)
        self.assertIn("视觉=2.00 秒/0次/多图", message)
        self.assertIn("总耗时=7.50 秒", message)
        self.assertNotIn("multi_image", message)

    def test_bili_summary_log_subject_prefers_title_and_author(self):
        from core.sight.bridge import SightMixin

        subject = SightMixin._bili_summary_log_subject(
            {"title": "妈妈一推门，天塌了", "author": "花世界满天飞"},
            BiliTarget(
                bvid="BV13sTF6rE1N", url="https://www.bilibili.com/video/BV13sTF6rE1N"
            ),
        )

        self.assertEqual(subject, "标题=妈妈一推门，天塌了；作者=花世界满天飞")

    def test_bili_summary_log_subject_uses_bvid_without_metadata(self):
        from core.sight.bridge import SightMixin

        subject = SightMixin._bili_summary_log_subject(
            {"platform": "bilibili", "bvid": "BV13sTF6rE1N"},
            BiliTarget(
                bvid="BV13sTF6rE1N", url="https://www.bilibili.com/video/BV13sTF6rE1N"
            ),
        )

        self.assertEqual(subject, "BV13sTF6rE1N")

    def test_professional_note_outdated_cache_schema_is_invalidated(self):
        from core.sight.bridge import SightMixin

        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4"),
            metadata={
                "professional_note_schema": "outdated_schema",
                "notes": {"professional": "# 过期缓存摘要"},
            },
        )

        self.assertEqual(SightMixin._cached_sight_note_markdown(insight), "")
        insight.metadata["professional_note_schema"] = PROFESSIONAL_NOTE_CACHE_SCHEMA
        insight.metadata["professional_note_prompt_key"] = (
            professional_note_prompt_key()
        )
        self.assertEqual(
            SightMixin._cached_sight_note_markdown(insight), "# 过期缓存摘要"
        )

    def test_professional_note_adds_fixed_closing_when_model_omits_it(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="结构化测试"),
            metadata={"title": "结构化测试"},
        )
        markdown = _payload_markdown(
            insight,
            {
                "sections": [
                    {
                        "role": "fact",
                        "start": "00:00",
                        "end": "00:30",
                        "title": "关键事实",
                        "paragraphs": ["视频说明了一个可以确认的事实。"],
                    }
                ]
            },
            style="professional",
        )

        self.assertEqual(markdown.count("## 总结与参考建议"), 1)
        self.assertGreater(
            markdown.index("## 总结与参考建议"), markdown.index("## 关键事实")
        )
        self.assertIn("**参考建议**", markdown)
        self.assertIn("可靠来源进一步核实", markdown)
        closing = markdown.split("## 总结与参考建议", 1)[1]
        self.assertNotIn("⏱", closing)

    def test_professional_note_preserves_model_closing_and_moves_it_last(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="结构化测试"),
            metadata={"title": "结构化测试"},
        )
        markdown = _payload_markdown(
            insight,
            {
                "sections": [
                    {
                        "role": "suggestion",
                        "start": "00:30",
                        "end": "00:40",
                        "title": "总结与建议",
                        "paragraphs": ["这是模型生成的整体总结。"],
                        "bullets": ["**参考建议**：保留这条具体建议。"],
                    },
                    {
                        "role": "fact",
                        "start": "00:00",
                        "end": "00:30",
                        "title": "关键事实",
                        "paragraphs": ["先展示具体内容。"],
                    },
                ]
            },
            style="professional",
        )

        self.assertEqual(markdown.count("## 总结与参考建议"), 1)
        self.assertGreater(
            markdown.index("## 总结与参考建议"), markdown.index("## 关键事实")
        )
        self.assertIn("这是模型生成的整体总结", markdown)
        self.assertIn("保留这条具体建议", markdown)
        closing = markdown.split("## 总结与参考建议", 1)[1]
        self.assertNotIn("⏱", closing)

    def test_professional_note_preserves_specific_titles_and_role_fallback(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="结构化测试"),
            summary="结构化摘要。",
            details=["结构化细节。"],
            metadata={"title": "结构化测试", "uploader": "测试作者"},
        )
        payload = {
            "sections": [
                {
                    "title": "核心论点",
                    "paragraphs": ["这段没有 role，不能按标题归类。"],
                    "bullets": [],
                    "quotes": [],
                },
                {
                    "title": "随意标题",
                    "role": "fact",
                    "paragraphs": [],
                    "bullets": ["这段有 role，按结构字段归类。"],
                    "quotes": [],
                },
            ]
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        self.assertIn("## 核心论点\n\n这段没有 role，不能按标题归类。", markdown)
        self.assertIn("## 随意标题\n\n- 这段有 role，按结构字段归类。", markdown)

    def test_professional_note_renders_compact_structured_section(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="专题分析"),
            metadata={"title": "专题分析"},
        )
        payload = {
            "sections": [
                {
                    "role": "analysis",
                    "start": "01:31",
                    "end": "03:08",
                    "title": "注意义务",
                    "paragraphs": ["视频认为，争议来自双方责任边界不清。"],
                    "bullets": ["**先行行为**：可能产生后续注意义务。"],
                    "quotes": ["关键结论需要结合具体证据判断。"],
                }
            ]
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        self.assertIn("## 注意义务\n\n⏱ 01:31", markdown)
        self.assertNotIn("01:31-03:08", markdown)
        self.assertIn("视频认为，争议来自双方责任边界不清。", markdown)
        self.assertIn("- **先行行为**：可能产生后续注意义务。", markdown)
        self.assertIn("> 关键结论需要结合具体证据判断。", markdown)

    def test_professional_note_highlights_plain_bullet_labels(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="重点标记测试"),
            metadata={"title": "重点标记测试"},
        )
        payload = {
            "sections": [
                {
                    "role": "analysis",
                    "title": "关键分析",
                    "bullets": [
                        "核心判断：这里是需要突出显示的说明。",
                        "普通说明不应被整句标记。",
                        "关键数据：比例为 **80%**。",
                        "00:10 进入下一阶段。",
                    ],
                }
            ]
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        self.assertIn("- **核心判断**：这里是需要突出显示的说明。", markdown)
        self.assertIn("- 普通说明不应被整句标记。", markdown)
        self.assertIn("- **关键数据**：比例为 **80%**。", markdown)
        self.assertIn("- 00:10 进入下一阶段。", markdown)

    def test_professional_note_renders_report_roles_in_order(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="专业总结测试"),
            summary="专业总结摘要。",
            metadata={"title": "专业总结测试", "author": "测试作者"},
        )
        payload = {
            "sections": [
                {"role": "overview", "paragraphs": ["背景信息。"]},
                {"role": "core", "bullets": ["核心论点。"]},
                {"role": "fact", "bullets": ["关键事实。"]},
                {"role": "data", "bullets": ["数据支撑。"]},
                {"role": "analysis", "bullets": ["影响分析。"]},
                {"role": "risk", "bullets": ["争议风险。"]},
                {"role": "suggestion", "bullets": ["参考建议。"]},
            ]
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        headings = [
            "## 背景概述",
            "## 核心论点",
            "## 关键事实",
            "## 数据支撑",
            "## 分析与影响",
            "## 争议与风险",
            "## 总结与参考建议",
        ]
        for heading in headings:
            self.assertIn(heading, markdown)
        self.assertEqual(
            [markdown.index(heading) for heading in headings],
            sorted(markdown.index(heading) for heading in headings),
        )
        self.assertNotIn("## AI 总结", markdown)

    def test_frame_prompt_keeps_frame_metadata_in_dynamic_section(self):
        prompt = frame_prompt(2, 5, SightClip(source="D:/tmp/video.mp4"), label="00:12")

        self.assertLess(prompt.index("只输出 JSON"), prompt.index("【画面帧】"))
        self.assertIn("抽样帧：第 2/5 个", prompt)
        self.assertIn("时间点：00:12", prompt)

    def test_embed_local_markdown_images_converts_existing_frame_to_data_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_path = Path(tmpdir) / "frame.png"
            frame_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

            markdown = embed_local_markdown_images(f"![00:12 关键帧]({frame_path})")

        self.assertIn("![00:12 关键帧](data:image/png;base64,", markdown)
        self.assertNotIn(str(frame_path), markdown)

    def test_embed_local_markdown_images_handles_parentheses_in_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_path = Path(tmpdir) / "frame(12).png"
            frame_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

            markdown = embed_local_markdown_images(f"![00:12 关键帧]({frame_path})")

        self.assertIn("![00:12 关键帧](data:image/png;base64,", markdown)
        self.assertNotIn(str(frame_path), markdown)

    def test_select_frame_seconds_spreads_across_video_duration(self):
        seconds = select_frame_seconds(80, 8)

        self.assertEqual(len(seconds), 8)
        self.assertGreater(seconds[0], 0)
        self.assertLess(seconds[0], 10)
        self.assertGreater(seconds[-1], 70)
        self.assertTrue(all(left < right for left, right in zip(seconds, seconds[1:])))

    def test_select_distinct_frames_removes_near_duplicates(self):
        frames = [
            SightFrame(path=Path(f"frame-{index}.jpg"), second=float(index))
            for index in range(5)
        ]
        signatures = [
            (0,) * 16,
            (1,) * 16,
            (128,) * 16,
            (130,) * 16,
            (255,) * 16,
        ]

        with patch("core.sight.sample._frame_visual_signature", side_effect=signatures):
            selected = select_distinct_frames(frames, 4)

        self.assertEqual(
            [item.path.name for item in selected],
            [
                "frame-0.jpg",
                "frame-2.jpg",
                "frame-4.jpg",
            ],
        )

    def test_bcut_payload_converts_to_transcript_result(self):
        result = _transcript_from_bcut(
            {
                "result": (
                    '{"language":"zh","utterances":['
                    '{"start_time":0,"end_time":1200,"transcript":"开场介绍"},'
                    '{"start_time":1400,"end_time":2500,"transcript":"后面讲到重点"}'
                    "]}"
                )
            },
            max_chars=100,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "必剪转写")
        self.assertEqual(result.full_text, "开场介绍 后面讲到重点")
        self.assertEqual(result.segments[1].start, 1.4)

    def test_bili_target_extracts_long_url_and_page(self):
        target = target_from_text(
            "看这个 https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1xx411c7mD")
        self.assertEqual(target.page, 2)
        self.assertEqual(
            target.canonical_url, "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        )

    def test_bili_target_extracts_shared_long_url_and_normalizes_canonical_url(self):
        target = target_from_text(
            "https://www.bilibili.com/video/BV1YV756rEuo/?share_source=copy_web&vd_source=fe1ef4a7f24137a8c213d708c6f97a31"
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1YV756rEuo")
        self.assertEqual(
            target.canonical_url, "https://www.bilibili.com/video/BV1YV756rEuo"
        )
        self.assertEqual(target.url, "https://www.bilibili.com/video/BV1YV756rEuo")

    def test_bili_target_extracts_qq_card_url(self):
        target = target_from_text(
            '[CQ:json,data={"meta":{"detail_1":{"qqdocurl":"https:\\/\\/www.bilibili.com\\/video\\/BV1yy411c7mE"}}}]'
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1yy411c7mE")

    def test_bili_target_extracts_bvid_near_chinese_text(self):
        target = target_from_text("这个BV1dd411c7mG能总结吗")

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1dd411c7mG")

    def test_bili_target_extracts_multipart_page_param(self):
        target = target_from_text("https://www.bilibili.com/video/BV1oERxBqEpE?p=2")

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1oERxBqEpE")
        self.assertEqual(target.page, 2)
        self.assertEqual(
            target.canonical_url, "https://www.bilibili.com/video/BV1oERxBqEpE?p=2"
        )

    def test_bili_short_target_resolves_to_bvid(self):
        async def resolver(url, timeout_seconds=10):
            return "https://www.bilibili.com/video/BV1zz411c7mF"

        from core.sight import bili as bili_module

        original = bili_module.follow_redirect
        bili_module.follow_redirect = resolver
        try:
            target = asyncio.run(
                resolve_bili_target(BiliTarget(url="https://b23.tv/abc123"))
            )
        finally:
            bili_module.follow_redirect = original

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.bvid, "BV1zz411c7mF")


class SightFrameCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_bili_metadata_uses_login_cookie_request_header(self):
        from core.sight import bili as bili_module

        calls = {}

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def json(self, content_type=None):
                return {
                    "data": {
                        "title": "真实标题",
                        "owner": {"name": "真实作者"},
                        "duration": 66,
                        "bvid": "BV1M5TY6tErE",
                        "aid": 123,
                        "cid": 456,
                    }
                }

        class Session:
            def __init__(self, **kwargs):
                calls["session_kwargs"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            def get(self, url, params=None, headers=None):
                calls["url"] = url
                calls["params"] = params
                calls["headers"] = headers
                return Response()

        old_session = bili_module.aiohttp.ClientSession
        bili_module.aiohttp.ClientSession = Session
        try:
            metadata = await fetch_bili_metadata(
                BiliTarget(
                    bvid="BV1M5TY6tErE",
                    url="https://www.bilibili.com/video/BV1M5TY6tErE",
                ),
                cookies={"SESSDATA": "session-token", "bili_jct": "csrf-token"},
            )
        finally:
            bili_module.aiohttp.ClientSession = old_session

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.title, "真实标题")
        self.assertEqual(metadata.author, "真实作者")
        kwargs = calls["session_kwargs"]
        self.assertNotIn("cookies", kwargs)
        self.assertNotIn("headers", kwargs)
        self.assertIn("SESSDATA=session-token", calls["headers"]["Cookie"])
        self.assertIn("bili_jct=csrf-token", calls["headers"]["Cookie"])
        self.assertEqual(calls["headers"]["Referer"], "https://www.bilibili.com")

    async def test_resolve_sample_source_prefers_bili_direct_fallback(self):
        calls = []

        async def fallback(source, cache_dir, *, cookiefile=None, **kwargs):
            calls.append((source, cookiefile))
            path = Path(cache_dir) / "media" / "fallback.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
            return path

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.sample.download_remote_video",
                lambda source, cache_dir, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.sample._download_bili_direct_video",
                fallback,
            ),
        ):
            result = await resolve_sample_source(
                "https://www.bilibili.com/video/BV1xx411c7mD", Path(tmpdir)
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "fallback.mp4")
        self.assertEqual(result.parent.name, "media")
        self.assertTrue(calls)

    async def test_bili_direct_video_uses_pagelist_cid_when_view_metadata_has_no_cid(
        self,
    ):
        calls = []

        async def resolve(target, timeout_seconds=10):
            return BiliTarget(
                bvid="BV1VST56DEHu",
                url="https://www.bilibili.com/video/BV1VST56DEHu",
                page=0,
            )

        async def pagelist_cid(bvid, page, referer, cookies):
            calls.append(("pagelist", bvid, page, referer))
            return 39496714106

        async def playurl(bvid, cid, referer, cookies):
            calls.append(("playurl", bvid, cid, referer))
            return "https://example.com/video.mp4"

        async def download(source, cache_dir, **kwargs):
            calls.append(("download", source, kwargs.get("cache_identity")))
            path = Path(cache_dir) / "media" / "single-page.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
            return path, ""

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.sample.resolve_bili_target",
                resolve,
            ),
            patch(
                "core.sight.sample.fetch_bili_metadata",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.sample._bili_pagelist_cid",
                pagelist_cid,
            ),
            patch(
                "core.sight.sample._bili_playurl_direct_url",
                playurl,
            ),
            patch(
                "core.sight.sample._download_remote_video_with_reason",
                download,
            ),
        ):
            result = await sample_module._download_bili_direct_video(
                "https://www.bilibili.com/video/BV1VST56DEHu",
                Path(tmpdir),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "single-page.mp4")
        self.assertIn(
            (
                "pagelist",
                "BV1VST56DEHu",
                1,
                "https://www.bilibili.com/video/BV1VST56DEHu",
            ),
            calls,
        )
        self.assertIn(
            (
                "playurl",
                "BV1VST56DEHu",
                39496714106,
                "https://www.bilibili.com/video/BV1VST56DEHu",
            ),
            calls,
        )
        self.assertIn(
            (
                "download",
                "https://example.com/video.mp4",
                "bilibili:BV1VST56DEHu:39496714106",
            ),
            calls,
        )

    async def test_reader_reuses_prepared_video_for_local_asr(self):
        runtime = types.SimpleNamespace(
            config=types.SimpleNamespace(
                sight=types.SimpleNamespace(audio_transcript_mode="local")
            ),
            data_path=Path("D:/tmp/daily_life.db"),
        )
        reader = SightReader(runtime)

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.reader.prepare_audio_source",
                side_effect=AssertionError("不应再次下载音频或视频"),
            ),
        ):
            source_path = Path(tmpdir) / "prepared.mp4"
            source_path.write_bytes(b"video")
            result = await reader.prepare_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD",
                prepared_video=source_path,
            )

        self.assertEqual(result, source_path)

    async def test_prepare_audio_source_uses_ytdlp_audio_for_video_page(self):
        seen_options = {}

        class YoutubeDL:
            def __init__(self, options):
                self.options = options
                seen_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, source, download=True):
                output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"fake-audio")
                return {"requested_downloads": [{"filepath": str(output)}]}

        module = types.ModuleType("yt_dlp")
        module.YoutubeDL = YoutubeDL
        ffmpeg_module = types.ModuleType("imageio_ffmpeg")
        ffmpeg_module.get_ffmpeg_exe = lambda: "D:/tools/ffmpeg.exe"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.hearing.resolve_sample_source",
                lambda source, cache_dir: async_return(None),
            ),
            patch("core.sight.ffmpeg.shutil.which", lambda name: None),
            patch.dict(
                sys.modules,
                {"yt_dlp": module, "imageio_ffmpeg": ffmpeg_module},
            ),
        ):
            result = await prepare_audio_source(
                "https://www.bilibili.com/video/BV1xx411c7mD", Path(tmpdir)
            )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.suffix, ".mp3")
            self.assertTrue(result.is_file())
            self.assertEqual(seen_options["ffmpeg_location"], "D:/tools/ffmpeg.exe")

    async def test_ytdlp_audio_uses_saved_bili_cookiefile(self):
        seen_options = {}

        class YoutubeDL:
            def __init__(self, options):
                self.options = options
                seen_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, source, download=True):
                output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"fake-audio")
                return {"requested_downloads": [{"filepath": str(output)}]}

        module = types.ModuleType("yt_dlp")
        module.YoutubeDL = YoutubeDL

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.hearing.resolve_sample_source",
                lambda source, cache_dir: async_return(None),
            ),
            patch.dict(sys.modules, {"yt_dlp": module}),
        ):
            BiliCookieJar(Path(tmpdir)).save({"SESSDATA": "session-token"})
            result = await prepare_audio_source(
                "https://www.bilibili.com/video/BV1xx411c7mD", Path(tmpdir)
            )
            self.assertIn("cookiefile", seen_options)
            self.assertTrue(Path(seen_options["cookiefile"]).is_file())

        self.assertIsNotNone(result)

    def test_bili_cookie_jar_accepts_plugin_data_and_sight_cache_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)
            first = BiliCookieJar(plugin_dir)
            self.assertTrue(first.save({"SESSDATA": "session-token"}))

            second = BiliCookieJar(plugin_dir / "sight")
            self.assertTrue(second.is_logged_in())
            self.assertEqual(
                second.cookiefile, plugin_dir / "sight" / "bili_cookies.txt"
            )

    async def test_bili_cookie_enrichment_keeps_login_and_adds_buvid(self):
        calls = []

        class Response:
            def __init__(self, payload=None, cookies=None):
                self.payload = payload or {}
                self.cookies = {
                    key: types.SimpleNamespace(key=key, value=value)
                    for key, value in dict(cookies or {}).items()
                }
                self.status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def json(self, content_type=None):
                return self.payload

        class Session:
            def __init__(self, *args, **kwargs):
                self.headers = kwargs.get("headers") or {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def get(self, url, **kwargs):
                calls.append(("GET", url, kwargs))
                if url.endswith("/x/frontend/finger/spi"):
                    return Response({"code": 0, "data": {"b_3": "b3", "b_4": "b4"}})
                return Response(cookies={"buvid3": "home-b3"})

        with patch("core.sight.auth.aiohttp.ClientSession", Session):
            cookies = await enrich_bili_cookies(
                {"SESSDATA": "session-token", "bili_jct": "csrf"}
            )

        self.assertEqual(cookies["SESSDATA"], "session-token")
        self.assertEqual(cookies["buvid3"], "home-b3")
        self.assertEqual(cookies["buvid4"], "b4")
        self.assertEqual([item[0] for item in calls], ["GET", "GET"])

    async def test_extract_video_frames_uses_imageio_ffmpeg_when_system_ffmpeg_missing(
        self,
    ):
        calls = []

        async def resolve(source, cache_dir, **kwargs):
            path = Path(cache_dir) / "video.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
            return path

        def extract(ffmpeg, source, target, second):
            calls.append((ffmpeg, second))
            target.write_bytes(f"frame-{second}".encode())
            return True

        ffmpeg_module = types.ModuleType("imageio_ffmpeg")
        ffmpeg_module.get_ffmpeg_exe = lambda: "D:/tools/ffmpeg.exe"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "core.sight.sample._extract_frame",
                extract,
            ),
            patch(
                "core.sight.sample._video_duration",
                lambda ffprobe, source: 4.0,
            ),
            patch(
                "core.sight.ffmpeg.shutil.which",
                lambda name: None,
            ),
            patch.dict(sys.modules, {"imageio_ffmpeg": ffmpeg_module}),
        ):
            source_path = Path(tmpdir) / "video.mp4"
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")
            frames = await extract_video_frames(source_path, Path(tmpdir), max_frames=2)

        self.assertEqual(len(frames), 2)
        self.assertTrue(calls)
        self.assertTrue(all(call[0] == "D:/tools/ffmpeg.exe" for call in calls))

    async def test_prepare_sample_video_source_passes_download_size_limit(self):
        seen = {}

        async def resolve(source, cache_dir, **kwargs):
            seen.update(kwargs)
            return None

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.sample.resolve_sample_source", resolve),
        ):
            source_path = await prepare_sample_video_source(
                "https://example.com/video.mp4",
                Path(tmpdir),
                max_video_mb=64,
            )

        self.assertIsNone(source_path)
        self.assertEqual(seen["max_bytes"], 64 * 1024 * 1024)

    async def test_prepare_sample_video_source_passes_download_timeout(self):
        seen = {}

        async def resolve(source, cache_dir, **kwargs):
            seen.update(kwargs)
            return None

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.sample.resolve_sample_source", resolve),
        ):
            source_path = await prepare_sample_video_source(
                "https://example.com/video.mp4",
                Path(tmpdir),
                download_timeout_seconds=360,
            )

        self.assertIsNone(source_path)
        self.assertEqual(seen["timeout_seconds"], 360)

    async def test_prepare_sample_video_source_allows_unlimited_download_size(self):
        seen = {}

        async def resolve(source, cache_dir, **kwargs):
            seen.update(kwargs)
            return None

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.sample.resolve_sample_source", resolve),
        ):
            source_path = await prepare_sample_video_source(
                "https://example.com/video.mp4",
                Path(tmpdir),
                max_video_mb=0,
            )

        self.assertIsNone(source_path)
        self.assertEqual(seen["max_bytes"], 0)

    async def test_local_asr_wav_uses_shared_audio_cache_dir(self):
        def convert(source, target):
            target.write_bytes(b"wav")
            return True

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.local._to_wav_sync", convert),
        ):
            cache_dir = Path(tmpdir) / "sight"
            source = Path(tmpdir) / "source.mp3"
            source.write_bytes(b"mp3")

            result = await ensure_wav(source, cache_dir)

            self.assertEqual(result.parent, cache_dir / "audio")
            self.assertEqual(result.suffix, ".wav")
            self.assertTrue(result.is_file())

    async def test_local_asr_missing_dependencies_does_not_install_at_runtime(self):
        from core.sight.local import LocalAsrConfig, ensure_ready

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.local._modules_ready", return_value=False),
            patch("core.sight.local.subprocess.run") as run,
        ):
            with self.assertRaisesRegex(AudioTranscriptError, "本地语音识别依赖未安装"):
                await ensure_ready(Path(tmpdir), LocalAsrConfig())

        run.assert_not_called()

    async def test_local_asr_writes_unified_transcript_cache(self):
        async def ensure_ready(_cache_dir, _config):
            return None

        async def run_worker(_input_path, output_path, _cache_dir, _config):
            output_path.write_text(
                json.dumps(
                    {
                        "plain_text": "完整文本",
                        "segments": [
                            {"start_ms": 0, "end_ms": 1200, "text": "开场介绍"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.local.ensure_ready", ensure_ready),
            patch(
                "core.sight.local._run_worker",
                run_worker,
            ),
        ):
            cache_dir = Path(tmpdir) / "sight"
            audio_path = Path(tmpdir) / "source.wav"
            audio_path.write_bytes(b"wav")

            result = await transcribe_local_audio(audio_path, cache_dir)

            self.assertIsNotNone(result)
            cached = list((cache_dir / "transcripts").glob("local_asr_*.json"))
            self.assertEqual(len(cached), 1)
            payload = json.loads(cached[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["engine"], "local_asr")
            self.assertEqual(payload["source_audio"], str(audio_path))
            self.assertEqual(payload["full_text"], "开场介绍")
            self.assertEqual(payload["raw_payload"]["plain_text"], "完整文本")
            self.assertFalse((cache_dir / "asr" / "result").exists())

    async def test_bcut_writes_unified_transcript_cache(self):
        class FakeBcutTranscriber:
            def __init__(self, **_kwargs):
                self.last_payload = {"result": {"language": "zh", "utterances": []}}

            async def transcribe(self, _audio_path):
                return TranscriptResult(
                    language="zh",
                    full_text="必剪转写内容",
                    segments=(
                        TranscriptSegment(start=0, end=1.2, text="必剪转写内容"),
                    ),
                    source="必剪转写",
                )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("core.sight.hearing.BcutTranscriber", FakeBcutTranscriber),
        ):
            cache_dir = Path(tmpdir) / "sight"
            audio_path = Path(tmpdir) / "source.mp3"
            audio_path.write_bytes(b"mp3")

            result = await transcribe_bcut_audio(audio_path, cache_dir=cache_dir)

            self.assertIsNotNone(result)
            cached = list((cache_dir / "transcripts").glob("bcut_*.json"))
            self.assertEqual(len(cached), 1)
            payload = json.loads(cached[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["engine"], "bcut")
            self.assertEqual(payload["source_audio"], str(audio_path))
            self.assertEqual(payload["full_text"], "必剪转写内容")
            self.assertEqual(payload["segments"][0]["end"], 1.2)

    async def test_bcut_mode_uses_audio_transcript_directly(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "bcut"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def audio_transcript(source, cache_dir, **kwargs):
            calls.append(("audio", source, ""))
            return TranscriptResult(
                language="zh",
                full_text="这是必剪转写出来的内容",
                segments=(
                    TranscriptSegment(start=0, end=1, text="这是必剪转写出来的内容"),
                ),
                source="必剪转写",
            )

        with patch("core.sight.reader.transcribe_bcut", audio_transcript):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertEqual(result.transcript_source, "必剪转写")
        self.assertEqual(result.transcript, "这是必剪转写出来的内容")
        self.assertEqual(calls, [("audio", "D:/tmp/video.mp4", "")])

    async def test_bcut_mode_uses_bcut_first(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "bcut"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def audio_transcript(source, cache_dir, **kwargs):
            calls.append(("bcut", source))
            return TranscriptResult(
                language="zh", full_text="音频转写", source="必剪转写"
            )

        async def local_transcript(source, cache_dir, **kwargs):
            calls.append(("local", source))
            return TranscriptResult(
                language="zh", full_text="本地转写", source="本地ASR"
            )

        with (
            patch("core.sight.reader.transcribe_bcut", audio_transcript),
            patch(
                "core.sight.reader.transcribe_local",
                local_transcript,
            ),
        ):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertEqual(result.transcript_source, "必剪转写")
        self.assertEqual(result.transcript, "音频转写")
        self.assertEqual(calls, [("bcut", "D:/tmp/video.mp4")])

    async def test_bcut_mode_falls_back_to_local_asr(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "bcut"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def bcut_transcript(source, cache_dir, **kwargs):
            calls.append(("bcut", source))
            return None

        async def local_transcript(source, cache_dir, **kwargs):
            calls.append(("local", source))
            return TranscriptResult(
                language="zh", full_text="本地转写内容", source="本地语音识别"
            )

        with (
            patch("core.sight.reader.transcribe_bcut", bcut_transcript),
            patch(
                "core.sight.reader.transcribe_local",
                local_transcript,
            ),
        ):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertEqual(result.transcript_source, "本地语音识别")
        self.assertEqual(result.transcript, "本地转写内容")
        self.assertEqual(result.errors, [])
        self.assertEqual(
            calls, [("bcut", "D:/tmp/video.mp4"), ("local", "D:/tmp/video.mp4")]
        )

    async def test_local_mode_falls_back_to_bcut(self):
        from core.sight.hearing import AudioTranscriptError
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "local"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def local_transcript(source, cache_dir, **kwargs):
            calls.append(("local", source))
            raise AudioTranscriptError("依赖未准备")

        async def bcut_transcript(source, cache_dir, **kwargs):
            calls.append(("bcut", source))
            return TranscriptResult(
                language="zh", full_text="必剪兜底内容", source="必剪转写"
            )

        with (
            patch("core.sight.reader.transcribe_local", local_transcript),
            patch(
                "core.sight.reader.transcribe_bcut",
                bcut_transcript,
            ),
        ):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertEqual(result.transcript_source, "必剪转写")
        self.assertEqual(result.transcript, "必剪兜底内容")
        self.assertEqual(result.errors, [])
        self.assertEqual(
            calls, [("local", "D:/tmp/video.mp4"), ("bcut", "D:/tmp/video.mp4")]
        )

    def test_sight_reader_captures_full_transcript_before_summary_chunking(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "sight_config": {
                    "max_transcript_chars": 4000,
                    "note_max_transcript_chars": 18000,
                }
            }
        )

        self.assertEqual(SightReader(runtime).max_chars, 500000)

    async def test_local_mode_uses_local_first(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "local"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def local_transcript(source, cache_dir, **kwargs):
            calls.append(("local", source))
            return TranscriptResult(
                language="zh", full_text="本地转写", source="本地语音识别"
            )

        async def bcut_transcript(source, cache_dir, **kwargs):
            calls.append(("bcut", source))
            return TranscriptResult(
                language="zh", full_text="必剪转写", source="必剪转写"
            )

        with (
            patch("core.sight.reader.transcribe_local", local_transcript),
            patch(
                "core.sight.reader.transcribe_bcut",
                bcut_transcript,
            ),
        ):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertEqual(result.transcript_source, "本地语音识别")
        self.assertEqual(result.transcript, "本地转写")
        self.assertEqual(calls, [("local", "D:/tmp/video.mp4")])

    async def test_audio_transcript_keeps_clip_metadata(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "bcut"}}
        )
        reader = SightReader(runtime)

        async def audio_transcript(source, cache_dir, **kwargs):
            return TranscriptResult(
                language="zh", full_text="必剪转写", source="必剪转写"
            )

        with patch("core.sight.reader.transcribe_bcut", audio_transcript):
            result = await reader.read(
                None,
                SightClip(
                    source="https://www.bilibili.com/video/BV1aa411c7mD",
                    metadata={
                        "title": "真实标题",
                        "author": "真实作者",
                        "platform": "bilibili",
                    },
                ),
            )

        self.assertEqual(result.transcript_source, "必剪转写")
        self.assertEqual(result.metadata["title"], "真实标题")
        self.assertEqual(result.metadata["author"], "真实作者")

    async def test_audio_transcript_falls_back_after_bcut_miss(self):
        from support import DailyLifeRuntime, LifeSettings

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"audio_transcript_mode": "bcut"}}
        )
        reader = SightReader(runtime)
        calls = []

        async def bcut_transcript(source, cache_dir, **kwargs):
            calls.append(("bcut", source))
            return None

        async def local_transcript(source, cache_dir, **kwargs):
            calls.append(("local", source))
            return None

        with (
            patch("core.sight.reader.transcribe_bcut", bcut_transcript),
            patch(
                "core.sight.reader.transcribe_local",
                local_transcript,
            ),
        ):
            result = await reader.read(None, SightClip(source="D:/tmp/video.mp4"))

        self.assertFalse(result.has_content)
        self.assertIn("必剪转写没有返回可用文字", result.errors)
        self.assertIn("本地语音识别转写没有返回可用文字", result.errors)
        self.assertEqual(
            calls, [("bcut", "D:/tmp/video.mp4"), ("local", "D:/tmp/video.mp4")]
        )

    def test_local_asr_payload_converts_to_transcript_result(self):
        result = transcript_from_payload(
            {
                "plain_text": "完整文本",
                "segments": [
                    {"start_ms": 0, "end_ms": 1200, "text": "开场介绍"},
                    {"start_ms": 1400, "end_ms": 2500, "text": "后面讲到重点"},
                ],
            },
            max_chars=100,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "本地语音识别")
        self.assertEqual(result.full_text, "开场介绍 后面讲到重点")
        self.assertEqual(result.segments[1].start, 1.4)
        self.assertEqual(result.metadata["engine"], "funasr")

    async def test_describe_frames_accepts_timestamped_sight_frame(self):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_path = Path(tmpdir) / "frame-1.jpg"
            frame_path.write_bytes(b"fake-frame")

            provider = Provider(
                ['{"summary":"画面里有人在教室讲话","details":["字幕在下方"]}'],
                provider_id="vision",
            )
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.context = Context(provider, providers={"vision": provider})
            runtime.config = LifeSettings.from_dict(
                {"vision_config": {"provider": "vision"}}
            )
            runtime.composer = type(
                "Composer",
                (),
                {
                    "_get_provider": staticmethod(
                        lambda provider_id="": async_return(provider)
                    ),
                    "_cleanup_conversation": staticmethod(
                        lambda session_id: async_return(None)
                    ),
                },
            )()

            notes, assets = await runtime._describe_sight_frames(
                SightClip(source="D:/tmp/classroom.mp4"),
                [SightFrame(path=frame_path, second=12.0, label="00:12")],
            )

            self.assertEqual(notes, ["00:12：画面里有人在教室讲话（字幕在下方）"])
            self.assertEqual(
                assets,
                [
                    {
                        "path": str(frame_path),
                        "label": "00:12",
                        "second": 12.0,
                        "note": "画面里有人在教室讲话（字幕在下方）",
                    }
                ],
            )
            self.assertIn("时间点：00:12", provider.vision_prompts[0]["prompt"])
            self.assertLess(
                provider.vision_prompts[0]["prompt"].index("只输出 JSON"),
                provider.vision_prompts[0]["prompt"].index("【画面帧】"),
            )

    async def test_describe_frames_uses_one_structured_multi_image_request(self):
        from support import Context, DailyLifeRuntime, LifeSettings

        calls = []

        class BatchProvider:
            async def text_chat(
                self, prompt, session_id=None, image_urls=None, **kwargs
            ):
                calls.append((prompt, list(image_urls or [])))
                return types.SimpleNamespace(
                    completion_text=json.dumps(
                        {
                            "frames": [
                                {"index": 1, "summary": "开场展示街道"},
                                {"index": 2, "summary": "镜头转入室内"},
                            ]
                        },
                        ensure_ascii=False,
                    )
                )

        provider = BatchProvider()
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.composer = types.SimpleNamespace(
            _get_provider=lambda provider_id="": async_return(provider),
            _cleanup_conversation=lambda session_id: async_return(None),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = [Path(tmpdir) / "frame-1.jpg", Path(tmpdir) / "frame-2.jpg"]
            for path in paths:
                path.write_bytes(b"frame")
            result = await runtime._describe_sight_frames(
                SightClip(source="D:/tmp/video.mp4", name="场景变化"),
                [
                    SightFrame(path=paths[0], second=2.0, label="00:02"),
                    SightFrame(path=paths[1], second=8.0, label="00:08"),
                ],
            )

        notes, assets = result
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], [str(path) for path in paths])
        self.assertEqual(result.request_count, 1)
        self.assertEqual(result.mode, "multi_image")
        self.assertEqual(notes, ["00:02：开场展示街道", "00:08：镜头转入室内"])
        self.assertEqual(len(assets), 2)

    async def test_describe_frames_uses_video_frame_provider_before_general_vision(
        self,
    ):
        from support import (
            Context,
            DailyLifeRuntime,
            LifeSettings,
            Provider,
            async_return,
        )

        default_provider = Provider(provider_id="default")
        general_vision = Provider(provider_id="vision")
        frame_provider = Provider(
            ['{"summary":"画面里有人在街边撑伞","details":["夜色里有灯光"]}'],
            provider_id="frame",
        )
        calls = []

        async def get_provider(provider_id=""):
            calls.append(provider_id)
            return {"frame-model": frame_provider, "vision-model": general_vision}.get(
                provider_id, default_provider
            )

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider)
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "vision-model"},
                "sight_config": {"frame_provider": "frame-model"},
            }
        )
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                ),
            },
        )()

        notes, assets = await runtime._describe_sight_frames(
            SightClip(source="D:/tmp/rain.mp4"),
            [SightFrame(path=Path("frame-1.jpg"), second=8.0, label="00:08")],
        )

        self.assertEqual(calls, ["frame-model"])
        self.assertEqual(notes, ["00:08：画面里有人在街边撑伞（夜色里有灯光）"])
        self.assertEqual(assets, [])
        self.assertEqual(len(frame_provider.vision_prompts), 1)
        self.assertEqual(general_vision.vision_prompts, [])

    async def test_describe_frames_uses_current_default_provider_when_unset(self):
        from support import (
            Context,
            DailyLifeRuntime,
            LifeSettings,
            Provider,
            async_return,
        )

        default_provider = Provider(
            ['{"summary":"画面里有窗边咖啡杯","details":[]}'], provider_id="default"
        )
        calls = []

        async def get_provider(provider_id=""):
            calls.append(provider_id)
            return default_provider

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider)
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "vision-model"},
                "sight_config": {"frame_provider": ""},
            }
        )
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(get_provider),
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                ),
            },
        )()

        notes, assets = await runtime._describe_sight_frames(
            SightClip(source="D:/tmp/cafe.mp4"),
            [SightFrame(path=Path("frame-1.jpg"), second=1.0, label="00:01")],
        )

        self.assertEqual(calls, [""])
        self.assertEqual(notes, ["00:01：画面里有窗边咖啡杯"])
        self.assertEqual(assets, [])
        self.assertEqual(len(default_provider.vision_prompts), 1)

    async def test_sight_brief_uses_summary_provider(self):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        default_provider = Provider(provider_id="default")
        summary_provider = Provider(
            ['{"summary":"音频里在介绍雨夜咖啡店","details":["提到了窗边暖光"]}'],
            provider_id="summary",
        )
        provider_ids = []

        class Composer:
            async def _get_provider(self, provider_id=""):
                provider_ids.append(provider_id)
                return {"summary-model": summary_provider}.get(
                    provider_id, default_provider
                )

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"summary_provider": "summary-model"}}
        )
        runtime.composer = Composer()

        summary, details = await SightBrief(runtime).summarize(
            SightClip(source="D:/tmp/cafe.mp4"),
            transcript="视频里有人介绍雨夜咖啡店。",
        )

        self.assertEqual(provider_ids, ["summary-model"])
        self.assertEqual(summary, "音频里在介绍雨夜咖啡店")
        self.assertIn("提到了窗边暖光", details)
        self.assertEqual(len(summary_provider.prompts), 1)
        self.assertEqual(default_provider.prompts, [])

    async def test_sight_brief_checkpoint_resumes_after_saved_partial_group(self):
        import json

        from core.sight.checkpoint import audio_outline_checkpointed
        from core.sight.sample import sight_cache_dir
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp())
        runtime.context = Context(Provider(provider_id="default"))
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"summary_provider": "summary-model"}}
        )
        runtime.composer = type(
            "Composer",
            (),
            {
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                )
            },
        )()

        brief = SightBrief(runtime)
        calls = []

        async def call_model(
            provider, call_llm, composer, prompt, *, prefix, provider_id=""
        ):
            calls.append(prompt)
            if "片段 5/5" in prompt:
                return '{"summary":"第五段主线","details":["第五段细节"]}'
            return '{"summary":"合并后的主线","details":["合并后的细节"]}'

        brief._call_model = call_model  # type: ignore[assignment]

        transcript = " ".join([f"word{i}_" + ("x" * 1000) for i in range(20)])
        checkpoint_dir = sight_cache_dir(runtime.data_path) / "brief"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_key = brief._checkpoint_key(
            SightClip(source="D:/tmp/long.mp4"),
            transcript,
            [],
            {},
            provider_id="summary-model",
        )
        digest = (
            __import__("hashlib")
            .sha256(f"{checkpoint_key}|summary-model".encode("utf-8", errors="ignore"))
            .hexdigest()[:32]
        )
        checkpoint_path = checkpoint_dir / f"{digest}.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "checkpoint_key": checkpoint_key,
                    "provider_id": "summary-model",
                    "stage": "groups",
                    "next_chunk": 4,
                    "partials": ["已完成的前半段", "前置第二段", "前置第三段"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        summary, details = await audio_outline_checkpointed(
            brief,
            Provider(provider_id="summary-model"),
            None,
            runtime.composer,
            transcript,
            {},
            provider_id="summary-model",
            checkpoint_key=checkpoint_key,
        )

        self.assertEqual(summary, "合并后的主线")
        self.assertEqual(details, ["音频主线：合并后的主线", "合并后的细节"])
        self.assertEqual(len(calls), 2)
        self.assertIn("片段 5/5", calls[0])
        self.assertIn("分段音频主线", calls[1])

    async def test_sight_note_uses_summary_provider(self):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        default_provider = Provider(provider_id="default")
        summary_provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "paragraphs": ["视频讲了窗边暖光。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="summary",
        )
        provider_ids = []

        class Composer:
            async def _get_provider(self, provider_id=""):
                provider_ids.append(provider_id)
                return {"summary-model": summary_provider}.get(
                    provider_id, default_provider
                )

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"summary_provider": "summary-model"}}
        )
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            transcript="视频讲了雨夜咖啡店。",
            metadata={"title": "雨夜咖啡店"},
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertEqual(provider_ids, ["summary-model"])
        self.assertIn("# 雨夜咖啡店", markdown)
        self.assertEqual(len(summary_provider.prompts), 1)
        self.assertIn("仅返回 JSON 对象本体", summary_provider.prompts[0])
        self.assertIn('"sections"', summary_provider.prompts[0])
        self.assertIn(
            '"role": "overview/core/fact/data/analysis/risk/suggestion/other，可空"',
            summary_provider.prompts[0],
        )
        self.assertNotIn('"summary"', summary_provider.prompts[0])
        self.assertNotIn("AI 总结", summary_provider.prompts[0])
        self.assertIn('"start": "00:00"', summary_provider.prompts[0])
        self.assertIn('"end": "00:25"', summary_provider.prompts[0])
        self.assertIn(
            '"paragraphs": ["背景或核心判断，可空"]', summary_provider.prompts[0]
        )
        self.assertIn(
            '"bullets": ["事实、依据、比较、影响或建议，可空"]',
            summary_provider.prompts[0],
        )
        self.assertIn('"quotes": ["关键原话或结论，可空"]', summary_provider.prompts[0])
        self.assertNotIn('"content"', summary_provider.prompts[0])
        self.assertNotIn('"claims"', summary_provider.prompts[0])
        self.assertNotIn("已有内容线索", summary_provider.prompts[0])
        self.assertIn("你是专业的视频总结助手", summary_provider.prompts[0])
        self.assertIn("仅返回 JSON 对象本体", summary_provider.prompts[0])
        self.assertIn("彻底省略广告、推广口播", summary_provider.prompts[0])
        self.assertIn("返回空的 sections", summary_provider.prompts[0])
        self.assertIn("视频认为", summary_provider.prompts[0])
        self.assertNotIn("通常形成 4-8 个章节", summary_provider.prompts[0])
        self.assertNotIn("主题判断 → 关键事实或依据", summary_provider.prompts[0])
        self.assertIn("根据素材主题自然划分", summary_provider.prompts[0])
        self.assertIn("不把有足够内容的独立主题强行合并", summary_provider.prompts[0])
        self.assertIn(
            "背景、核心观点、事实与数据、依据或机制、比较与影响、结论与建议",
            summary_provider.prompts[0],
        )
        self.assertIn("只使用素材实际包含的部分", summary_provider.prompts[0])
        self.assertIn("**短标签**：具体说明", summary_provider.prompts[0])
        self.assertIn("少量标记核心概念、关键数据和结论", summary_provider.prompts[0])
        self.assertIn("短标签两侧的 `**` 必须保留", summary_provider.prompts[0])
        self.assertIn("人物、数字、案例、条件和结论", summary_provider.prompts[0])
        self.assertIn("这是排版倾向，实际数量由素材决定", summary_provider.prompts[0])
        self.assertIn("最后一个 section 固定为", summary_provider.prompts[0])
        self.assertIn("title=“总结与参考建议”", summary_provider.prompts[0])
        self.assertIn(
            "该 section 的 start/end 必须留空字符串",
            summary_provider.prompts[0],
        )
        self.assertIn("不要求写成行动清单", summary_provider.prompts[0])
        self.assertIn("不要求由视频作者明确提出", summary_provider.prompts[0])
        self.assertIn("不得冒充作者原话", summary_provider.prompts[0])
        self.assertIn(
            "**专业模式**：提供完整、正式的结构化总结",
            summary_provider.prompts[0],
        )
        self.assertEqual(default_provider.prompts, [])

    async def test_professional_note_chunks_long_transcript_without_omitting_windows(
        self,
    ):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "start": "00:00",
                                "end": "00:10",
                                "title": "第一段",
                                "content": "保留第一段的例子、数据和解释。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "sections": [
                            {
                                "start": "00:10",
                                "end": "00:20",
                                "title": "第二段",
                                "content": "保留第二段的步骤、结论和建议。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            ],
            provider_id="summary",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"note_max_transcript_chars": 2000}}
        )
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/long-note.mp4", name="长视频"),
            transcript="甲" * 1500 + "乙" * 1500,
            metadata={
                "title": "长视频",
                "transcript_segments": [
                    {"start": 0, "end": 10, "text": "甲" * 1500},
                    {"start": 10, "end": 20, "text": "乙" * 1500},
                ],
            },
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertIn("## 第一段\n\n⏱ 00:00", markdown)
        self.assertNotIn("00:00-00:10", markdown)
        self.assertNotIn("## AI 总结", markdown)
        self.assertIn("保留第一段的例子、数据和解释。", markdown)
        self.assertIn("## 第二段\n\n⏱ 00:10", markdown)
        self.assertNotIn("00:10-00:20", markdown)
        self.assertIn("保留第二段的步骤、结论和建议。", markdown)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("连续时间窗 1/2", provider.prompts[0])
        self.assertIn("连续时间窗 2/2", provider.prompts[1])
        metrics = insight.metadata["sight_metrics"]
        self.assertEqual(metrics["professional_chunk_count"], 2)
        self.assertEqual(metrics["professional_section_count"], 3)
        self.assertEqual(metrics["professional_empty_chunk_count"], 0)

    async def test_professional_note_accepts_empty_ad_window_without_retry_or_output(
        self,
    ):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "start": "00:00",
                                "end": "00:10",
                                "role": "core",
                                "title": "案件争议焦点",
                                "content": "这里保留案件的有效内容。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"sections": []}, ensure_ascii=False),
            ]
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"note_max_transcript_chars": 2000}}
        )
        runtime.composer = Composer()
        useful_text = "案件有效内容" * 180
        ad_text = "广告推广口播" * 180
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/ad-note.mp4", name="广告过滤测试"),
            transcript=useful_text + ad_text,
            metadata={
                "title": "广告过滤测试",
                "transcript_segments": [
                    {"start": 0, "end": 10, "text": useful_text},
                    {"start": 10, "end": 20, "text": ad_text},
                ],
            },
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertIn("这里保留案件的有效内容。", markdown)
        self.assertNotIn("广告推广口播", markdown)
        self.assertNotIn("广告已省略", markdown)
        self.assertEqual(len(provider.prompts), 2)
        metrics = insight.metadata["sight_metrics"]
        self.assertEqual(metrics["professional_model_calls"], 2)
        self.assertEqual(metrics["professional_empty_chunk_count"], 1)
        self.assertEqual(metrics["professional_section_count"], 2)

    async def test_professional_note_resumes_saved_long_video_chunks(self):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        first_provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "start": "00:00",
                                "end": "00:10",
                                "title": "第一段",
                                "content": "第一段已保存。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="summary",
        )
        second_provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "start": "00:10",
                                "end": "00:20",
                                "title": "第二段",
                                "content": "第二段续跑完成。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="summary",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.current_provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, **kwargs):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.context = Context(first_provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"note_max_transcript_chars": 2000}}
        )
        runtime.current_provider = first_provider
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/resume-note.mp4", name="断点测试"),
            transcript="甲" * 1500 + "乙" * 1500,
            metadata={
                "title": "断点测试",
                "transcript_segments": [
                    {"start": 0, "end": 10, "text": "甲" * 1500},
                    {"start": 10, "end": 20, "text": "乙" * 1500},
                ],
            },
        )

        with self.assertRaises(SightNoteError):
            await SightNote(runtime).compose(insight)

        runtime.current_provider = second_provider
        markdown = await SightNote(runtime).compose(insight)

        self.assertIn("第一段已保存。", markdown)
        self.assertIn("第二段续跑完成。", markdown)
        self.assertEqual(len(second_provider.prompts), 1)
        self.assertIn("连续时间窗 2/2", second_provider.prompts[0])
        checkpoint_dir = runtime.data_path.parent / "sight" / "professional_notes"
        self.assertEqual(list(checkpoint_dir.glob("*.json")), [])

    def test_professional_note_keeps_markdown_content_without_duplicate_summary(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="完整笔记"),
            metadata={"title": "完整笔记"},
        )
        payload = {
            "sections": [
                {
                    "start": "00:00",
                    "end": "00:30",
                    "title": "完整论证",
                    "content": "先说明背景。\n\n- 数据：80%\n- 例子：夜间充电\n\n最后给出建议。",
                }
            ],
            "summary": "最后给出建议。",
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        self.assertIn("- 数据：80%", markdown)
        self.assertIn("- 例子：夜间充电", markdown)
        self.assertEqual(markdown.count("## 总结与参考建议"), 1)

    def test_professional_note_ignores_unused_summary_field(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/video.mp4", name="分析测试"),
            metadata={"title": "分析测试"},
        )
        payload = {
            "summary": "核心矛盾不是一次争吵，而是边界控制持续侵入个人选择。",
            "sections": [
                {
                    "start": "00:00",
                    "end": "00:30",
                    "title": "控制关系如何形成",
                    "content": "父母通过替代决策逐步削弱了当事人的自主空间。",
                }
            ],
        }

        markdown = _payload_markdown(insight, payload, style="professional")

        self.assertNotIn("## AI 总结", markdown)
        self.assertNotIn("核心矛盾不是一次争吵", markdown)
        self.assertIn("## 控制关系如何形成\n\n⏱ 00:00", markdown)
        self.assertNotIn("00:00-00:30", markdown)

    def test_professional_note_keeps_distinct_topics_with_same_start_time(self):
        from core.sight.note import _merge_sections

        sections = _merge_sections(
            [],
            [
                {
                    "start": "00:50",
                    "end": "00:55",
                    "title": "面试受阻",
                    "content": "当事人的面试被突然打断。",
                },
                {
                    "start": "00:50",
                    "end": "01:02",
                    "title": "边界越界",
                    "content": "家人直接接管交流，暴露持续控制问题。",
                },
            ],
        )

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["title"], "面试受阻")
        self.assertEqual(sections[1]["title"], "边界越界")

    def test_all_note_styles_sort_sections_by_source_time(self):
        from core.sight.note import _merge_sections

        additions = [
            {
                "start": "02:13",
                "end": "02:40",
                "title": "比较不同处理方式",
                "content": "先讨论不同处理方式的后果。",
            },
            {
                "start": "01:31",
                "end": "02:05",
                "title": "提炼核心原则",
                "content": "再说明判断原则和依据。",
            },
        ]

        professional = _merge_sections([], additions, style="professional")
        detailed = _merge_sections([], additions, style="detailed")

        self.assertEqual(
            [item["title"] for item in professional],
            ["提炼核心原则", "比较不同处理方式"],
        )
        self.assertEqual(
            [item["title"] for item in detailed],
            ["提炼核心原则", "比较不同处理方式"],
        )

    async def test_professional_note_accepts_visual_only_evidence(self):
        from support import Context, DailyLifeRuntime, Provider

        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "画面概述",
                                "paragraphs": ["画面里有窗边咖啡杯。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="summary",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/silent.mp4", name="无声画面"),
            summary="画面里有窗边咖啡杯。",
            frame_notes=["00:12 画面里有窗边咖啡杯。"],
            metadata={"title": "无声画面"},
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertIn("## 画面概述", markdown)
        self.assertIn("画面里有窗边咖啡杯", markdown)
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("关键画面辅助证据", provider.prompts[0])

    async def test_sight_note_fails_without_summary_model(self):
        from support import DailyLifeRuntime

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            transcript="视频讲了雨夜咖啡店。",
            metadata={"title": "雨夜咖啡店"},
        )

        with self.assertRaisesRegex(SightNoteError, "总结模型不可用"):
            await SightNote(runtime).compose(insight)

    async def test_sight_note_fails_on_empty_summary_response(self):
        from support import Context, DailyLifeRuntime, Provider

        provider = Provider([""], provider_id="summary")

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            transcript="视频讲了雨夜咖啡店。",
            metadata={"title": "雨夜咖啡店"},
        )

        with self.assertRaisesRegex(SightNoteError, "返回空内容"):
            await SightNote(runtime).compose(insight)

        self.assertEqual(len(provider.prompts), 2)

    async def test_sight_note_retries_invalid_structure_once(self):
        from support import Context, DailyLifeRuntime, Provider

        provider = Provider(
            [
                "这不是 JSON。",
                json.dumps(
                    {
                        "sections": [
                            {
                                "role": "analysis",
                                "start": "00:00",
                                "end": "00:10",
                                "title": "问题如何形成",
                                "content": "第二次返回了有效的专业分析。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ],
            provider_id="summary",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/retry.mp4", name="格式重试"),
            transcript="这里解释问题如何形成。",
            metadata={"title": "格式重试"},
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertIn("第二次返回了有效的专业分析。", markdown)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("上次返回格式无效", provider.prompts[1])
        metrics = insight.metadata["sight_metrics"]
        self.assertEqual(metrics["professional_model_calls"], 2)
        self.assertEqual(metrics["professional_format_retries"], 1)

    async def test_sight_note_fails_on_invalid_summary_json(self):
        from support import Context, DailyLifeRuntime, Provider

        provider = Provider(
            ["# 雨夜咖啡店\n\n## 概述\n\n这不是 JSON。"], provider_id="summary"
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            transcript="视频讲了雨夜咖啡店。",
            metadata={"title": "雨夜咖啡店"},
        )

        with self.assertRaisesRegex(SightNoteError, "JSON 无法解析"):
            await SightNote(runtime).compose(insight)

        self.assertEqual(len(provider.prompts), 2)

    async def test_professional_note_uses_frames_as_evidence_without_rendering_them(
        self,
    ):
        from support import Context, DailyLifeRuntime, LifeSettings, Provider

        frame_path = "D:/tmp/frame-12.jpg"
        summary_provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "窗边暖光",
                                "time": "00:12",
                                "paragraphs": ["这里讲到窗边的咖啡杯。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="summary",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            summary_provider, providers={"summary": summary_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"summary_provider": "summary"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return summary_provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            transcript="00:12 讲到窗边的咖啡杯。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {
                        "path": frame_path,
                        "label": "00:12",
                        "second": 12,
                        "note": "窗边有咖啡杯",
                    }
                ],
            },
        )

        markdown = await SightNote(runtime).compose(insight)

        self.assertNotIn("![00:12 关键帧]", markdown)
        self.assertNotIn(frame_path, markdown)
        self.assertNotIn("## 关键画面", markdown)
        self.assertIn("⏱ 00:12", markdown)
        self.assertIn("00:12：窗边有咖啡杯", summary_provider.prompts[0])
        self.assertNotIn("可引用关键帧", summary_provider.prompts[0])
        self.assertNotIn("Content-[", summary_provider.prompts[0])
        self.assertNotIn("Screenshot-[", summary_provider.prompts[0])
        self.assertNotIn(frame_path, summary_provider.prompts[0])

    async def test_sight_note_rebuilds_frame_images_and_inserts_missing_frames(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-10.jpg", "label": "00:10", "second": 10},
                    {"path": "D:/tmp/frame-20.jpg", "label": "00:20", "second": 20},
                    {"path": "D:/tmp/frame-35.jpg", "label": "00:35", "second": 35},
                ],
            },
        )
        markdown = SightNote.normalize(
            insight,
            (
                "# 雨夜咖啡店\n\n"
                "## 00:10 开场\n\n"
                "![00:10 关键帧](D:/tmp/frame-10.jpg)\n\n"
                "开场内容。\n\n"
                "## 00:20 转折\n\n"
                "中段内容。\n\n"
                "## 00:35 结尾\n\n"
                "结尾内容。"
            ),
        )

        self.assertEqual(markdown.count("![00:10 关键帧](D:/tmp/frame-10.jpg)"), 1)
        self.assertIn("![00:20 关键帧](D:/tmp/frame-20.jpg)", markdown)
        self.assertIn("![00:35 关键帧](D:/tmp/frame-35.jpg)", markdown)
        self.assertNotIn("## 关键画面", markdown)
        self.assertLess(
            markdown.index("## 00:10 开场"), markdown.index("![00:10 关键帧]")
        )
        self.assertLess(
            markdown.index("## 00:20 转折"), markdown.index("![00:20 关键帧]")
        )
        self.assertLess(
            markdown.index("## 00:35 结尾"), markdown.index("![00:35 关键帧]")
        )

    async def test_sight_note_does_not_insert_frame_under_untimed_heading(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-10.jpg", "label": "00:10", "second": 10}
                ],
            },
        )
        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 概述\n\n这里只是概述。\n\n- `00:10` 开始介绍咖啡店。",
        )

        self.assertNotIn("## 概述\n\n![00:10 关键帧]", markdown)
        self.assertIn(
            "- `00:10` 开始介绍咖啡店。\n\n![00:10 关键帧](D:/tmp/frame-10.jpg)",
            markdown,
        )
        self.assertNotIn("## 关键画面", markdown)

    async def test_sight_note_skips_frames_when_markdown_has_no_timestamp(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-10.jpg", "label": "00:10", "second": 10}
                ],
            },
        )

        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 概述\n\n这里只是没有时间戳的概述。",
        )

        self.assertNotIn("关键帧", markdown)
        self.assertNotIn("## 关键画面", markdown)
        self.assertNotIn("D:/tmp/frame-10.jpg", markdown)

    async def test_sight_note_removes_model_frame_line_by_filename(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {
                        "path": "/tmp/daily_life_test/frames/key/frame_01_00_03.jpg",
                        "label": "00:03",
                        "second": 3,
                    }
                ],
            },
        )
        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 00:03 开场\n\n![00:03 关键帧](frame_01_00_03.jpg)\n\n开场内容。",
        )

        self.assertEqual(markdown.count("![00:03 关键帧]"), 1)
        self.assertIn(
            "![00:03 关键帧](/tmp/daily_life_test/frames/key/frame_01_00_03.jpg)",
            markdown,
        )
        self.assertNotIn("](frame_01_00_03.jpg)", markdown)

    async def test_sight_note_inserts_frame_image_from_body_timestamp_line(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨天咖啡店"),
            summary="视频讲了雨天咖啡店的窗边场景。",
            metadata={
                "title": "雨天咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-10.jpg", "label": "00:10", "second": 10}
                ],
            },
        )

        markdown = SightNote.normalize(
            insight,
            "# 雨天咖啡店\n\n## 概述\n\n✨ 00:10：窗边的咖啡杯亮起来了。",
        )

        self.assertIn("D:/tmp/frame-10.jpg", markdown)
        self.assertIn("✨ 00:10：窗边的咖啡杯亮起来了。\n\n![00:10", markdown)
        self.assertLess(
            markdown.index("✨ 00:10：窗边的咖啡杯亮起来了。"),
            markdown.index("![00:10"),
        )
        self.assertNotIn("## 关键画面", markdown)

    async def test_sight_note_normalizes_bililnote_style_markers(self):
        insight = SightInsight(
            clip=SightClip(source="D:/tmp/cafe.mp4", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店的窗边场景。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-12.jpg", "label": "00:12", "second": 12}
                ],
                "transcript_segments": [{"start": 12, "text": "窗边的灯光亮起来了。"}],
            },
        )

        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 概述 *Content-[00:12]\n\n这里开始讲窗边的灯光。 *Screenshot-[00:12]",
        )

        self.assertIn("## 00:12 概述", markdown)
        self.assertIn("![00:12 关键帧](D:/tmp/frame-12.jpg)", markdown)
        self.assertNotIn("*Content-", markdown)
        self.assertNotIn("*Screenshot-", markdown)
        self.assertNotIn("概述 *", markdown)
        self.assertNotIn("灯光。 *", markdown)
        self.assertNotIn("<!--", markdown)

    async def test_sight_note_keeps_timestamp_heading_without_source_jump_link(self):
        insight = SightInsight(
            clip=SightClip(source="https://example.com/watch", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店的窗边场景。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-12.jpg", "label": "00:12", "second": 12}
                ],
                "transcript_segments": [{"start": 12, "text": "窗边的灯光亮起来了。"}],
            },
        )

        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 概述 *Content-[00:12]\n\n这里开始讲窗边的灯光。",
        )

        self.assertIn("## 00:12 概述", markdown)
        self.assertNotIn("原片 @", markdown)
        self.assertNotIn("https://example.com/watch?t=12", markdown)

    async def test_sight_note_accepts_plain_marker_without_asterisks(self):
        insight = SightInsight(
            clip=SightClip(source="https://example.com/watch", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店的窗边场景。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-12.jpg", "label": "00:12", "second": 12}
                ],
            },
        )

        markdown = SightNote.normalize(
            insight,
            "# 雨夜咖啡店\n\n## 概述 Content-[00:12]\n\n这里开始讲窗边的灯光。 Screenshot-[00:12]",
        )

        self.assertIn("## 00:12 概述", markdown)
        self.assertIn("![00:12 关键帧](D:/tmp/frame-12.jpg)", markdown)
        self.assertNotIn("Content-", markdown)
        self.assertNotIn("Screenshot-", markdown)
        self.assertNotIn("<!--", markdown)

    async def test_sight_note_sanitizes_render_artifacts(self):
        insight = SightInsight(
            clip=SightClip(source="https://example.com/watch", name="雨夜咖啡店"),
            summary="视频讲了雨夜咖啡店的窗边场景。",
            metadata={
                "title": "雨夜咖啡店",
                "frames": [
                    {"path": "D:/tmp/frame-03.jpg", "label": "00:03", "second": 3},
                    {"path": "D:/tmp/frame-09.jpg", "label": "00:09", "second": 9},
                ],
            },
        )

        markdown = SightNote.normalize(
            insight,
            (
                "# 雨夜咖啡店\n\n"
                "## 00:03 现象描述 (Screenshot-[00:03])\n\n"
                "可用关键帧：83张（/tmp/frames）\n\n"
                "<!--00:09-->\n\n"
                "```quote\n"
                "名言引用 (Content-[00:09])\n"
                "```\n\n"
                "## 00:09 名言引用 ()\n\n"
                "内容。"
            ),
        )

        self.assertIn("## 00:03 现象描述", markdown)
        self.assertIn("00:09 名言引用", markdown)
        self.assertIn("![00:03 关键帧](D:/tmp/frame-03.jpg)", markdown)
        self.assertIn("![00:09 关键帧](D:/tmp/frame-09.jpg)", markdown)
        self.assertNotIn("可用关键帧", markdown)
        self.assertNotIn("Content-", markdown)
        self.assertNotIn("Screenshot-", markdown)
        self.assertNotIn("<!--", markdown)
        self.assertNotIn("```", markdown)
        self.assertNotIn("()", markdown)
