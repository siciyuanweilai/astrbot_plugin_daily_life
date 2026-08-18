from __future__ import annotations

import asyncio
import hashlib
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from astrbot.api import logger

from ...clock import today as life_today
from ...life.tools import extract_json_from_text
from ...media.base import REFERENCE_IMAGE_MAX_BYTES, image_mime_and_ext
from ...models import (
    STYLE_CATALOG_CARRY_MODES,
    STYLE_CATALOG_HOME_PRESENCE,
    STYLE_CATALOG_KIND_LABELS,
    STYLE_CATALOG_KIND_SET,
    STYLE_CATALOG_KINDS,
    PreferenceRecord,
    StyleCatalogItemRecord,
)
from ...outcome import ToolResultText
from ...paths import STYLE_CATALOG_DIR_NAME, path_exists, runtime_data_root
from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX

_STYLE_KINDS = {"auto", "both", *STYLE_CATALOG_KIND_SET}
_STYLE_ITEM_KINDS = STYLE_CATALOG_KIND_SET
_STYLE_FEEDBACK_SENTIMENTS = {"prefer", "dislike", "neutral", "disable", "archive"}
_STYLE_IMAGE_MAX_SIDE = 1600
_STYLE_IMAGE_JPEG_QUALITY = 90
_STYLE_REVIEW_CONFIDENCE = 0.72
_STYLE_PERCEPTUAL_DISTANCE = 5


class RuntimeStyleCatalogMixin:
    @staticmethod
    def _style_asset_key(value: object) -> str:
        """生成仅用于本轮搜索去重的稳定图片地址键。"""
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = urlsplit(text)
        except ValueError:
            return text.casefold()
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            return text.casefold()
        try:
            query = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.casefold().startswith("utm_")
                and key.casefold() not in {"ref", "referer", "source", "spm"}
            ]
        except ValueError:
            query = []
        query.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
        host = (parsed.hostname or "").casefold()
        try:
            port = parsed.port
        except ValueError:
            return text.casefold()
        default_port = (parsed.scheme.casefold() == "http" and port == 80) or (
            parsed.scheme.casefold() == "https" and port == 443
        )
        host_port = host if not port or default_port else f"{host}:{port}"
        return urlunsplit(
            (
                parsed.scheme.casefold(),
                host_port,
                parsed.path.rstrip("/") or "/",
                urlencode(query, doseq=True),
                "",
            )
        ).casefold()

    @staticmethod
    def _style_asset_dimensions(asset: dict[str, Any]) -> tuple[int, int]:
        """读取搜索服务提供的尺寸元数据；缺失时不臆测。"""
        values = []
        for key in ("width", "height"):
            try:
                value = int(asset.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            values.append(max(0, value))
        return values[0], values[1]

    @classmethod
    def _style_asset_is_candidate(cls, asset: dict[str, Any]) -> bool:
        """只过滤明确不适合作为视觉参考的尺寸，不用关键词猜测图片内容。"""
        image = cls._style_text(asset.get("url"), 1500)
        if not image:
            return False
        width, height = cls._style_asset_dimensions(asset)
        return not (width and height and min(width, height) < 240)

    @staticmethod
    def _requested_style_kinds(kind: str) -> set[str]:
        return (
            set(STYLE_CATALOG_KINDS)
            if kind in {"auto", "both"}
            else {kind}
            if kind in STYLE_CATALOG_KIND_SET
            else set(STYLE_CATALOG_KINDS)
        )

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
                "visual_prompt",
                "confidence",
            }:
                continue
            if isinstance(item, (list, tuple, set)):
                if all(isinstance(entry, dict) for entry in item):
                    normalized = []
                    for entry in item:
                        nested = cls._style_attributes(entry)
                        if nested:
                            normalized.append(nested)
                else:
                    normalized = cls._style_list(item, 12)
            elif isinstance(item, dict):
                normalized = cls._style_attributes(item)
            elif isinstance(item, (int, float, bool)):
                normalized = item
            else:
                normalized = cls._style_text(
                    item,
                    800
                    if normalized_key
                    in {"home_description", "outing_reserve_description"}
                    else 240,
                )
            if normalized not in ("", [], {}):
                result[normalized_key] = normalized
        return result

    @classmethod
    def _style_analysis_item(cls, payload: dict[str, Any], kind: str) -> dict[str, Any]:
        raw = payload.get(kind)
        if not isinstance(raw, dict) or raw.get("present") is not True:
            return {}
        # visual_prompt 只用于兼容上一个开发版本的模型返回；新数据统一
        # 将详细视觉提示词保存到 description，避免维护两份重复描述。
        description = cls._style_text(
            raw.get("visual_prompt") or raw.get("description"), 800
        )
        if not description:
            return {}
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        attributes = cls._style_attributes(raw)
        if kind in {"footwear", "accessory"}:
            home_presence = cls._style_text(
                attributes.get("home_presence"), 16
            ).lower()
            attributes["home_presence"] = (
                home_presence
                if home_presence in STYLE_CATALOG_HOME_PRESENCE
                else "unknown"
            )
        if kind == "accessory":
            carry_mode = cls._style_text(attributes.get("carry_mode"), 16).lower()
            attributes["carry_mode"] = (
                carry_mode if carry_mode in STYLE_CATALOG_CARRY_MODES else "unknown"
            )
        if kind == "outfit":
            profiles = attributes.get("component_roles")
            normalized_profiles = []
            for profile in profiles if isinstance(profiles, list) else []:
                if not isinstance(profile, dict):
                    continue
                profile_kind = cls._style_text(profile.get("kind"), 16).lower()
                name = cls._style_text(
                    profile.get("name") or profile.get("description"), 120
                )
                if profile_kind not in {"footwear", "accessory"} or not name:
                    continue
                role = cls._style_text(profile.get("home_presence"), 16).lower()
                carry_mode = cls._style_text(profile.get("carry_mode"), 16).lower()
                normalized_profiles.append(
                    {
                        "kind": profile_kind,
                        "name": name,
                        "home_presence": role
                        if role in STYLE_CATALOG_HOME_PRESENCE
                        else "unknown",
                        "carry_mode": carry_mode
                        if carry_mode in STYLE_CATALOG_CARRY_MODES
                        else "unknown",
                    }
                )
            if normalized_profiles:
                attributes["component_roles"] = normalized_profiles
            else:
                attributes.pop("component_roles", None)
        return {
            "kind": kind,
            "title": cls._style_text(raw.get("title"), 100),
            "description": description,
            "attributes": attributes,
            "confidence": max(0.0, min(confidence, 1.0)),
        }

    def _style_catalog_contract(self, note: str, kind: str) -> str:
        fixed = """角色设定：你是视觉衣橱资料整理师。
任务：只依据上传图片的可见内容，把完整造型与可独立复用的组成项分别整理成结构化候选。

事实边界：
- 套装、上装、下装、鞋袜、配饰、发型、妆容和美甲是八个独立类别，不能把一个类别的事实写进另一个类别。
- outfit 只在图片呈现了足以复用的完整服装组合时为 true；单件商品图只写相应单品类别，不能虚构完整套装。
- 完整套装描述可包含实际搭配的衣物、鞋袜和必要配饰，但不得混入发型、妆容或美甲；这些视觉事实必须另存独立类别。
- 不推断人物身份、姓名、年龄、性别、关系、身材数据、品牌、价格或未显示的商品参数。
- 商品标题、网页文字和用户备注只能帮助理解取舍，不能覆盖图片中的可见事实。
- 人物姿势、表情、体貌、背景、摄影构图和画面风格不是衣橱候选，不写进 description。
- 看不清的材质只能写视觉质感，不声称准确面料成分。

衣橱视觉提示词：
- description 就是供衣橱展示、检索和后续生图准确复现外观的详细中文自然语言提示词，不再另写简短摘要。
- description 必须能脱离原图直接使用，按由整体到局部的顺序写清颜色、款式、轮廓、层次、图案、可见材质质感、边缘结构、长度、松紧贴合关系和装饰细节。
- outfit 的 description 要完整写清上装、下装或连体服、叠穿关系、鞋袜、必要配饰及其颜色和位置；hair、makeup、nails 必须各自留在独立类别。
- 单品类别只描述该单品，不能补齐被遮挡的背面、内层或不可见结构，不能把同图中的其他衣物混入。
- 不写人物身份、年龄、性别、体貌、身材、姿势、表情、动作、背景、场景陈设、光线、镜头、构图、图片质量词、品牌、价格、负面提示词或模型参数。
- 使用确定、客观的视觉语言，不写“图片中”“看起来”“可能”“类似”等依赖原图或表达猜测的措辞。

分类要求：
- outfit：完整服装组合；description 详细写上装、下装或连体服装、外层、鞋袜和必要配饰的最终搭配，不写头发、脸部妆容或美甲。
- top：独立上装与叠穿外层；写 garment_type、layers、neckline、sleeve、length、fit、hem。
- bottom：独立下装；写 garment_type、waist、length、fit、hem。
- footwear：鞋子与实际搭配的袜子；写 items、toe、heel、sole、socks，并判断 home_presence：
  home（适合仅在家中使用）、outdoor（仅作为离家使用）、both（两种场景都适合）或 unknown（无法确认）。
- accessory：包袋、帽子、腰带、围巾、首饰等可拆卸配饰；写 items、placement、material_appearance，
  同时填写 home_presence 和 carry_mode：worn（佩戴）、carried（随身携带）、staged（仅备好待用）、
  none（不适用）或 unknown（无法确认）。这些是结构化枚举，不要把判断理由塞进 description。
- outfit：除 footwear/accessories 名称外，补充 component_roles 数组；每个鞋袜或配饰组成项写
  kind、name、home_presence、carry_mode。另写 home_description（仅包含适合当前居家的实际穿着）和
  outing_reserve_description（仅包含离家时才使用的组成）；没有对应组成时写空字符串。组件角色缺少
  可靠证据时使用 unknown，不要猜测。
- hair：只写发型；写 length、bangs、parting、tie、texture、volume、accessories。
- makeup：只写可见妆容；写 finish、base、brows、eyes、cheeks、lips。
- nails：只写可见美甲；写 shape、length、finish、colors、patterns、designs。
- 每一类的 description 都必须能脱离原图独立使用；不可见或信息不足时 present=false。
- category、colors、patterns、styles、seasons、scenes、weather_fit、activity_fit 等复数字段使用短词数组。

输出要求：
- 只返回严格 JSON 对象，不要 Markdown、代码块或解释。
- present 只有在图片中确实看得到且信息足够形成候选时才为 true；否则为 false。
{
  "outfit": {
    "present": false,
    "title": "简短候选名称",
    "description": "只复现这套服装、鞋袜和必要配饰的详细衣橱视觉提示词",
    "home_description": "排除外出专用组成后的详细居家穿着描述",
    "outing_reserve_description": "仅列出离家时才使用的鞋袜或随身配饰",
    "category": [], "colors": [], "pieces": [], "patterns": [],
    "silhouette": "", "neckline": "", "sleeve": "", "length": "",
    "material_appearance": "", "thickness": "", "exposure_level": "",
    "footwear": [], "accessories": [],
    "component_roles": [{"kind": "footwear | accessory", "name": "",
      "home_presence": "home | outdoor | both | unknown",
      "carry_mode": "worn | carried | staged | none | unknown"}],
    "styles": [], "seasons": [], "scenes": [], "weather_fit": [],
    "confidence": 0.0
  },
  "top": {
    "present": false, "title": "", "description": "只复现上装与可见叠穿结构的详细衣橱视觉提示词",
    "category": [], "garment_type": [], "layers": [], "colors": [],
    "patterns": [], "neckline": "", "sleeve": "", "length": "",
    "fit": "", "hem": "", "material_appearance": "", "thickness": "",
    "styles": [], "seasons": [], "scenes": [], "weather_fit": [],
    "confidence": 0.0
  },
  "bottom": {
    "present": false, "title": "", "description": "只复现下装结构的详细衣橱视觉提示词",
    "category": [], "garment_type": [], "colors": [], "patterns": [],
    "waist": "", "length": "", "fit": "", "hem": "",
    "material_appearance": "", "thickness": "", "styles": [],
    "seasons": [], "scenes": [], "weather_fit": [], "confidence": 0.0
  },
  "footwear": {
    "present": false, "title": "", "description": "只复现鞋子与实际可见袜子的详细衣橱视觉提示词",
    "category": [], "items": [], "colors": [], "patterns": [],
    "toe": "", "heel": "", "sole": "", "socks": [],
    "material_appearance": "", "styles": [], "seasons": [],
    "scenes": [], "weather_fit": [], "activity_fit": [],
    "home_presence": "home | outdoor | both | unknown", "confidence": 0.0
  },
  "accessory": {
    "present": false, "title": "", "description": "只复现配饰及其佩戴位置的详细衣橱视觉提示词",
    "category": [], "items": [], "colors": [], "patterns": [],
    "placement": [], "material_appearance": "", "styles": [],
    "seasons": [], "scenes": [], "weather_fit": [],
    "home_presence": "home | outdoor | both | unknown",
    "carry_mode": "worn | carried | staged | none | unknown", "confidence": 0.0
  },
  "hair": {
    "present": false,
    "title": "简短发型名称",
    "description": "只复现发型结构、发色和发饰的详细衣橱视觉提示词",
    "category": [], "colors": [], "length": "", "bangs": "",
    "parting": "", "tie": "", "texture": "", "volume": "",
    "accessories": [], "styles": [], "scenes": [], "weather_fit": [],
    "activity_fit": [], "confidence": 0.0
  },
  "makeup": {
    "present": false, "title": "", "description": "只复现可见妆容细节的详细衣橱视觉提示词",
    "category": [], "colors": [], "finish": "", "base": "",
    "brows": "", "eyes": "", "cheeks": "", "lips": "",
    "styles": [], "scenes": [], "weather_fit": [], "confidence": 0.0
  },
  "nails": {
    "present": false, "title": "", "description": "只复现可见美甲细节的详细衣橱视觉提示词",
    "category": [], "colors": [], "patterns": [], "shape": "",
    "length": "", "finish": "", "designs": [], "styles": [],
    "scenes": [], "confidence": 0.0
  }
}"""
        focus = (
            "按图片实际可见内容拆分并保存所有可靠类别。"
            if kind in {"auto", "both"}
            else f"只保存{STYLE_CATALOG_KIND_LABELS.get(kind, '指定')}候选；其他类别仍可分析，但不会入库。"
        )
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
            runtime_data_root(getattr(self, "data_path", None)) / STYLE_CATALOG_DIR_NAME
        )
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        target = target_dir / f"style_{digest[:24]}{suffix}"
        if not await asyncio.to_thread(target.exists):
            await asyncio.to_thread(target.write_bytes, data)
        logger.debug(f"{LOG_PREFIX} 视觉衣橱参考图已缓存：{target.name}（{mime}）")
        return str(target), digest

    @staticmethod
    def _style_perceptual_hash(path_text: str) -> str:
        try:
            with Image.open(path_text) as source:
                image = ImageOps.exif_transpose(source).convert("L")
                image = image.resize((9, 8), Image.Resampling.LANCZOS)
                flatten = getattr(image, "get_flattened_data", None)
                pixels = list(flatten() if callable(flatten) else image.getdata())
        except (FileNotFoundError, UnidentifiedImageError, OSError, ValueError):
            return ""
        bits = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                bits = (bits << 1) | int(
                    pixels[offset + column] > pixels[offset + column + 1]
                )
        return f"{bits:016x}"

    @staticmethod
    def _style_hash_distance(left: str, right: str) -> int:
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except (TypeError, ValueError):
            return 10_000

    async def _remove_unused_style_catalog_image(self, path_text: str) -> None:
        path = (
            runtime_data_root(getattr(self, "data_path", None))
            / STYLE_CATALOG_DIR_NAME
        )
        try:
            candidate = await asyncio.to_thread(Path(path_text).expanduser().resolve)
            candidate.relative_to(await asyncio.to_thread(path.resolve))
        except (OSError, RuntimeError, ValueError):
            return
        items = await self.archive.get_style_catalog_items(status="", limit=500)
        if any(str(item.image_path or "") == str(candidate) for item in items):
            return
        await asyncio.to_thread(candidate.unlink, missing_ok=True)

    async def _analyze_style_catalog_image(
        self, image: str, *, note: str, kind: str
    ) -> dict[str, Any]:
        configured_provider_id = self._style_text(
            getattr(getattr(self.config, "vision", None), "provider", ""),
            240,
        )
        sessions: list[str] = []
        last_error: Exception = RuntimeError("视觉模型不可用")
        try:
            prompt = self._style_catalog_contract(note, kind)
            index = 0
            async for provider in self.get_text_provider_candidates(
                configured_provider_id
            ):
                session_id = f"daily_life_style_catalog_{uuid.uuid4().hex[:8]}"
                if index:
                    session_id = f"{session_id}_fallback"
                    logger.info(
                        f"{LOG_PREFIX} 视觉衣橱指定模型识别失败，改用当前默认模型"
                    )
                sessions.append(session_id)
                try:
                    return await self._style_catalog_call_provider(
                        provider,
                        prompt,
                        image,
                        session_id,
                    )
                except Exception as exc:
                    last_error = exc
                index += 1
            raise last_error
        finally:
            cleanup = getattr(self, "close_text_session", None)
            if callable(cleanup):
                for active_session_id in sessions:
                    await cleanup(active_session_id)

    async def _style_catalog_call_provider(
        self,
        provider: Any,
        prompt: str,
        image: str,
        session_id: str,
    ) -> dict[str, Any]:
        result = await self._reverse_prompt_call_provider(
            provider,
            prompt,
            image,
            session_id,
        )
        if result is None:
            raise RuntimeError("视觉模型未返回结果")
        payload = extract_json_from_text(self._completion_text(result))
        if not isinstance(payload, dict):
            raise RuntimeError("视觉模型未返回有效结构")
        if not any(
            isinstance(payload.get(item_kind), dict)
            for item_kind in STYLE_CATALOG_KINDS
        ):
            raise RuntimeError("视觉模型未返回衣橱分类结构")
        return payload

    async def _learn_style_catalog_image(
        self,
        event: Any | None,
        image: str,
        *,
        source_url: str = "",
        source_kind: str = "user_image",
        source_scope: str = "",
        source_batch_id: str = "",
        source_query: str = "",
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
        perceptual_hash = await asyncio.to_thread(
            self._style_perceptual_hash, cached_image or image
        )
        normalized_scope = (
            self._style_text(source_scope, 240)
            or self._event_session_id(event)
            or "dashboard"
        )
        requested = self._requested_style_kinds(kind)
        saved: list[StyleCatalogItemRecord] = []
        for item_kind in STYLE_CATALOG_KINDS:
            if item_kind not in requested:
                continue
            analyzed = self._style_analysis_item(payload, item_kind)
            if not analyzed:
                continue
            attributes = dict(analyzed.get("attributes") or {})
            if perceptual_hash:
                attributes["perceptual_hash"] = perceptual_hash
            if source_batch_id:
                attributes["source_batch_id"] = self._style_text(source_batch_id, 120)
            if source_query:
                attributes["source_query"] = self._style_text(source_query, 500)
            if source_url:
                try:
                    source_host = (urlsplit(source_url).hostname or "").casefold()
                except ValueError:
                    source_host = ""
                if source_host:
                    attributes["source_host"] = source_host
            confidence = float(analyzed.get("confidence") or 0.0)
            incoming = {
                **analyzed,
                "attributes": attributes,
                "image_path": cached_image or image,
                "source_url": source_url,
                "source_scope": normalized_scope,
                "source_kind": source_kind,
                "source_image_hash": image_hash,
                "status": (
                    "active" if confidence >= _STYLE_REVIEW_CONFIDENCE else "pending"
                ),
            }
            similar = None
            if perceptual_hash:
                similar = await self.archive.find_similar_style_catalog_item(
                    item_kind,
                    perceptual_hash,
                    max_distance=_STYLE_PERCEPTUAL_DISTANCE,
                )
            if similar:
                record = await self.archive.merge_style_catalog_item(
                    similar.id, incoming
                )
            else:
                record = await self.archive.upsert_style_catalog_item(incoming)
            if record:
                saved.append(record)
        if saved and all(
            str(item.image_path or "") != str(cached_image or image) for item in saved
        ):
            await self._remove_unused_style_catalog_image(cached_image or image)
        return saved

    async def review_style_catalog_item(
        self, item_id: int, *, note: str = ""
    ) -> StyleCatalogItemRecord:
        item = await self.archive.get_style_catalog_item(item_id)
        if not item:
            raise ValueError("衣橱素材不存在")
        image_path = self._style_text(item.image_path, 1500)
        if not image_path or not await asyncio.to_thread(path_exists, image_path):
            raise ValueError("衣橱素材图片不存在")
        payload = await self._analyze_style_catalog_image(
            image_path,
            note=note,
            kind="auto",
        )
        analyzed = self._style_analysis_item(payload, item.kind)
        if not analyzed:
            raise ValueError("图片中没有足够清晰的造型信息")
        attributes = dict(analyzed.get("attributes") or {})
        existing_attributes = (
            item.attributes if isinstance(item.attributes, dict) else {}
        )
        for key in ("perceptual_hash", "used_count"):
            if existing_attributes.get(key) not in (None, ""):
                attributes[key] = existing_attributes[key]
        confidence = float(analyzed.get("confidence") or 0.0)
        updated = await self.archive.revise_style_catalog_item(
            item.id,
            title=str(analyzed.get("title") or ""),
            description=str(analyzed.get("description") or ""),
            attributes=attributes,
            confidence=confidence,
            status=("active" if confidence >= _STYLE_REVIEW_CONFIDENCE else "pending"),
        )
        if not updated:
            raise RuntimeError("衣橱素材更新失败")
        perceptual_hash = self._style_text(
            existing_attributes.get("perceptual_hash"), 32
        )
        for other_kind in STYLE_CATALOG_KINDS:
            if other_kind == item.kind:
                continue
            other = self._style_analysis_item(payload, other_kind)
            if not other:
                continue
            other_attributes = dict(other.get("attributes") or {})
            if perceptual_hash:
                other_attributes["perceptual_hash"] = perceptual_hash
            other_confidence = float(other.get("confidence") or 0.0)
            await self.archive.upsert_style_catalog_item(
                {
                    **other,
                    "attributes": other_attributes,
                    "image_path": item.image_path,
                    "source_url": item.source_url,
                    "source_scope": item.source_scope,
                    "source_kind": item.source_kind,
                    "source_image_hash": item.source_image_hash,
                    "status": (
                        "active"
                        if other_confidence >= _STYLE_REVIEW_CONFIDENCE
                        else "pending"
                    ),
                }
            )
        return updated

    @staticmethod
    def _style_catalog_result_text(
        items: list[StyleCatalogItemRecord],
        *,
        heading: str = "已加入视觉衣橱候选：",
    ) -> str:
        if not items:
            return "图片中没有足够清晰、可保存的衣橱信息。"
        lines = [heading]
        groups: dict[str, list[StyleCatalogItemRecord]] = {}
        for item in items:
            key = str(item.source_image_hash or item.image_path or item.id)
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            parts = [
                f"{STYLE_CATALOG_KIND_LABELS.get(item.kind, '造型')} #{item.id}："
                f"{item.title or item.description}"
                for item in group
            ]
            lines.append(f"- {'；'.join(parts)}")
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
                "缺少要浏览的穿搭、单品或造型需求。",
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
            target_count = max(1, min(int(count or 3), 12))
        except (TypeError, ValueError):
            target_count = 3
        existing_items = await self.archive.get_style_catalog_items(
            status="", limit=100
        )
        existing_ids = {int(item.id or 0) for item in existing_items}
        saved: list[StyleCatalogItemRecord] = []
        saved_keys: set[tuple[int, str]] = set()
        successful_images = 0
        failures: list[str] = []
        attempted = 0
        candidate_count = 0
        attempted_asset_keys: set[str] = set()
        saved_image_keys: set[str] = set()
        batch_id = f"web_{uuid.uuid4().hex[:12]}"
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
                if successful_images >= target_count:
                    break
                if not isinstance(asset, dict):
                    continue
                if not self._style_asset_is_candidate(asset):
                    continue
                image = self._style_text(asset.get("url"), 1500)
                asset_key = self._style_asset_key(image)
                if not asset_key or asset_key in attempted_asset_keys:
                    continue
                attempted_asset_keys.add(asset_key)
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
                        source_batch_id=batch_id,
                        source_query=query,
                    )
                    if not learned:
                        failures.append("图片中没有足够清晰的目标造型")
                        logger.debug(
                            f"{LOG_PREFIX} 视觉衣橱联网学习图片跳过："
                            f"序号={attempted}；原因={failures[-1]}"
                        )
                        continue
                    image_saved = False
                    image_key = ""
                    for item in learned:
                        image_key = image_key or str(item.source_image_hash or "")
                        key = (int(item.id or 0), str(item.kind or ""))
                        if key[0] in existing_ids:
                            failures.append("相同图片的造型已在视觉衣橱中")
                            continue
                        if key in saved_keys:
                            continue
                        saved_keys.add(key)
                        saved.append(item)
                        image_saved = True
                    if image_saved and image_key and image_key not in saved_image_keys:
                        saved_image_keys.add(image_key)
                        successful_images += 1
                except Exception as exc:
                    failures.append(self._media_error_summary(exc))
                    logger.debug(
                        f"{LOG_PREFIX} 视觉衣橱联网学习图片失败：序号={attempted}；"
                        f"原因={failures[-1][:240]}"
                    )
            if successful_images >= target_count:
                break
            if depth == "quick":
                logger.debug(
                    f"{LOG_PREFIX} 视觉衣橱快速搜索结果不足，继续深度搜索："
                    f"目标={target_count}；当前成功图片={successful_images}"
                )
        logger.debug(
            f"{LOG_PREFIX} 视觉衣橱联网学习保存完成：查询={query[:120]}；"
            f"目标={target_count}；候选={candidate_count}；尝试={attempted}；"
            f"成功图片={successful_images}；保存条目={len(saved)}；"
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
        if successful_images < target_count:
            result_text = (
                f"{result_text}\n"
                f"本次需要 {target_count} 组图片，实际保存 {successful_images} 组；"
                "其余搜索图片无法形成可靠候选。"
            )
        return ToolResultText(
            result_text,
            status="ok",
            media="style_catalog",
        )

    async def life_style_catalog_list(
        self, event: Any, *, kind: str = "", limit: int = 20
    ) -> str:
        del event
        normalized_kind = self._style_text(kind, 16).lower()
        if normalized_kind not in _STYLE_ITEM_KINDS:
            normalized_kind = ""
        try:
            safe_limit = max(1, min(int(limit or 20), 50))
        except (TypeError, ValueError):
            safe_limit = 20
        counts = await self.archive.get_style_catalog_counts(status="active")
        total_count = sum(counts.values())
        inventory_parts = [
            f"{STYLE_CATALOG_KIND_LABELS[item_kind]} {counts.get(item_kind, 0)}"
            for item_kind in STYLE_CATALOG_KINDS
        ]
        inventory_summary = (
            f"视觉衣橱库存：共 {total_count} 个已启用候选；"
            f"{'、'.join(inventory_parts)}。"
        )
        query_total = counts.get(normalized_kind, 0) if normalized_kind else total_count
        items = await self.archive.get_style_catalog_items(
            kind=normalized_kind, status="active", limit=safe_limit
        )
        if not items:
            if total_count > 0 and normalized_kind:
                label = STYLE_CATALOG_KIND_LABELS[normalized_kind]
                return f"{inventory_summary}\n当前没有已启用的{label}候选。"
            return (
                f"{inventory_summary}\n视觉衣橱还没有可用候选。"
                "如果用户本轮要求找或搜索网上新穿搭，必须继续调用 "
                "life_style_browse_learn；life_style_catalog 不会新增候选。"
            )
        shown_count = len(items)
        if normalized_kind:
            label = STYLE_CATALOG_KIND_LABELS[normalized_kind]
            display_summary = (
                f"当前查询：{label}共 {query_total} 个，已显示 {shown_count} 个。"
            )
            heading = f"{label}候选："
        else:
            display_summary = (
                f"当前展示：全部类别按偏好排序的前 {shown_count} 个条目，"
                f"总计 {query_total} 个；这不是各分类的完整清单。"
            )
            heading = "视觉衣橱候选："
        remaining_hint = ""
        if shown_count < query_total:
            remaining_hint = (
                f"\n还有 {query_total - shown_count} 个未展示；"
                "不得据此声称衣橱只有当前这些候选。"
            )
        return (
            f"{inventory_summary}\n{display_summary}\n"
            f"{self._style_catalog_result_text(items, heading=heading)}"
            f"{remaining_hint}\n"
            "用户追问套装、上装、下装、鞋袜、配饰、发型、妆容或美甲时，"
            "必须重新调用 life_style_catalog 并传入对应 kind，不能沿用混合查询的展示子集。\n"
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
            f"- #{item.id} [{item.kind}] {item.title}："
            f"{self._style_text((item.attributes or {}).get('visual_prompt') or item.description, 800)}"
            for item in items
        )
        fixed = """你负责把用户对视觉衣橱候选的自然语言反馈转换成结构化调整。
只依据用户反馈与给出的候选，不从词表、固定关键词或人物性别猜测偏好。
- prefer 表示更喜欢或更适合；dislike 表示不喜欢或不适合；neutral 表示只补充说明；disable 表示明确停用、不再自动采用。
- score_delta 范围 -1.0 到 1.0，单次反馈按明确程度调整，不要夸大。
- preference_points 只提炼长期可复用的审美、舒适度或场景偏好；只针对某一张图的意见不要上升为长期偏好。
- category 只能写 outfit、top、bottom、footwear、accessory、hair、makeup、nails 或 style。
只返回严格 JSON：
{
  "adjustments": [{"item_id": 1, "sentiment": "prefer | dislike | neutral | disable", "score_delta": 0.0, "reason": "简短理由"}],
  "preference_points": [{"category": "outfit | top | bottom | footwear | accessory | hair | makeup | nails | style", "content": "稳定偏好", "weight": 0.1}]
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
                "disabled"
                if sentiment in {"disable", "archive"}
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
            if category not in {*STYLE_CATALOG_KIND_SET, "style"} or not content:
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
