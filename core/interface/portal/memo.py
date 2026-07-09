from __future__ import annotations

from ...clock import now as life_now
from ...models import BehaviorFeedbackRecord, FocusTargetRecord, MemoryBoundaryRecord


class PortalMemoryMixin:
    async def page_experience_episode_correct(self):
        async def handler():
            body = await self._page_json_body()
            episode_id = int(body.get("episode_id") or 0)
            correction = str(body.get("correction") or "").strip()
            if episode_id <= 0 or not correction:
                raise ValueError("生活片段标识和纠正内容不能为空")
            protected = self._page_bool(body.get("protected", True))
            if not await self.runtime.archive.correct_life_episode(episode_id, correction, protected=protected):
                raise ValueError(f"未找到生活片段：{episode_id}")
            return {"status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_experience_episode_protect(self):
        async def handler():
            body = await self._page_json_body()
            episode_id = int(body.get("episode_id") or 0)
            if episode_id <= 0:
                raise ValueError("生活片段标识不能为空")
            protected = self._page_bool(body.get("protected", True))
            if not await self.runtime.archive.set_life_episode_protected(episode_id, protected):
                raise ValueError(f"未找到生活片段：{episode_id}")
            return {"status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_experience_focus(self):
        async def handler():
            body = await self._page_json_body()
            target = FocusTargetRecord.from_value(body.get("focus") or body)
            if not target:
                raise ValueError("关注目标不能为空")
            saved = await self.runtime.archive.upsert_focus_target(target)
            return {"focus": saved.as_dict() if saved else None, "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_experience_boundary(self):
        async def handler():
            body = await self._page_json_body()
            boundary = MemoryBoundaryRecord.from_value(body.get("boundary") or body)
            if not boundary:
                raise ValueError("记忆边界不能为空")
            saved = await self.runtime.archive.set_memory_boundary(boundary)
            return {"boundary": saved.as_dict() if saved else None, "status": await self._build_page_status()}

        return await self._page_json(handler)

    async def page_experience_feedback(self):
        async def handler():
            body = await self._page_json_body()
            now = life_now()
            feedback = BehaviorFeedbackRecord.from_value(
                {
                    **(body.get("feedback") if isinstance(body.get("feedback"), dict) else body),
                    "date": str(body.get("date") or now.strftime("%Y-%m-%d")),
                    "source": str(body.get("source") or "dashboard"),
                }
            )
            if not feedback:
                raise ValueError("行为反馈不能为空")
            saved = await self.runtime.archive.add_behavior_feedback(feedback)
            return {"feedback": saved.as_dict() if saved else None, "status": await self._build_page_status()}

        return await self._page_json(handler)
