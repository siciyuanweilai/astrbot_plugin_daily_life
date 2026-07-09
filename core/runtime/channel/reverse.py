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
from ...media.shared import REFERENCE_IMAGE_MAX_BYTES, image_mime_and_ext
from ...paths import runtime_data_root
from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX


REVERSE_PROMPT_PROFILES = {
    "通用": "按高密度通用反推处理，完整提取主体、环境、动作、道具、文字、构图、镜头、光线、色彩、材质、纹理、画质和比例；每个关键元素都写出可复现的具体特征。",
    "通用超详细": "按超详细视觉反推处理，像视觉导演一样逐层拆解画面，把主体锚定、动作场景、美学光线、摄影画质、构图比例和生成约束连成一段可直接生图的中文完整提示词。",
    "生活照": "优先保留真实生活感、手机或相机随手拍逻辑、自然姿态、非完美构图、现场环境、光线时间感、真实皮肤纹理、衣物褶皱和不过度修饰的偶然细节。",
    "人像": "按专业人像摄影反推处理，重点保留人物五官气质、发型发色、妆容皮肤、表情眼神、姿态手势、服装配饰、景别视角、空间关系、光线结构、画质状态和照片气质。",
    "CCD人像": "按复古 CCD / Y2K / 胶片快照人像处理，重点保留低像素数码感、闪光灯或硬光、颗粒噪点、压缩感、过曝与暗角、生活抓拍偶然性、人物真实姿态和社交媒体照片气质。",
    "棚拍": "按商业棚拍处理，重点保留主光、辅光、轮廓光、背景光、背景材质、人物或商品摆位、服装妆造、清晰度、反光高光、修饰程度和商业摄影质感。",
    "棚拍人像": "按高精度棚拍人像处理，重点保留人物五官、妆造、服装材质、布光位置、背景布景、商业精修程度、眼神光、肤色表现、镜头焦段感和主体与背景分离方式。",
    "古风": "按古风人像或国风场景处理，重点保留服饰制式、发饰妆容、器物道具、场景年代感、空间层次、光影氛围、构图雅致感、画面气韵和材质纹理。",
    "古风特调": "按高密度古风特调处理，细写人物气质、服饰层次、发饰妆容、手持物、布景器物、烟雾花影、光影层次、色彩气韵、镜头景深和国风幻想或写真质感。",
    "商品": "按商品摄影处理，重点保留商品主体、包装信息、品牌文字、材质纹理、摆放角度、背景道具、商业布光、反光阴影、景深关系和可复现的拍摄方式。",
    "插画": "按插画或二次元画面处理，重点保留画风、线条、上色方式、角色设计、服装道具、场景层次、色彩气氛、光影渲染、笔触材质和构图节奏。",
}


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
            items = [part.strip() for part in value.replace("，", ",").replace("、", ",").split(",")]
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
    def _reverse_prompt_analysis_text(cls, value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        labels = [
            ("主体", value.get("subject")),
            ("动作", value.get("action")),
            ("服装物件", value.get("outfit_props")),
            ("场景", value.get("environment")),
            ("构图", value.get("composition")),
            ("镜头", value.get("camera")),
            ("光线", value.get("lighting")),
            ("色彩", value.get("color")),
            ("质感", value.get("texture")),
        ]
        parts = []
        for label, item in labels:
            text = cls._reverse_prompt_clean(item, 90)
            if text:
                parts.append(f"{label}：{text}")
        return "；".join(parts)

    def _reverse_prompt_text_from_payload(self, payload: dict[str, Any], fallback: str = "") -> str:
        for key in ("prompt", "image_prompt", "text", "result", "zh_prompt"):
            text = self._reverse_prompt_clean(payload.get(key))
            if text:
                return text
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            text = self._reverse_prompt_analysis_text(analysis)
            if text:
                return text
        parts = []
        for key in ("subject", "action", "outfit_props", "scene", "environment", "composition", "camera", "lighting", "color", "texture", "style", "details"):
            text = self._reverse_prompt_clean(payload.get(key), 160)
            if text:
                parts.append(text)
        prompt = "，".join(part for part in parts if part)
        return prompt or self._reverse_prompt_clean(fallback)

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

    def _remember_reverse_prompt_for_scope(self, event: Any, prompt: str, image: str = "") -> None:
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

    async def _reverse_reference_bytes(self, image: str) -> tuple[bytes, str]:
        image = str(image or "").strip()
        loader = getattr(getattr(getattr(self, "media", None), "image", None), "_load_reference_image", None)
        if callable(loader):
            data, mime = await loader(image)
            return bytes(data or b""), str(mime or "").strip()
        if image.startswith("base64://"):
            data = base64.b64decode(image.removeprefix("base64://"), validate=True)
            return data, image_mime_and_ext(data)[0]
        if image.startswith(("http://", "https://")):
            return b"", ""
        path = Path(image).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"参考图片不存在：{image}")
        if path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        data = await asyncio.to_thread(path.read_bytes)
        return data, image_mime_and_ext(data)[0]

    async def _persist_reverse_reference_image(self, image: str) -> str:
        image = str(image or "").strip()
        if not image:
            return ""
        data, _ = await self._reverse_reference_bytes(image)
        if not data:
            return image
        if len(data) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        mime, suffix = image_mime_and_ext(data)
        digest = hashlib.sha256(data).hexdigest()[:24]
        target_dir = self._reverse_reference_cache_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"reverse_reference_{digest}{suffix}"
        if not target.exists():
            await asyncio.to_thread(target.write_bytes, data)
        logger.debug(f"{LOG_PREFIX} 图片反推参考图已缓存：{target.name}（{mime}）")
        return str(target)

    def _reverse_prompt_contract(self, source_prompt: str, profile: str) -> str:
        profile_name, profile_instruction = self._reverse_prompt_profile_instruction(profile)
        source_text = self._reverse_prompt_clean(source_prompt, 500) or "无"
        fixed = """角色设定：你是一位视觉导演与图像逆向分析师。
核心任务：分析上传图片，反推出一条可复制、可用于生图的中文完整提示词。
总原则：只依据可见画面；优先保留图片里最影响相似度的主体、构图、镜头、光线、色彩、材质和细节；写成可直接生图的提示词，不写成评价、教程或普通说明。

分析维度：
- 主体锚定：人物或物体身份、数量、年龄感、性别表达、外貌轮廓、发型发色、表情眼神、身体姿态、手部动作和主体之间的互动关系。
- 造型物件：服装颜色、版型、领口袖口、褶皱材质、饰品、手持物、道具、文字内容、可见品牌或图案；看不见的不要补。
- 场景环境：地点类型、时间天气、前景中景远景、墙面地面、家具植物、车辆建筑、背景元素和具体方位关系。
- 摄影画质：照片类型、设备感、焦段视角、清晰区域、景深虚化、颗粒噪点、压缩感、真实皮肤或材质纹理、修饰程度。
- 光线色彩：主光来源、光线方向、软硬强弱、阴影位置、高光反光、轮廓光、眼神光、明暗反差、色温色调、后期风格。
- 构图比例：取景范围、横竖画幅、主体位置、留白、裁切、透视、框架构图、背景压缩、画面比例。

输出要求：
- 只输出严格 JSON，不要 Markdown、代码块、解释或多余前后缀。
- prompt 是最终可复制、可用于生图的中文完整提示词，必须体现动态参考里的参考重点和反推方案取舍；使用自然中文长句，信息密度高但语义连贯，尽量覆盖会影响相似度的可见细节。
- keywords 给 6 到 12 个中文短词。
- ratio 写观察到的画面比例，不确定写空字符串。
- usage 写“文生图”或“图生图参考”中更合适的一个。
{{
  "title": "",
  "prompt": "",
  "keywords": [],
  "ratio": "",
  "usage": "",
  "analysis": {{
    "subject": "",
    "action": "",
    "outfit_props": "",
    "environment": "",
    "composition": "",
    "camera": "",
    "lighting": "",
    "color": "",
    "texture": ""
  }}
}}"""
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
        if not any(callable(getattr(provider, name, None)) for name in ("text_chat", "image_chat", "vision_chat")):
            raise RuntimeError("当前视觉模型不支持图片理解。")
        prompt = self._reverse_prompt_contract(source_prompt, profile)
        session_id = f"daily_life_reverse_image_{uuid.uuid4().hex[:8]}"
        try:
            result = await self._reverse_prompt_call_provider(provider, prompt, image, session_id)
            if result is None:
                raise RuntimeError("视觉模型未返回结果。")
            text = self._completion_text(result)
            payload = self._reverse_prompt_payload_from_text(text)
            payload["prompt"] = self._reverse_prompt_text_from_payload(payload, text)
            payload["title"] = self._reverse_prompt_clean(payload.get("title"), 24)
            payload["ratio"] = self._reverse_prompt_clean(payload.get("ratio"), 24)
            payload["usage"] = self._reverse_prompt_clean(payload.get("usage"), 24)
            payload["keywords"] = self._reverse_prompt_list(payload.get("keywords"), 12)
            payload["analysis_text"] = self._reverse_prompt_analysis_text(payload.get("analysis"))
            return payload
        finally:
            cleanup = getattr(getattr(self, "composer", None), "_cleanup_conversation", None)
            if callable(cleanup):
                await cleanup(session_id)

    @staticmethod
    async def _reverse_prompt_call_provider(provider: Any, prompt: str, image: str, session_id: str) -> Any:
        for name, kwargs in (
            ("text_chat", {"prompt": prompt, "image_urls": [image], "session_id": session_id}),
            ("image_chat", {"prompt": prompt, "image": image, "session_id": session_id}),
            ("vision_chat", {"prompt": prompt, "image": image, "session_id": session_id}),
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
            return "没有找到可反推的图片。"
        if image and not image.startswith(("http://", "https://")) and not Path(image).expanduser().exists():
            return "没有找到可反推的图片。"
        try:
            cached_image = await self._persist_reverse_reference_image(image)
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片反推参考图缓存失败：{error}")
            return f"图片反推参考图缓存失败：{error}"
        try:
            payload = await self._reverse_prompt_call_vision(image, source_prompt, profile)
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片反推提示词失败：{error}")
            return f"图片反推提示词失败：{error}"
        prompt = self._reverse_prompt_clean(payload.get("prompt"))
        if not prompt:
            return "图片反推提示词失败：视觉模型未返回可用提示词"
        title = self._reverse_prompt_clean(payload.get("title"), 24)
        keywords = self._reverse_prompt_list(payload.get("keywords"), 12)
        ratio = self._reverse_prompt_clean(payload.get("ratio"), 24)
        usage = self._reverse_prompt_clean(payload.get("usage"), 24)
        analysis = self._reverse_prompt_clean(payload.get("analysis_text"), 500)
        profile_name, _ = self._reverse_prompt_profile_instruction(profile)
        await self._save_reverse_prompt_for_scope(
            event,
            prompt=prompt,
            image=cached_image or image,
            title=title,
            keywords=keywords,
            ratio=ratio,
            usage=usage,
            profile=profile_name,
            source_prompt=source_prompt,
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
