from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from typing import Any

from astrbot.api import logger

from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX


class StageFigureMixin:
    """从当前角色人设中提取图片工作流可复用的稳定体貌事实。"""

    @staticmethod
    def _character_appearance_prompt(persona: str) -> str:
        fixed = f"""你是角色稳定体貌资料提取器。只提取人设明确写出的、跨场景长期稳定的身体外观事实。
提取边界：
- 可以保留明确的成年身份或年龄、整体体型、身体比例、肩腰胯关系、上半身曲线、腿部线条和稳定体态。
- 不得根据性别、年龄、性格或审美推断人设没有写出的身体特征，也不得美化、强化或改写原意。
- 排除脸部五官、发型、妆容、服装、配饰、动作、姿势、场景、镜头、光线、临时状态、隐私回应策略和交流规则。
- appearance_profile 使用紧凑、客观的自然中文；没有明确体貌事实时 supported=false 且留空。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"supported":false,"appearance_profile":""}}"""
        return cache_friendly_prompt(fixed, persona, dynamic_title="当前角色人设")

    def _character_appearance_cache(self) -> dict[str, str]:
        cache = getattr(self, "_life_character_appearance_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._life_character_appearance_cache = cache
        return cache

    def _character_appearance_tasks(self) -> dict[str, asyncio.Task[str]]:
        tasks = getattr(self, "_life_character_appearance_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._life_character_appearance_tasks = tasks
        return tasks

    @staticmethod
    def _character_appearance_fingerprint(persona: str) -> str:
        normalized = unicodedata.normalize(
            "NFKC", " ".join(str(persona or "").split())
        )
        cjk_or_punctuation = r"\u3400-\u9fff，。！？；：、"
        normalized = re.sub(
            rf"(?<=[{cjk_or_punctuation}])\s+|\s+(?=[{cjk_or_punctuation}])",
            "",
            normalized,
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _remember_character_appearance_profile(
        self, persona: str, appearance_profile: str
    ) -> str:
        persona = str(persona or "").strip()
        if not persona:
            return ""
        profile = " ".join(str(appearance_profile or "").strip().split())[:600]
        cache = self._character_appearance_cache()
        cache[self._character_appearance_fingerprint(persona)] = profile
        while len(cache) > 16:
            cache.pop(next(iter(cache)))
        return profile

    async def _character_appearance_context(
        self,
        event: Any = None,
        *,
        schedule_extract: bool = True,
        wait_for_extract: bool = False,
    ) -> tuple[str, str]:
        persona_getter = getattr(self, "get_persona_text", None)
        if not callable(persona_getter):
            return "", ""
        scope_getter = getattr(self, "_event_session_id", None)
        scope = (
            scope_getter(event) if event is not None and callable(scope_getter) else ""
        )
        try:
            persona = str(await persona_getter(scope) or "").strip()
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 角色稳定体貌读取失败：{exc}")
            return "", ""
        if not persona:
            return "", ""

        fingerprint = self._character_appearance_fingerprint(persona)
        cache = self._character_appearance_cache()
        if fingerprint in cache:
            return persona, cache[fingerprint]
        if not schedule_extract:
            return persona, ""

        tasks = self._character_appearance_tasks()
        task = tasks.get(fingerprint)
        if task is None:
            task = asyncio.create_task(self._extract_character_appearance_profile(persona))
            tasks[fingerprint] = task

            def store_result(completed: asyncio.Task[str]) -> None:
                tasks.pop(fingerprint, None)
                try:
                    profile = completed.result()
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.debug(f"{LOG_PREFIX} 角色稳定体貌提取失败：{exc}")
                    return
                self._remember_character_appearance_profile(persona, profile)

            task.add_done_callback(store_result)
        if wait_for_extract:
            try:
                return persona, await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 完成回调统一记录异常，并保留下一次重新提取的机会。
                pass
        return persona, ""

    async def _extract_character_appearance_profile(self, persona: str) -> str:
        caller = getattr(self, "_media_director_call", None)
        if not callable(caller):
            return ""
        payload = await caller(self._character_appearance_prompt(persona))
        if not isinstance(payload, dict) or not isinstance(payload.get("supported"), bool):
            raise ValueError("角色稳定体貌提取结果缺少有效 supported 字段")
        if payload["supported"] is not True:
            return ""
        return " ".join(str(payload.get("appearance_profile") or "").strip().split())[
            :600
        ]

    async def _character_appearance_profile(self, event: Any = None) -> str:
        _persona, profile = await self._character_appearance_context(
            event, wait_for_extract=True
        )
        return profile

    async def _close_character_appearance_tasks(self) -> None:
        tasks = list(self._character_appearance_tasks().values())
        self._character_appearance_tasks().clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["StageFigureMixin"]
