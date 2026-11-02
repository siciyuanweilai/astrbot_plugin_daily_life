from __future__ import annotations

from ...clock import now as life_now
from ...models import BehaviorFeedbackRecord, MemoryBoundaryRecord

from typing import Any, Protocol


class PortalMemoryComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _build_page_status: Any
    _page_bool: Any
    _page_json: Any
    _page_json_body: Any
    _page_response: Any
    runtime: Any


class PortalMemoryComponent:
    def __init__(self, context: PortalMemoryComponentContext) -> None:
        self.context = context

    async def page_experience_episode_correct(self):
        async def handler():
            body = await self.context._page_json_body()
            episode_id = int(body.get("episode_id") or 0)
            correction = str(body.get("correction") or "").strip()
            if episode_id <= 0 or not correction:
                raise ValueError("生活片段标识和纠正内容不能为空")
            protected = self.context._page_bool(body.get("protected", True))
            if not await self.context.runtime.archive.correct_life_episode(
                episode_id, correction, protected=protected
            ):
                raise ValueError(f"未找到生活片段：{episode_id}")
            return {"status": await self.context._build_page_status()}

        return await self.context._page_json(handler)

    async def page_experience_episode_protect(self):
        async def handler():
            body = await self.context._page_json_body()
            episode_id = int(body.get("episode_id") or 0)
            if episode_id <= 0:
                raise ValueError("生活片段标识不能为空")
            protected = self.context._page_bool(body.get("protected", True))
            if not await self.context.runtime.archive.set_life_episode_protected(
                episode_id, protected
            ):
                raise ValueError(f"未找到生活片段：{episode_id}")
            return {"status": await self.context._build_page_status()}

        return await self.context._page_json(handler)

    async def page_experience_focus(self):
        """明确拒绝已退役写入，避免将未接入决策的记录报告为生效。"""
        return await self.context._page_response(
            {
                "ok": False,
                "error": {
                    "code": "feature_retired",
                    "message": "旧关注目标接口已退役；持续安排请使用现有承诺、共同活动和回访流程。历史记录可通过诊断状态只读查询。",
                    "public": True,
                },
            },
            410,
        )

    async def page_experience_boundary(self):
        async def handler():
            body = await self.context._page_json_body()
            boundary = MemoryBoundaryRecord.from_value(body.get("boundary") or body)
            if not boundary:
                raise ValueError("记忆边界不能为空")
            saved = await self.context.runtime.archive.set_memory_boundary(boundary)
            return {
                "boundary": saved.as_dict() if saved else None,
                "status": await self.context._build_page_status(),
            }

        return await self.context._page_json(handler)

    async def page_experience_feedback(self):
        async def handler():
            body = await self.context._page_json_body()
            now = life_now()
            feedback = BehaviorFeedbackRecord.from_value(
                {
                    **(
                        body.get("feedback")
                        if isinstance(body.get("feedback"), dict)
                        else body
                    ),
                    "date": str(body.get("date") or now.strftime("%Y-%m-%d")),
                    "source": str(body.get("source") or "dashboard"),
                }
            )
            if not feedback:
                raise ValueError("行为反馈不能为空")
            saved = await self.context.runtime.archive.add_behavior_feedback(feedback)
            return {
                "feedback": saved.as_dict() if saved else None,
                "status": await self.context._build_page_status(),
            }

        return await self.context._page_json(handler)

    async def page_memory_center(self):
        async def handler():
            args = self.context._page_query_args()
            archive = self.context.runtime.archive
            service = archive.memory_life
            scopes = await service.scopes()
            scope = str(args.get("scope") or "")
            visible = [scope] if scope else scopes
            section = str(args.get("section") or "timeline")
            offset = max(0, int(args.get("offset") or 0))
            if section == "timeline":
                data = await service.timeline(
                    visible, offset=offset, limit=int(args.get("limit") or 12)
                )
            elif section == "memories":
                data = await archive._run_db(
                    lambda: list(reversed(service._memories(visible)))
                )
            elif section == "graph":
                data = await service.graph(
                    visible, entity_id=int(args.get("entity_id") or 0)
                )
            elif section == "diary":
                data = await service.diaries(visible, 0)
            elif section == "library":
                data = {
                    "sources": await service.library(visible),
                    "notes": await service.reading_notes(visible, 100),
                }
            elif section == "originals":
                data = await service.originals(
                    visible,
                    start=str(args.get("start") or ""),
                    end=str(args.get("end") or ""),
                    offset=offset,
                    limit=12,
                )
            elif section == "evidence":
                mid = int(args.get("memory_id") or 0)
                data = await archive.read_memory_evidence(
                    mid, scopes=visible, offset=offset
                )
                memory = await archive._run_db(
                    lambda: next(
                        (r for r in service._memories(visible) if r["id"] == mid), None
                    )
                )
                data["memory"] = memory
                data["validity"] = (
                    await archive.get_memory_recall_context([mid], scopes=visible)
                ).get(mid, "")
            elif section == "passage":
                data = await service.passage(
                    int(args.get("source_id") or 0),
                    visible,
                    passage_id=int(args.get("passage_id") or 0),
                    include_inactive=True,
                )
            elif section == "chapters":
                data = await service.chapters(visible, 0)
            else:
                raise ValueError("未知记忆视图")
            more = None
            if isinstance(data, list):
                more = offset + 12 if len(data) > offset + 12 else None
                data = data[offset : offset + 12]
            return {"scopes": scopes, "data": data, "next_offset": more}

        return await self.context._page_json(handler)

    async def page_memory_manage(self):
        async def handler():
            body = await self.context._page_json_body()
            archive = self.context.runtime.archive
            service = archive.memory_life
            operation = str(body.get("operation") or "")
            if operation == "import":
                result = await service.import_source(
                    scope=str(body.get("scope") or "global"),
                    title=str(body.get("title") or ""),
                    content=str(body.get("content") or ""),
                    origin=str(body.get("origin") or ""),
                )
            elif operation == "correct":
                mid = int(body.get("memory_id") or 0)
                scope = str(body.get("scope") or "")
                result = await archive.correct_life_memory(
                    scope,
                    mid,
                    str(body.get("content") or ""),
                    evidence_id=f"dashboard:{life_now().isoformat()}",
                    evidence=str(body.get("reason") or ""),
                    expected_content=str(body["expected_content"])
                    if "expected_content" in body
                    else None,
                )
            elif operation in {"merge", "split"}:
                result = {
                    "entity_id": await service.manage_identity(
                        operation=operation,
                        entity_id=int(body.get("entity_id") or 0),
                        target_id=int(body.get("target_id") or 0),
                        name=str(body.get("name") or ""),
                        memory_ids=body.get("memory_ids") or [],
                    )
                }
            elif operation == "source_status":
                result = {
                    "saved": await service.set_source_status(
                        int(body.get("source_id") or 0), str(body.get("status") or "")
                    )
                }
            else:
                raise ValueError("未知记忆管理操作")
            await self.context.runtime.mark_page_status_changed("memory_center")
            return {"result": result}

        return await self.context._page_json(handler)
