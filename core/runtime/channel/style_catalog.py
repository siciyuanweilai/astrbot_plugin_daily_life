from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import uuid
from typing import Any

from astrbot.api import logger
from PIL import Image, ImageOps, UnidentifiedImageError

from ...clock import today as life_today
from ...life.tools import extract_json_from_text
from ...media.base import REFERENCE_IMAGE_MAX_BYTES, image_mime_and_ext
from ...models import PreferenceRecord, StyleCatalogItemRecord
from ...outcome import ToolResultText
from ...paths import path_exists, runtime_data_root
from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX

_STYLE_KINDS = {"auto", "outfit", "hair", "both"}
_STYLE_ITEM_KINDS = {"outfit", "hair"}
_STYLE_FEEDBACK_SENTIMENTS = {"prefer", "dislike", "neutral", "archive"}
_STYLE_IMAGE_MAX_SIDE = 1600
_STYLE_IMAGE_JPEG_QUALITY = 90


class RuntimeStyleCatalogMixin:
    @staticmethod
    def _style_text(value: object, limit: int = 0) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit].strip() if limit > 0 else text

    @classmethod
    def _style_list(cls, value: object, limit: int = 8) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []
        result: list[str] = []
        for item in values:
            text = cls._style_text(item, 60)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _style_item_ids(value: object) -> list[int]:
        if isinstance(value, (str, int, float)):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []
        result = []
        for item in values:
            try:
                item_id = int(item)
            except (TypeError, ValueError):
                continue
            if item_id > 0 and item_id not in result:
                result.append(item_id)
        return result[:20]

    @classmethod
    def _style_attributes(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = cls._style_text(key, 48)
            if not normalized_key or normalized_key in {
                "present",
                "title",
                "description",
                "confidence",
            }:
                continue
            if isinstance(item, (list, tuple, set)):
                normalized = cls._style_list(item, 12)
            elif isinstance(item, dict):
                normalized = {
                    cls._style_text(sub_key, 40): cls._style_text(sub_value, 120)
                    for sub_key, sub_value in item.items()
                    if cls._style_text(sub_key, 40) and cls._style_text(sub_value, 120)
                }
            elif isinstance(item, (int, float, bool)):
                normalized = item
            else:
                normalized = cls._style_text(item, 240)
            if normalized not in ("", [], {}):
                result[normalized_key] = normalized
        return result

    @classmethod
    def _style_analysis_item(cls, payload: dict[str, Any], kind: str) -> dict[str, Any]:
        raw = payload.get(kind)
        if not isinstance(raw, dict) or raw.get("present") is not True:
            return {}
        description = cls._style_text(raw.get("description"), 600)
        if not description:
            return {}
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "kind": kind,
            "title": cls._style_text(raw.get("title"), 100),
            "description": description,
            "attributes": cls._style_attributes(raw),
            "confidence": max(0.0, min(confidence, 1.0)),
        }

    def _style_catalog_contract(self, note: str, kind: str) -> str:
        fixed = """角色设定：你是服装与发型视觉资料整理师。
任务：只依据上传图片的可见内容，把可复用的服装造型和发型分别整理成结构化候选。

事实边界：
- 服装与发型必须分开；服装描述不得混入头发，发型描述不得混入衣物。
- 完整穿搭可包含服装本体、鞋袜、包袋、首饰、头饰、妆容和美甲，但这些组成必须分别落在结构化字段中；图片不可见时留空。
- 不推断人物身份、姓名、年龄、性别、关系、身材数据、品牌、价格或未显示的商品参数。
- 商品标题、网页文字和用户备注只能帮助理解取舍，不能覆盖图片中的可见事实。
- 人物姿势、表情、体貌、背景、摄影构图和画面风格不是衣橱候选，不写进 description。
- 看不清的材质只能写视觉质感，不声称准确面料成分。

服装字段：
- description 写完整可复用造型，包含实际可见单品、主色、版型、长度和层次；鞋袜、配饰、妆容和美甲只在确实可见时简洁补充。
- category、colors、pieces、patterns、styles、seasons、scenes、weather_fit 使用短词数组。
- silhouette、neckline、sleeve、length、material_appearance、thickness、exposure_level 使用短文本。
- footwear、accessories、makeup、nails 分别记录可见鞋袜、配饰、妆容与美甲；不得把妆容或美甲写成服装材质。

发型字段：
- description 只写当前可见发型，包含长度/层次、刘海/分缝、扎法、发尾、质感、发饰中的有效信息。
- category、colors、styles、scenes、weather_fit、activity_fit、accessories 使用短词数组。
- length、bangs、parting、tie、texture、volume 使用短文本。

输出要求：
- 只返回严格 JSON 对象，不要 Markdown、代码块或解释。
- present 只有在图片中确实看得到且信息足够形成候选时才为 true；否则为 false。
{
  "image_summary": "只概括与服装和发型有关的可见信息",
  "outfit": {
    "present": true,
    "title": "简短候选名称",
    "description": "不含发型的完整服装造型",
    "category": [], "colors": [], "pieces": [], "patterns": [],
    "silhouette": "", "neckline": "", "sleeve": "", "length": "",
    "material_appearance": "", "thickness": "", "exposure_level": "",
    "footwear": [], "accessories": [], "makeup": [], "nails": [],
    "styles": [], "seasons": [], "scenes": [], "weather_fit": [],
    "confidence": 0.0
  },
  "hair": {
    "present": true,
    "title": "简短发型名称",
    "description": "不含服装的完整发型",
    "category": [], "colors": [], "length": "", "bangs": "",
    "parting": "", "tie": "", "texture": "", "volume": "",
    "accessories": [], "styles": [], "scenes": [], "weather_fit": [],
    "activity_fit": [], "confidence": 0.0
  }
}"""
        focus = {
            "outfit": "只保存服装候选；发型仍可分析，但不会入库。",
            "hair": "只保存发型候选；服装仍可分析，但不会入库。",
            "both": "服装与发型分别保存。",
            "auto": "按图片实际可见内容决定保存服装、发型或两者。",
        }.get(kind, "按图片实际可见内容决定。")
        dynamic = f"学习重点：{focus}\n用户补充：{self._style_text(note, 500) or '无'}"
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="视觉衣橱学习")

    @staticmethod
    def _prepare_style_catalog_image_bytes(data: bytes) -> bytes:
        """把网页图片转换为视觉模型稳定支持的静态 JPEG。"""
        try:
            with Image.open(BytesIO(data)) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                image.load()
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("参考图片尺寸无效")
                if max(image.size) > _STYLE_IMAGE_MAX_SIDE:
                    image.thumbnail(
                        (_STYLE_IMAGE_MAX_SIDE, _STYLE_IMAGE_MAX_SIDE),
                        Image.Resampling.LANCZOS,
                    )
                if image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                output = BytesIO()
                image.save(
                    output,
                    format="JPEG",
                    quality=_STYLE_IMAGE_JPEG_QUALITY,
                    optimize=True,
                )
                rendered = output.getvalue()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("参考图片不是可读取的图片内容") from exc
        if not rendered:
            raise ValueError("参考图片转换失败")
        return rendered

    async def _persist_style_catalog_image(
        self, image: str, *, source_url: str = ""
    ) -> tuple[str, str]:
        data, _ = await self._reverse_reference_bytes(image, referer=source_url)
        if not data:
            return image, ""
        if len(data) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        data = await asyncio.to_thread(self._prepare_style_catalog_image_bytes, data)
        if len(data) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片转换后仍然过大")
        mime, suffix = image_mime_and_ext(data)
        target_dir = (
            runtime_data_root(getattr(self, "data_path", None)) / "style_catalog"
        )
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        target = target_dir / f"style_{digest[:24]}{suffix}"
        if not await asyncio.to_thread(target.exists):
            await asyncio.to_thread(target.write_bytes, data)
        logger.debug(f"{LOG_PREFIX} 视觉衣橱参考图已缓存：{target.name}（{mime}）")
        return str(target), digest

    async def _analyze_style_catalog_image(
        self, image: str, *, note: str, kind: str
    ) -> dict[str, Any]:
        provider = await self._get_vision_provider()
        if not provider:
            raise RuntimeError("视觉模型不可用")
        session_id = f"daily_life_style_catalog_{uuid.uuid4().hex[:8]}"
        try:
            result = await self._reverse_prompt_call_provider(
                provider,
                self._style_catalog_contract(note, kind),
                image,
                session_id,
            )
            if result is None:
                raise RuntimeError("视觉模型未返回结果")
            payload = extract_json_from_text(self._completion_text(result))
            if not isinstance(payload, dict):
                raise RuntimeError("视觉模型未返回有效结构")
            return payload
        finally:
            cleanup = getattr(self, "close_text_session", None)
            if callable(cleanup):
                await cleanup(session_id)

    async def _learn_style_catalog_image(
        self,
        event: Any,
        image: str,
        *,
        source_url: str = "",
        source_kind: str = "user_image",
        note: str = "",
        kind: str = "auto",
    ) -> list[StyleCatalogItemRecord]:
        cached_image, image_hash = await self._persist_style_catalog_image(
            image,
            source_url=source_url,
        )
        if not image_hash:
            image_hash = hashlib.sha256(image.encode("utf-8")).hexdigest()
        payload = await self._analyze_style_catalog_image(
            cached_image or image, note=note, kind=kind
        )
        requested = _STYLE_ITEM_KINDS if kind in {"auto", "both"} else {kind}
        saved: list[StyleCatalogItemRecord] = []
        for item_kind in ("outfit", "hair"):
            if item_kind not in requested:
                continue
            analyzed = self._style_analysis_item(payload, item_kind)
            if not analyzed:
                continue
            record = await self.archive.upsert_style_catalog_item(
                {
                    **analyzed,
                    "image_path": cached_image or image,
                    "source_url": source_url,
                    "source_scope": self._event_session_id(event),
                    "source_kind": source_kind,
                    "source_image_hash": image_hash,
                }
            )
            if record:
                saved.append(record)
        return saved

    @staticmethod
    def _style_catalog_result_text(items: list[StyleCatalogItemRecord]) -> str:
        if not items:
            return "图片中没有足够清晰、可保存的服装或发型信息。"
        lines = ["已加入视觉衣橱候选："]
        for item in items:
            label = "服装" if item.kind == "outfit" else "发型"
            details = []
            attributes = item.attributes if isinstance(item.attributes, dict) else {}
            for detail_label, key in (
                ("鞋袜", "footwear"),
                ("配饰", "accessories"),
                ("妆容", "makeup"),
                ("美甲", "nails"),
            ):
                value = attributes.get(key)
                values = (
                    [str(part).strip() for part in value if str(part).strip()]
                    if isinstance(value, (list, tuple, set))
                    else [str(value).strip()]
                    if str(value or "").strip()
                    else []
                )
                if values:
                    details.append(f"{detail_label}：{'、'.join(values[:4])}")
            suffix = f"（{'；'.join(details)}）" if details else ""
            lines.append(
                f"- #{item.id} {label}：{item.title or item.description}{suffix}"
            )
        lines.append("这些只是造型候选，不代表当前已经换上。")
        return "\n".join(lines)

    async def life_style_learn(
        self,
        event: Any,
        reference_image: str = "",
        *,
        reference_images: list[str] | None = None,
        source_url: str = "",
        note: str = "",
        kind: str = "auto",
    ) -> str:
        normalized_kind = self._style_text(kind, 16).lower()
        if normalized_kind not in _STYLE_KINDS:
            normalized_kind = "auto"
        requested = [self._style_text(item, 1500) for item in (reference_images or [])]
        if self._style_text(reference_image, 1500):
            requested.insert(0, self._style_text(reference_image, 1500))
        images: list[str] = []
        for value in requested[:6]:
            resolved = await self._resolve_life_image_reference_async(event, value)
            if resolved and resolved not in images:
                images.append(resolved)
        if not images:
            resolved = await self._resolve_life_image_reference_async(event, "")
            if resolved:
                images.append(resolved)
        if not images:
            return ToolResultText(
                "没有找到可学习的图片。请发送、引用或提供一张商品/造型图片。",
                status="failed",
                media="style_catalog",
            )

        saved: list[StyleCatalogItemRecord] = []
        failures = []
        for image in images:
            if not image.startswith(
                ("http://", "https://")
            ) and not await asyncio.to_thread(path_exists, image):
                failures.append("图片不存在或临时文件已经失效")
                continue
            try:
                saved.extend(
                    await self._learn_style_catalog_image(
                        event,
                        image,
                        source_url=source_url,
                        source_kind="product_image" if source_url else "user_image",
                        note=note,
                        kind=normalized_kind,
                    )
                )
            except Exception as exc:
                failures.append(self._media_error_summary(exc))
        if not saved:
            error = failures[0] if failures else "未识别到可保存造型"
            return ToolResultText(
                f"视觉衣橱学习失败：{error}",
                status="failed",
                media="style_catalog",
            )
        return ToolResultText(
            self._style_catalog_result_text(saved),
            status="ok",
            media="style_catalog",
        )

    async def life_style_browse_learn(
        self,
        event: Any,
        query: str,
        *,
        kind: str = "auto",
        count: int = 3,
        note: str = "",
    ) -> str:
        query = self._style_text(query, 500)
        if not query:
            return ToolResultText(
                "缺少要浏览的服装或发型需求。",
                status="failed",
                media="style_catalog",
            )
        normalized_kind = self._style_text(kind, 16).lower()
        if normalized_kind not in _STYLE_KINDS:
            normalized_kind = "auto"
        search = getattr(self, "search", None)
        if search is None or not bool(getattr(search, "enabled", False)):
            return ToolResultText(
                "联网搜索未启用，暂时只能学习用户发送或指定的图片。",
                status="failed",
                media="style_catalog",
            )
        try:
            target_count = max(1, min(int(count or 3), 6))
        except (TypeError, ValueError):
            target_count = 3
        existing_items = await self.archive.get_style_catalog_items(
            status="", limit=100
        )
        existing_ids = {int(item.id or 0) for item in existing_items}
        saved: list[StyleCatalogItemRecord] = []
        saved_keys: set[tuple[int, str]] = set()
        failures: list[str] = []
        attempted = 0
        candidate_count = 0
        attempted_urls: set[str] = set()
        for depth in ("quick", "deep"):
            try:
                result = await search.search(
                    query,
                    depth=depth,
                    source_scope="web",
                    image_search=True,
                    image_understanding=True,
                    include_images=True,
                    include_image_descriptions=True,
                    auto_parameters=True,
                    umo=self._event_session_id(event),
                    trace_id=f"style_catalog_{uuid.uuid4().hex[:8]}",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures.append(f"{depth} 搜索失败：{self._media_error_summary(exc)}")
                logger.debug(
                    f"{LOG_PREFIX} 视觉衣橱联网学习搜索失败：深度={depth}；"
                    f"原因={failures[-1][:240]}"
                )
                continue
            assets = list(getattr(result, "images", []) or [])
            logger.debug(
                f"{LOG_PREFIX} 视觉衣橱联网学习搜索完成：查询={query[:120]}；"
                f"深度={depth}；状态={getattr(result, 'status', '') or 'unknown'}；"
                f"图片候选={len(assets)}"
            )
            if str(getattr(result, "status", "")) != "ok" or not assets:
                failures.append(f"{depth} 搜索没有返回图片候选")
                continue
            candidate_count += len(assets)
            for asset in assets[:20]:
                if len(saved) >= target_count:
                    break
                if not isinstance(asset, dict):
                    continue
                image = self._style_text(asset.get("url"), 1500)
                if not image or image in attempted_urls:
                    continue
                attempted_urls.add(image)
                attempted += 1
                source_url = self._style_text(asset.get("source_url"), 1500)
                asset_note = self._style_text(asset.get("description"), 300)
                combined_note = "；".join(
                    item for item in (note, asset_note) if self._style_text(item)
                )
                try:
                    learned = await self._learn_style_catalog_image(
                        event,
                        image,
                        source_url=source_url,
                        source_kind="web_image",
                        note=combined_note,
                        kind=normalized_kind,
                    )
                    if not learned:
                        failures.append("图片中没有足够清晰的目标造型")
                        logger.debug(
                            f"{LOG_PREFIX} 视觉衣橱联网学习图片跳过："
                            f"序号={attempted}；原因={failures[-1]}"
                        )
                        continue
                    for item in learned:
                        key = (int(item.id or 0), str(item.kind or ""))
                        if key[0] in existing_ids:
                            failures.append("相同图片的造型已在视觉衣橱中")
                            continue
                        if key in saved_keys:
                            continue
                        saved_keys.add(key)
                        saved.append(item)
                except Exception as exc:
                    failures.append(self._media_error_summary(exc))
                    logger.debug(
                        f"{LOG_PREFIX} 视觉衣橱联网学习图片失败：序号={attempted}；"
                        f"原因={failures[-1][:240]}"
                    )
            if len(saved) >= target_count:
                break
            if depth == "quick":
                logger.debug(
                    f"{LOG_PREFIX} 视觉衣橱快速搜索结果不足，继续深度搜索："
                    f"目标={target_count}；当前成功={len(saved)}"
                )
        logger.debug(
            f"{LOG_PREFIX} 视觉衣橱联网学习保存完成：查询={query[:120]}；"
            f"目标={target_count}；候选={candidate_count}；尝试={attempted}；"
            f"成功={len(saved)}；"
            f"跳过或失败={len(failures)}"
        )
        if not saved:
            if candidate_count <= 0:
                return ToolResultText(
                    "没有搜索到可用的商品或造型图片。",
                    status="failed",
                    media="style_catalog",
                )
            error = failures[0] if failures else "搜索图片无法完成视觉分析"
            return ToolResultText(
                f"浏览学习失败：{error}",
                status="failed",
                media="style_catalog",
            )
        result_text = self._style_catalog_result_text(saved)
        if len(saved) < target_count:
            result_text = (
                f"{result_text}\n"
                f"本次需要 {target_count} 条，实际保存 {len(saved)} 条；"
                "其余搜索图片无法形成可靠候选。"
            )
        return ToolResultText(
            result_text,
            status="ok",
            media="style_catalog",
        )

    async def life_style_catalog_list(
        self, event: Any, *, kind: str = "", limit: int = 8
    ) -> str:
        del event
        normalized_kind = self._style_text(kind, 16).lower()
        if normalized_kind not in _STYLE_ITEM_KINDS:
            normalized_kind = ""
        try:
            safe_limit = max(1, min(int(limit or 8), 20))
        except (TypeError, ValueError):
            safe_limit = 8
        items = await self.archive.get_style_catalog_items(
            kind=normalized_kind, status="active", limit=safe_limit
        )
        if not items:
            return (
                "视觉衣橱还没有可用候选。"
                "如果用户本轮要求找或搜索网上新穿搭，必须继续调用 "
                "life_style_browse_learn；life_style_catalog 不会新增候选。"
            )
        return (
            f"{self._style_catalog_result_text(items)}\n"
            "如果用户本轮还要求找或搜索网上新穿搭，必须继续调用 "
            "life_style_browse_learn，不能只回复稍后再搜。"
        )

    async def _style_feedback_decision(
        self,
        items: list[StyleCatalogItemRecord],
        feedback: str,
    ) -> dict[str, Any]:
        provider = await self.get_text_provider()
        if not provider:
            raise RuntimeError("文本模型不可用")
        item_text = "\n".join(
            f"- #{item.id} [{item.kind}] {item.title}：{item.description}"
            for item in items
        )
        fixed = """你负责把用户对视觉衣橱候选的自然语言反馈转换成结构化调整。
只依据用户反馈与给出的候选，不从词表、固定关键词或人物性别猜测偏好。
- prefer 表示更喜欢或更适合；dislike 表示不喜欢或不适合；neutral 表示只补充说明；archive 表示明确不要再使用。
- score_delta 范围 -1.0 到 1.0，单次反馈按明确程度调整，不要夸大。
- preference_points 只提炼长期可复用的审美、舒适度或场景偏好；只针对某一张图的意见不要上升为长期偏好。
- category 只能写 outfit、hair 或 style。
只返回严格 JSON：
{
  "adjustments": [{"item_id": 1, "sentiment": "prefer | dislike | neutral | archive", "score_delta": 0.0, "reason": "简短理由"}],
  "preference_points": [{"category": "outfit | hair | style", "content": "稳定偏好", "weight": 0.1}]
}"""
        dynamic = f"候选：\n{item_text}\n\n用户反馈：{self._style_text(feedback, 1000)}"
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="衣橱反馈")
        session_id = f"daily_life_style_feedback_{uuid.uuid4().hex[:8]}"
        try:
            text = await self.call_text_model(provider, prompt, session_id)
            payload = extract_json_from_text(text)
            return payload if isinstance(payload, dict) else {}
        finally:
            await self.close_text_session(session_id)

    async def life_style_feedback(
        self,
        event: Any,
        feedback: str,
        *,
        item_ids: list[int] | None = None,
    ) -> str:
        feedback = self._style_text(feedback, 1000)
        if not feedback:
            return ToolResultText(
                "没有收到对视觉衣橱候选的反馈。",
                status="failed",
                media="style_catalog",
            )
        normalized_ids = self._style_item_ids(item_ids or [])
        if normalized_ids:
            items = await self.archive.get_style_catalog_items(
                status="", ids=normalized_ids, limit=len(normalized_ids)
            )
        else:
            items = await self.archive.get_recent_style_catalog_items(
                self._event_session_id(event), limit=1
            )
        if not items:
            return ToolResultText(
                "没有找到需要调整的视觉衣橱候选。",
                status="failed",
                media="style_catalog",
            )
        try:
            payload = await self._style_feedback_decision(items, feedback)
        except Exception as exc:
            return ToolResultText(
                f"视觉衣橱反馈处理失败：{self._media_error_summary(exc)}",
                status="failed",
                media="style_catalog",
            )
        allowed_ids = {item.id for item in items}
        changed: list[StyleCatalogItemRecord] = []
        for adjustment in payload.get("adjustments") or []:
            if not isinstance(adjustment, dict):
                continue
            try:
                item_id = int(adjustment.get("item_id") or 0)
            except (TypeError, ValueError):
                continue
            sentiment = self._style_text(adjustment.get("sentiment"), 16).lower()
            if (
                item_id not in allowed_ids
                or sentiment not in _STYLE_FEEDBACK_SENTIMENTS
            ):
                continue
            status = (
                "archived"
                if sentiment == "archive"
                else "active"
                if sentiment == "prefer"
                else ""
            )
            updated = await self.archive.add_style_catalog_feedback(
                item_id,
                scope=self._event_session_id(event),
                feedback=feedback,
                sentiment=sentiment,
                score_delta=adjustment.get("score_delta") or 0.0,
                reason=self._style_text(adjustment.get("reason"), 240),
                status=status,
            )
            if updated:
                changed.append(updated)

        preferences = []
        for point in payload.get("preference_points") or []:
            if not isinstance(point, dict):
                continue
            category = self._style_text(point.get("category"), 16).lower()
            content = self._style_text(point.get("content"), 240)
            if category not in {"outfit", "hair", "style"} or not content:
                continue
            try:
                weight = float(point.get("weight") or 0.1)
            except (TypeError, ValueError):
                weight = 0.1
            preferences.append(
                PreferenceRecord(
                    category=category,
                    content=content,
                    weight=max(0.1, min(weight, 1.0)),
                    evidence="用户对视觉衣橱候选的明确反馈",
                    source="style_catalog_feedback",
                )
            )
        if preferences:
            await self.archive.upsert_preferences(preferences, life_today().isoformat())
        if not changed and not preferences:
            return ToolResultText(
                "没有从这条反馈中确认可执行的候选调整。",
                status="failed",
                media="style_catalog",
            )
        changed_ids = "、".join(f"#{item.id}" for item in changed)
        parts = []
        if changed_ids:
            parts.append(f"已更新候选 {changed_ids}")
        if preferences:
            parts.append(f"已沉淀 {len(preferences)} 条长期造型偏好")
        return ToolResultText(
            "；".join(parts) + "。",
            status="ok",
            media="style_catalog",
        )


__all__ = ["RuntimeStyleCatalogMixin"]
