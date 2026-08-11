from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...life.tools import extract_json_from_text
from ...outcome import ToolResultText
from ...media.base import REFERENCE_IMAGE_MAX_BYTES, image_mime_and_ext
from ...paths import (
    expand_path,
    path_exists,
    path_is_file,
    path_size,
    runtime_data_root,
)
from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX


REVERSE_PROMPT_PROFILES = {
    "通用": "完整但简洁地提取最影响复现效果的主体、场景、构图、镜头、光线、色彩、材质和画面风格。",
    "通用超详细": "逐层拆解全部可见关键细节，以高信息密度组织最终提示词，优先追求画面复现度。",
    "生活照": "重点保留真实生活感、随手拍逻辑、自然姿态、非完美构图、现场光线、真实纹理和偶然细节。",
    "人像": "重点保留人物外观、妆发、表情、姿态、服装配饰、景别、空间关系、光线结构和人像气质。",
    "CCD人像": "重点保留 CCD、Y2K 或胶片快照的设备观感、闪光、噪点、压缩、过曝暗角、抓拍偶然性和人物状态。",
    "棚拍": "重点保留棚拍布光、背景材质、主体摆位、清晰度、反光阴影、修饰程度和商业摄影质感。",
    "棚拍人像": "重点保留人物妆造、服装材质、布光位置、背景布景、眼神光、肤色、焦段感和主体分离方式。",
    "古风": "重点保留服饰制式、发饰妆容、器物道具、年代感、空间层次、光影氛围、画面气韵和材质纹理。",
    "古风特调": "重点保留人物气质、服饰层次、布景器物、烟雾花影、色彩气韵、镜头景深和国风幻想或写真质感。",
    "商品": "重点保留商品主体、包装文字、材质纹理、摆放角度、背景道具、商业布光、反光阴影和拍摄方式。",
    "视觉封面": "重点保留封面视觉中心、主体识别度、主体位置、标题或文案留白、裁切安全区、前中后景层次、主色对比和视觉动线；只记录实际可见文字，不虚构标题或品牌文案。",
    "设计视觉": "重点保留版式结构、网格与对齐、文字层级、可见文字、字体视觉倾向、色板、间距、形状图标、装饰元素、信息分区和视觉动线；无法辨认的文字留空，不把设计意图当作画面事实。",
    "插画": "重点保留媒介画风、线条、上色、角色设计、服装道具、场景层次、笔触材质、光影和构图节奏。",
}

REVERSE_ANALYSIS_SECTIONS = (
    (
        "主体",
        "subject",
        (
            ("", "main"),
            ("特征", "attributes"),
            ("动作", "action"),
            ("互动", "interaction"),
        ),
    ),
    (
        "人物造型",
        "appearance",
        (("面部", "face"), ("发型", "hair"), ("体态姿势", "body_pose")),
    ),
    (
        "服装物件",
        "outfit_props",
        (("服装", "clothing"), ("配饰", "accessories"), ("道具", "props")),
    ),
    (
        "风格",
        "style",
        (
            ("媒介", "medium"),
            ("类型", "genre"),
            ("情绪", "mood"),
            ("视觉特征", "reference_look"),
        ),
    ),
    (
        "场景",
        "environment",
        (
            ("类型", "scene_type"),
            ("前景", "foreground"),
            ("中景", "midground"),
            ("背景", "background"),
            ("空间关系", "spatial_relation"),
        ),
    ),
    (
        "构图",
        "composition",
        (
            ("景别", "shot_type"),
            ("主体位置", "subject_placement"),
            ("裁切", "crop"),
            ("透视", "perspective"),
            ("留白", "negative_space"),
            ("引导线", "leading_lines"),
            ("对称", "symmetry"),
        ),
    ),
    (
        "镜头",
        "camera",
        (
            ("角度", "angle"),
            ("焦段感", "focal_length_feel"),
            ("景深", "depth_of_field"),
            ("设备观感", "device_look"),
            ("画质特征", "quality_artifacts"),
        ),
    ),
    (
        "光线",
        "lighting",
        (
            ("来源", "source"),
            ("方向", "direction"),
            ("性质", "quality"),
            ("效果", "effect"),
            ("时间感", "time_of_day"),
        ),
    ),
    (
        "材质",
        "material",
        (
            ("表面", "surface"),
            ("微观纹理", "micro_detail"),
            ("光学属性", "optical_properties"),
        ),
    ),
    (
        "色彩",
        "color",
        (
            ("色板", "palette"),
            ("色温", "temperature"),
            ("对比", "contrast"),
            ("调色", "color_grade"),
        ),
    ),
    (
        "可见文字",
        "visible_text",
        (("内容", "content"), ("作用", "role"), ("排除项", "excluded")),
    ),
    (
        "渲染",
        "rendering",
        (
            ("真实程度", "realism"),
            ("修饰程度", "retouch"),
            ("优先特征", "priority_terms"),
        ),
    ),
)


class RuntimeReverseMediaMixin:
    @staticmethod
    def _reverse_prompt_clean(value: Any, limit: int | None = None) -> str:
        text = " ".join(str(value or "").strip().split())
        if isinstance(limit, int) and limit > 0:
            text = text[:limit]
        return text.strip()

    @classmethod
    def _reverse_prompt_list(cls, value: Any, limit: int = 8) -> list[str]:
        if isinstance(value, str):
            items = [
                part.strip()
                for part in value.replace("，", ",").replace("、", ",").split(",")
            ]
        else:
            try:
                items = list(value or [])
            except TypeError:
                items = []
        result = []
        for item in items:
            text = cls._reverse_prompt_clean(item, 32)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _reverse_prompt_analysis_item_text(
        cls, value: Any, fields: tuple[tuple[str, str], ...]
    ) -> str:
        if isinstance(value, dict):
            parts = []
            for label, key in fields:
                text = cls._reverse_prompt_analysis_item_text(value.get(key), ())
                if text:
                    parts.append(f"{label}{text}" if label else text)
            if parts:
                return "，".join(parts)
            return ""
        if isinstance(value, (list, tuple, set)):
            parts = []
            for item in value:
                text = cls._reverse_prompt_analysis_item_text(item, ())
                if text and text not in parts:
                    parts.append(text)
                if len(parts) >= 12:
                    break
            return "、".join(parts)
        return cls._reverse_prompt_clean(value, 240)

    @classmethod
    def _reverse_prompt_analysis_payload(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for _, key, fields in REVERSE_ANALYSIS_SECTIONS:
            section = value.get(key)
            if isinstance(section, dict):
                normalized = {}
                for _, field_key in fields:
                    field = section.get(field_key)
                    if isinstance(field, list):
                        items = cls._reverse_prompt_list(field, 12)
                        if items:
                            normalized[field_key] = items
                    else:
                        text = cls._reverse_prompt_clean(field, 500)
                        if text:
                            normalized[field_key] = text
                if normalized:
                    result[key] = normalized
                continue
            text = cls._reverse_prompt_clean(section, 500)
            if text:
                result[key] = text
        return result

    @classmethod
    def _reverse_prompt_analysis_text(cls, value: Any) -> str:
        analysis = cls._reverse_prompt_analysis_payload(value)
        parts = []
        for label, key, fields in REVERSE_ANALYSIS_SECTIONS:
            text = cls._reverse_prompt_analysis_item_text(analysis.get(key), fields)
            if text:
                parts.append(f"{label}：{text}")
        return "；".join(parts)

    def _reverse_prompt_text_from_payload(
        self, payload: dict[str, Any], fallback: str = ""
    ) -> str:
        for key in ("prompt", "image_prompt", "text", "result", "zh_prompt"):
            text = self._reverse_prompt_clean(payload.get(key))
            if text:
                return text
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            text = self._reverse_prompt_analysis_text(analysis)
            if text:
                return text
        return self._reverse_prompt_clean(fallback)

    def _reverse_prompt_payload_from_text(self, text: str) -> dict[str, Any]:
        cleaned = self._reverse_prompt_json_source(text)
        payload = extract_json_from_text(cleaned)
        if isinstance(payload, dict):
            return payload
        raw = str(cleaned or "").strip()
        if not raw:
            return {}
        for candidate in self._reverse_prompt_json_candidates(raw):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        return {"prompt": raw}

    @staticmethod
    def _reverse_prompt_json_source(text: str) -> str:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        for old, new in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
            raw = raw.replace(old, new)
        return raw

    @staticmethod
    def _reverse_prompt_json_candidates(text: str) -> list[str]:
        raw = str(text or "").strip()
        candidates = []
        if raw.startswith("{") and raw.endswith("}"):
            candidates.append(raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if 0 <= start < end:
            candidates.append(raw[start : end + 1])
        result = []
        for item in candidates:
            if item and item not in result:
                result.append(item)
        return result

    def _reverse_prompt_profile_instruction(self, profile: str) -> tuple[str, str]:
        name = self._reverse_prompt_clean(profile, 24) or "通用"
        instruction = REVERSE_PROMPT_PROFILES.get(name)
        if instruction:
            return name, instruction
        return name, f"按“{name}”这个用户指定方向取舍画面重点，同时保持可见内容准确。"

    def _remember_reverse_prompt_for_scope(
        self, event: Any, prompt: str, image: str = ""
    ) -> None:
        scope = self._event_session_id(event)
        text = self._reverse_prompt_clean(prompt)
        if not scope or not text:
            return
        store = getattr(self, "_life_reverse_prompt_cache", None)
        if not isinstance(store, dict):
            store = {}
            self._life_reverse_prompt_cache = store
        store[scope] = {
            "prompt": text,
            "image": str(image or "").strip(),
        }

    async def _save_reverse_prompt_for_scope(
        self,
        event: Any,
        *,
        prompt: str,
        image: str = "",
        title: str = "",
        keywords: list[str] | None = None,
        ratio: str = "",
        usage: str = "",
        profile: str = "",
        source_prompt: str = "",
        analysis: dict[str, Any] | None = None,
        source_image_hash: str = "",
    ) -> None:
        self._remember_reverse_prompt_for_scope(event, prompt, image)
        scope = self._event_session_id(event)
        archive = getattr(self, "archive", None)
        saver = getattr(archive, "save_reverse_prompt", None)
        if not scope or not callable(saver):
            return
        try:
            await saver(
                {
                    "scope": scope,
                    "prompt": prompt,
                    "image_path": image,
                    "title": title,
                    "keywords": keywords or [],
                    "ratio": ratio,
                    "usage": usage,
                    "profile": profile,
                    "source_prompt": source_prompt,
                    "analysis": analysis or {},
                    "source_image_hash": source_image_hash,
                }
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片反推提示词缓存写入失败：{exc}")

    def _last_reverse_prompt_for_scope(self, event: Any) -> str:
        scope = self._event_session_id(event)
        if not scope:
            return ""
        store = getattr(self, "_life_reverse_prompt_cache", None)
        if not isinstance(store, dict):
            return ""
        item = store.get(scope)
        if isinstance(item, dict):
            return self._reverse_prompt_clean(item.get("prompt"))
        return self._reverse_prompt_clean(item)

    async def _last_reverse_prompt_record_for_scope(self, event: Any) -> dict[str, str]:
        scope = self._event_session_id(event)
        if not scope:
            return {}
        prompt = self._last_reverse_prompt_for_scope(event)
        image = self._last_reverse_reference_for_scope(event)
        if prompt:
            return {"prompt": prompt, "image": image}
        archive = getattr(self, "archive", None)
        loader = getattr(archive, "get_latest_reverse_prompt", None)
        if not callable(loader):
            return {}
        try:
            record = await loader(scope)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片反推提示词缓存读取失败：{exc}")
            return {}
        if not record:
            return {}
        prompt = self._reverse_prompt_clean(getattr(record, "prompt", ""))
        image = str(getattr(record, "image_path", "") or "").strip()
        if prompt:
            self._remember_reverse_prompt_for_scope(event, prompt, image)
            return {"prompt": prompt, "image": image}
        return {}

    async def _cached_reverse_prompt_for_scope(
        self,
        event: Any,
        *,
        source_image_hash: str,
        profile: str,
        source_prompt: str,
    ) -> Any | None:
        scope = self._event_session_id(event)
        archive = getattr(self, "archive", None)
        loader = getattr(archive, "get_reverse_prompt_by_fingerprint", None)
        if not scope or not source_image_hash or not callable(loader):
            return None
        try:
            return await loader(scope, source_image_hash, profile, source_prompt)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片反推缓存读取失败：{exc}")
            return None

    def _format_reverse_prompt_result(self, value: Any) -> str:
        if isinstance(value, dict):
            payload = value
        else:
            payload = {
                "title": getattr(value, "title", ""),
                "prompt": getattr(value, "prompt", ""),
                "keywords": getattr(value, "keywords", []),
                "ratio": getattr(value, "ratio", ""),
                "usage": getattr(value, "usage", ""),
                "analysis": getattr(value, "analysis", {}),
            }
        title = self._reverse_prompt_clean(payload.get("title"), 24)
        prompt = self._reverse_prompt_clean(payload.get("prompt"))
        keywords = self._reverse_prompt_list(payload.get("keywords"), 12)
        ratio = self._reverse_prompt_clean(payload.get("ratio"), 24)
        usage = self._reverse_prompt_clean(payload.get("usage"), 24)
        analysis = self._reverse_prompt_clean(
            self._reverse_prompt_analysis_text(payload.get("analysis")), 2000
        )

        lines = []
        if title:
            lines.extend(["标题：", title, ""])
        lines.extend(["图片反推提示词：", prompt])
        extras = []
        if keywords:
            extras.append(f"关键词：{'、'.join(keywords)}")
        if ratio:
            extras.append(f"比例：{ratio}")
        if usage:
            extras.append(f"适合：{usage}")
        if extras:
            lines.extend(["", "补充建议：", "；".join(extras)])
        if analysis:
            lines.extend(["", "画面拆解：", analysis])
        return "\n".join(lines)

    def _last_reverse_reference_for_scope(self, event: Any) -> str:
        scope = self._event_session_id(event)
        if not scope:
            return ""
        store = getattr(self, "_life_reverse_prompt_cache", None)
        if not isinstance(store, dict):
            return ""
        item = store.get(scope)
        if not isinstance(item, dict):
            return ""
        return str(item.get("image") or "").strip()

    def _reverse_reference_cache_dir(self) -> Path:
        return runtime_data_root(getattr(self, "data_path", None)) / "reverse"

    async def _reverse_reference_bytes(
        self, image: str, *, referer: str = ""
    ) -> tuple[bytes, str]:
        image = str(image or "").strip()
        loader = getattr(
            getattr(getattr(self, "media", None), "image", None),
            "_load_reference_image",
            None,
        )
        if callable(loader):
            if referer:
                data, mime = await loader(image, referer=referer)
            else:
                data, mime = await loader(image)
            return bytes(data or b""), str(mime or "").strip()
        if image.startswith("base64://"):
            data = base64.b64decode(image.removeprefix("base64://"), validate=True)
            return data, image_mime_and_ext(data)[0]
        if image.startswith(("http://", "https://")):
            return b"", ""
        path = await asyncio.to_thread(expand_path, image)
        if not await asyncio.to_thread(path_is_file, path):
            raise FileNotFoundError(f"参考图片不存在：{image}")
        if await asyncio.to_thread(path_size, path) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        data = await asyncio.to_thread(path.read_bytes)
        return data, image_mime_and_ext(data)[0]

    async def _persist_reverse_reference_image(self, image: str) -> tuple[str, str]:
        image = str(image or "").strip()
        if not image:
            return "", ""
        data, _ = await self._reverse_reference_bytes(image)
        if not data:
            return image, ""
        if len(data) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        mime, suffix = image_mime_and_ext(data)
        source_image_hash = await asyncio.to_thread(
            lambda: hashlib.sha256(data).hexdigest()
        )
        digest = source_image_hash[:24]
        target_dir = self._reverse_reference_cache_dir()
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        target = target_dir / f"reverse_reference_{digest}{suffix}"
        if not await asyncio.to_thread(target.exists):
            await asyncio.to_thread(target.write_bytes, data)
        logger.debug(f"{LOG_PREFIX} 图片反推参考图已缓存：{target.name}（{mime}）")
        return str(target), source_image_hash

    def _reverse_prompt_contract(self, source_prompt: str, profile: str) -> str:
        profile_name, profile_instruction = self._reverse_prompt_profile_instruction(
            profile
        )
        source_text = self._reverse_prompt_clean(source_prompt, 500) or "无"
        fixed = """角色设定：你是一位视觉导演与图像逆向分析师。
核心任务：分析上传图片，反推出一条可复制、可直接用于生图的中文完整提示词，并给出结构化画面拆解。

事实边界：
- 只依据可见画面。无法确认的身份、年龄、性别、品牌、材质、设备、焦段和光圈留空，不用常识或所选方案补齐。
- 可以描述“广角感、长焦感、浅景深、手机感、CCD 感”等视觉倾向；没有可靠画面依据时，不写准确毫米数、光圈值、相机或镜头型号。
- 先记录可见事实，再按反推方案调整信息权重。方案只决定重点，不得添加原图不存在的人物、道具、环境、光效或风格细节。

分析维度：
- 主体与人物造型：主体类型、数量、可见外观、面部、发型、体态、动作、手势、表情、互动关系。
- 服装与物件：服装颜色、版型、局部结构、材质表现、配饰、手持物、道具和可见图案。
- 风格与环境：媒介类型、画面类型、情绪、视觉特征、地点、时间天气、前景中景背景及具体空间关系。
- 构图与镜头：景别、主体位置、裁切、留白、透视、引导线、对称关系、拍摄角度、焦段感、景深、设备观感和画质瑕疵。
- 光线与材质：光源、方向、软硬强弱、阴影高光、反光轮廓光、时间感、表面材质、微观纹理和可见光学属性。
- 色彩与渲染：主色、色温、对比、调色、真实程度、修饰程度和最影响复现效果的优先特征。
- 可见文字：有意义的正文、包装或招牌文字尽量准确记录；无法辨认时留空，不猜写。水印、平台界面、字幕和拍摄叠层写入 excluded，默认不进入最终 prompt；用户明确要求保留时除外。

最终提示词：
- prompt 使用自然、连贯、高信息密度的中文，写成可直接生图的完整画面描述，不写成评价、教程、字段清单或分析报告。
- 优先保留最影响复现效果的主体、人物造型、动作、场景、构图、镜头、光线、色彩、材质和风格特征。
- keywords 给 6 到 12 个中文短词；ratio 写观察到的画面比例，不确定时留空；usage 只能写“文生图”或“图生图参考”。

输出要求：
- 只输出下面结构的严格 JSON 对象，不要 Markdown、代码块、解释或多余前后缀。
- 未观察到的可选字段使用空字符串或空数组；不要删除 title、prompt、keywords、ratio、usage、analysis。
{
  "title": "",
  "prompt": "",
  "keywords": [],
  "ratio": "",
  "usage": "",
  "analysis": {
    "subject": {"main": "", "attributes": [], "action": "", "interaction": ""},
    "appearance": {"face": "", "hair": "", "body_pose": ""},
    "outfit_props": {"clothing": "", "accessories": "", "props": ""},
    "style": {"medium": "", "genre": "", "mood": "", "reference_look": ""},
    "environment": {"scene_type": "", "foreground": "", "midground": "", "background": "", "spatial_relation": ""},
    "composition": {"shot_type": "", "subject_placement": "", "crop": "", "perspective": "", "negative_space": "", "leading_lines": "", "symmetry": ""},
    "camera": {"angle": "", "focal_length_feel": "", "depth_of_field": "", "device_look": "", "quality_artifacts": []},
    "lighting": {"source": "", "direction": "", "quality": "", "effect": "", "time_of_day": ""},
    "material": {"surface": "", "micro_detail": "", "optical_properties": []},
    "color": {"palette": "", "temperature": "", "contrast": "", "color_grade": ""},
    "visible_text": {"content": "", "role": "", "excluded": []},
    "rendering": {"realism": "", "retouch": "", "priority_terms": []}
  }
}"""
        dynamic = f"""参考重点：{source_text}
反推方案：{profile_name}
方案取舍：{profile_instruction}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="反推参考")

    async def _reverse_prompt_call_vision(
        self,
        image: str,
        source_prompt: str = "",
        profile: str = "",
    ) -> dict[str, Any]:
        provider = await self._get_vision_provider()
        if not provider:
            raise RuntimeError("视觉模型不可用。")
        if not any(
            callable(getattr(provider, name, None))
            for name in ("text_chat", "image_chat", "vision_chat")
        ):
            raise RuntimeError("当前视觉模型不支持图片理解。")
        prompt = self._reverse_prompt_contract(source_prompt, profile)
        session_id = f"daily_life_reverse_image_{uuid.uuid4().hex[:8]}"
        try:
            result = await self._reverse_prompt_call_provider(
                provider, prompt, image, session_id
            )
            if result is None:
                raise RuntimeError("视觉模型未返回结果。")
            text = self._completion_text(result)
            payload = self._reverse_prompt_payload_from_text(text)
            payload["prompt"] = self._reverse_prompt_text_from_payload(payload, text)
            payload["title"] = self._reverse_prompt_clean(payload.get("title"), 24)
            payload["ratio"] = self._reverse_prompt_clean(payload.get("ratio"), 24)
            payload["usage"] = self._reverse_prompt_clean(payload.get("usage"), 24)
            payload["keywords"] = self._reverse_prompt_list(payload.get("keywords"), 12)
            payload["analysis"] = self._reverse_prompt_analysis_payload(
                payload.get("analysis")
            )
            return payload
        finally:
            cleanup = getattr(self, "close_text_session", None)
            if callable(cleanup):
                await cleanup(session_id)

    @staticmethod
    async def _reverse_prompt_call_provider(
        provider: Any, prompt: str, image: str, session_id: str
    ) -> Any:
        for name, kwargs in (
            (
                "text_chat",
                {"prompt": prompt, "image_urls": [image], "session_id": session_id},
            ),
            (
                "image_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
            (
                "vision_chat",
                {"prompt": prompt, "image": image, "session_id": session_id},
            ),
        ):
            method = getattr(provider, name, None)
            if not callable(method):
                continue
            try:
                result = method(**kwargs)
            except (TypeError, NotImplementedError, AttributeError):
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except (TypeError, NotImplementedError, AttributeError):
                continue
            return result
        return None

    async def life_image_reverse_prompt(
        self,
        event: Any,
        reference_image: str = "",
        source_prompt: str = "",
        profile: str = "",
    ) -> str:
        image = await self._resolve_life_image_reference_async(event, reference_image)
        if not image:
            return ToolResultText(
                "没有找到可反推的图片。",
                status="failed",
                media="image_reverse_prompt",
            )
        if (
            image
            and not image.startswith(("http://", "https://"))
            and not await asyncio.to_thread(path_exists, image)
        ):
            return ToolResultText(
                "没有找到可反推的图片。",
                status="failed",
                media="image_reverse_prompt",
            )
        profile_name, _ = self._reverse_prompt_profile_instruction(profile)
        source_text = self._reverse_prompt_clean(source_prompt, 500)
        try:
            (
                cached_image,
                source_image_hash,
            ) = await self._persist_reverse_reference_image(image)
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片反推参考图缓存失败：{error}")
            return ToolResultText(
                f"图片反推参考图缓存失败：{error}",
                status="failed",
                media="image_reverse_prompt",
            )

        cached = await self._cached_reverse_prompt_for_scope(
            event,
            source_image_hash=source_image_hash,
            profile=profile_name,
            source_prompt=source_text,
        )
        if cached and self._reverse_prompt_clean(getattr(cached, "prompt", "")):
            self._remember_reverse_prompt_for_scope(
                event,
                getattr(cached, "prompt", ""),
                cached_image or image,
            )
            logger.debug(f"{LOG_PREFIX} 图片反推命中缓存：{profile_name}")
            return ToolResultText(
                self._format_reverse_prompt_result(cached),
                status="ok",
                media="image_reverse_prompt",
            )

        try:
            payload = await self._reverse_prompt_call_vision(
                image, source_text, profile_name
            )
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片反推提示词失败：{error}")
            return ToolResultText(
                f"图片反推提示词失败：{error}",
                status="failed",
                media="image_reverse_prompt",
            )
        prompt = self._reverse_prompt_clean(payload.get("prompt"))
        if not prompt:
            return ToolResultText(
                "图片反推提示词失败：视觉模型未返回可用提示词",
                status="failed",
                media="image_reverse_prompt",
            )
        title = self._reverse_prompt_clean(payload.get("title"), 24)
        keywords = self._reverse_prompt_list(payload.get("keywords"), 12)
        ratio = self._reverse_prompt_clean(payload.get("ratio"), 24)
        usage = self._reverse_prompt_clean(payload.get("usage"), 24)
        analysis = self._reverse_prompt_analysis_payload(payload.get("analysis"))
        await self._save_reverse_prompt_for_scope(
            event,
            prompt=prompt,
            image=cached_image or image,
            title=title,
            keywords=keywords,
            ratio=ratio,
            usage=usage,
            profile=profile_name,
            source_prompt=source_text,
            analysis=analysis,
            source_image_hash=source_image_hash,
        )
        return ToolResultText(
            self._format_reverse_prompt_result(
                {
                    "title": title,
                    "prompt": prompt,
                    "keywords": keywords,
                    "ratio": ratio,
                    "usage": usage,
                    "analysis": analysis,
                }
            ),
            status="ok",
            media="image_reverse_prompt",
        )
