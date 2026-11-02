from __future__ import annotations

from ..runtime import PLUGIN_ID

from . import view as _part_page_view_component
from .portal import entry as _part_portal_base_component
from .portal import asset as _part_portal_reference_component
from .portal import closet as _part_portal_closet_component
from .portal import stickers as _part_portal_emoji_component
from .portal import line as _part_portal_line_component
from typing import Any, Protocol


class DailyLifeDashboardComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _external_plugin_lease: Any
    _external_runtime_lease: Any
    _page_request_method: Any
    context: Any
    runtime: Any
    _page_json: Any
    _page_json_body: Any
    _page_query_args: Any
    page_browsing_list: Any
    page_browsing_control: Any
    page_character_reference_delete: Any
    page_character_reference_preview: Any
    page_character_reference_upload: Any
    page_closet_backup: Any
    page_closet_delete: Any
    page_closet_feedback: Any
    page_closet_generate: Any
    page_closet_import: Any
    page_closet_list: Any
    page_closet_preview: Any
    page_closet_restore: Any
    page_closet_review: Any
    page_closet_status: Any
    page_config: Any
    page_emoji_backup: Any
    page_emoji_delete: Any
    page_emoji_import: Any
    page_emoji_list: Any
    page_emoji_preview: Any
    page_emoji_restore: Any
    page_emoji_sendable: Any
    page_experience_boundary: Any
    page_experience_episode_correct: Any
    page_experience_episode_protect: Any
    page_experience_feedback: Any
    page_experience_focus: Any
    page_friend_reference_delete: Any
    page_friend_reference_preview: Any
    page_friend_reference_upload: Any
    page_generate_week: Any
    page_refresh_state: Any
    page_reset_day: Any
    page_status: Any
    page_status_wait: Any
    page_timeline_save: Any


class DailyLifeDashboardComponent:
    """日常生活插件的轻量页面面板。"""

    def __init__(self, context: DailyLifeDashboardComponentContext) -> None:
        self.context = context

    async def page_browsing_list(self):
        async def read():
            query = self.context._page_query_args()
            status = str(query.get("status") or "")
            from ..browsing.bookmarks import STATES

            if status and status not in STATES:
                raise ValueError("无效的浏览筛选条件")
            offset = max(0, min(10000, int(query.get("offset") or 0)))
            service = self.context.runtime.bili_browsing
            records = await service.archive.list(limit=41, offset=offset, status=status)
            state = await service.archive.state()
            enabled = self.context.runtime.config.browsing.enabled
            error = state.get("last_error", "")
            reason = state.get("last_reason", "")
            if enabled:
                from ..browsing.feed import BiliReadError

                try:
                    await service.reader.require_login()
                    if state.get("login_required"):
                        error = ""
                except BiliReadError as exc:
                    error, reason = str(exc), ""
            return {"items": [service.card(item) for item in records[:40]], "has_more": len(records) > 40,
                    "enabled": enabled, "reason": reason,
                    "error": error, "interests": state.get("taste", {}),
                    "activities": await service.social.context(None),
                    "creators": state.get("creators", {})}
        return await self.context._page_json(read)

    async def page_browsing_control(self):
        async def act():
            body = await self.context._page_json_body()
            record = await self.context.runtime.bili_browsing.control(str(body.get("id") or ""), str(body.get("operation") or ""))
            return {"item": record}
        return await self.context._page_json(act)

    def _register_page_web_apis(self) -> None:
        dashboard_routes = (
            ("page/browsing/list", self.context.page_browsing_list, ["GET"], "B站浏览记录"),
            ("page/browsing/control", self.context.page_browsing_control, ["POST"], "暂停或继续浏览视频"),
            ("page/status", self.context.page_status, ["GET"], "日常生活工作台状态"),
            (
                "page/status/wait",
                self.context.page_status_wait,
                ["GET"],
                "等待日常生活工作台状态更新",
            ),
            (
                "page/action/refresh-state",
                self.context.page_refresh_state,
                ["POST"],
                "刷新日常生活状态",
            ),
            ("page/action/reset-day", self.context.page_reset_day, ["POST"], "重生成日常生活"),
            ("page/memory/center", self.context.page_memory_center, ["GET"], "记忆中心"),
            ("page/memory/manage", self.context.page_memory_manage, ["POST"], "整理记忆与资料"),
            ("page/timeline/save", self.context.page_timeline_save, ["POST"], "保存时间轴"),
            ("page/config", self.context.page_config, ["GET", "POST"], "日常生活设置"),
            (
                "page/config/character-reference",
                self.context.page_character_reference_upload,
                ["POST"],
                "上传角色形象参考图",
            ),
            (
                "page/config/character-reference/preview",
                self.context.page_character_reference_preview,
                ["POST"],
                "预览角色形象参考图",
            ),
            (
                "page/config/character-reference/delete",
                self.context.page_character_reference_delete,
                ["POST"],
                "删除角色形象参考图",
            ),
            # AstrBot 按注册顺序匹配插件接口，具体操作必须排在贪婪路径之前。
            (
                "page/config/friend-reference/preview/<path:profile_id>",
                self.context.page_friend_reference_preview,
                ["POST"],
                "预览好友参考图",
            ),
            (
                "page/config/friend-reference/delete/<path:profile_id>",
                self.context.page_friend_reference_delete,
                ["POST"],
                "删除好友参考图",
            ),
            (
                "page/config/friend-reference/<path:profile_id>",
                self.context.page_friend_reference_upload,
                ["POST"],
                "上传好友参考图",
            ),
            ("page/emoji/list", self.context.page_emoji_list, ["GET"], "表情素材列表"),
            ("page/emoji/import", self.context.page_emoji_import, ["POST"], "导入表情素材"),
            ("page/emoji/preview", self.context.page_emoji_preview, ["POST"], "预览表情素材"),
            ("page/emoji/delete", self.context.page_emoji_delete, ["POST"], "删除表情素材"),
            (
                "page/emoji/sendable",
                self.context.page_emoji_sendable,
                ["POST"],
                "切换表情可发送状态",
            ),
            ("page/emoji/backup", self.context.page_emoji_backup, ["GET"], "备份表情素材"),
            ("page/emoji/restore", self.context.page_emoji_restore, ["POST"], "还原表情素材"),
            ("page/closet/list", self.context.page_closet_list, ["GET"], "视觉衣橱列表"),
            ("page/closet/import", self.context.page_closet_import, ["POST"], "导入衣橱图片"),
            ("page/closet/generate", self.context.page_closet_generate, ["POST"], "创意生成衣橱"),
            ("page/closet/preview", self.context.page_closet_preview, ["POST"], "预览衣橱图片"),
            ("page/closet/status", self.context.page_closet_status, ["POST"], "更新衣橱状态"),
            ("page/closet/feedback", self.context.page_closet_feedback, ["POST"], "保存衣橱反馈"),
            ("page/closet/review", self.context.page_closet_review, ["POST"], "重新识别衣橱素材"),
            ("page/closet/delete", self.context.page_closet_delete, ["POST"], "删除衣橱素材"),
            ("page/closet/backup", self.context.page_closet_backup, ["GET"], "备份视觉衣橱"),
            ("page/closet/restore", self.context.page_closet_restore, ["POST"], "还原视觉衣橱"),
        )
        # 这些接口供自动流程、命令或外部管理调用，不在 Dashboard 中重复放置入口。
        programmatic_routes = (
            (
                "page/action/generate-week",
                self.context.page_generate_week,
                ["POST"],
                "生成周计划",
            ),
            (
                "page/experience/episode/correct",
                self.context.page_experience_episode_correct,
                ["POST"],
                "纠正生活片段",
            ),
            (
                "page/experience/episode/protect",
                self.context.page_experience_episode_protect,
                ["POST"],
                "保护生活片段",
            ),
            (
                "page/experience/focus",
                self.context.page_experience_focus,
                ["POST"],
                "旧关注接口已退役（只读历史见诊断状态）",
            ),
            (
                "page/experience/boundary",
                self.context.page_experience_boundary,
                ["POST"],
                "保存记忆边界",
            ),
            (
                "page/experience/feedback",
                self.context.page_experience_feedback,
                ["POST"],
                "保存行为反馈",
            ),
        )
        for endpoint, handler, methods, desc in (
            dashboard_routes + programmatic_routes
        ):
            runtime_guard = getattr(self.context, "_external_runtime_lease", None)
            plugin_guard = getattr(self.context, "_external_plugin_lease", None)
            if callable(runtime_guard):
                original = handler

                async def guarded(
                    *args,
                    _original=original,
                    _endpoint=endpoint,
                    _runtime_guard=runtime_guard,
                    _plugin_guard=plugin_guard,
                    **kwargs,
                ):
                    guard = _runtime_guard
                    if (
                        _endpoint == "page/config"
                        and self.context._page_request_method() == "POST"
                        and callable(_plugin_guard)
                    ):
                        guard = _plugin_guard
                    async with guard():
                        return await _original(*args, **kwargs)

                handler = guarded
            self.context.context.register_web_api(
                f"/{PLUGIN_ID}/{endpoint}", handler, methods, desc
            )

    @staticmethod
    def _page_records_for_date(records: list, target_date: str, *date_fields: str) -> list:
        return _part_page_view_component.PageViewComponent._page_records_for_date(records, target_date, *date_fields)

    @staticmethod
    def _load_page_config_schema() -> dict:
        return _part_page_view_component.PageViewComponent._load_page_config_schema()

    @classmethod
    async def _page_config_schema(cls) -> dict:
        return await _part_page_view_component.PageViewComponent._page_config_schema.__func__(cls, )

    @staticmethod
    def _page_compact(value: object, limit: int=120) -> str:
        return _part_page_view_component.PageViewComponent._page_compact(value, limit)

    @staticmethod
    def _page_internal_reference_segments(value: object) -> list[str]:
        return _part_page_view_component.PageViewComponent._page_internal_reference_segments(value)

    @staticmethod
    def _page_is_evidence_reference(value: object) -> bool:
        return _part_page_view_component.PageViewComponent._page_is_evidence_reference(value)

    @classmethod
    def _page_readable_evidence(cls, value: object) -> str:
        return _part_page_view_component.PageViewComponent._page_readable_evidence.__func__(cls, value)

    @classmethod
    def _page_readable_evidence_record(cls, record: dict) -> dict:
        return _part_page_view_component.PageViewComponent._page_readable_evidence_record.__func__(cls, record)

    @staticmethod
    def _page_strip_same_day_prefix(value: object, target_date: str) -> str:
        return _part_page_view_component.PageViewComponent._page_strip_same_day_prefix(value, target_date)

    @classmethod
    def _page_decision_display(cls, decision: dict, target_date: str) -> dict:
        return _part_page_view_component.PageViewComponent._page_decision_display.__func__(cls, decision, target_date)

    @staticmethod
    def _page_current_decision(target_date: str, life_decisions: list) -> dict:
        return _part_page_view_component.PageViewComponent._page_current_decision(target_date, life_decisions)

    @staticmethod
    def _page_memo_lines(memo: str) -> list[str]:
        return _part_page_view_component.PageViewComponent._page_memo_lines(memo)

    @classmethod
    def _page_memo_items(cls, date_str: str, scope: str, memo: str) -> list[dict]:
        return _part_page_view_component.PageViewComponent._page_memo_items.__func__(cls, date_str, scope, memo)

    @staticmethod
    def _page_memo_payload(items: list[dict]) -> dict:
        return _part_page_view_component.PageViewComponent._page_memo_payload(items)

    @staticmethod
    def _page_decision_sources(decision: dict, evidence: list[dict]) -> list[str]:
        return _part_page_view_component.PageViewComponent._page_decision_sources(decision, evidence)

    @staticmethod
    def _page_unique_records(records: list, keys: tuple[str, ...]) -> list:
        return _part_page_view_component.PageViewComponent._page_unique_records(records, keys)

    @staticmethod
    def _page_feedback_records(records: list) -> list:
        return _part_page_view_component.PageViewComponent._page_feedback_records(records)

    @staticmethod
    def _page_action_decisions(decisions: list, limit: int=_part_page_view_component.PAGE_WORLD_RECORD_LIMIT) -> list:
        return _part_page_view_component.PageViewComponent._page_action_decisions(decisions, limit)

    @staticmethod
    def _is_generic_page_name(name: str) -> bool:
        return _part_page_view_component.PageViewComponent._is_generic_page_name(name)

    _PAGE_UPLOAD_CHUNK_SIZE = _part_portal_base_component.PortalBaseComponent._PAGE_UPLOAD_CHUNK_SIZE

    @staticmethod
    def _page_public_error(exc: Exception) -> str:
        return _part_portal_base_component.PortalBaseComponent._page_public_error(exc)

    @staticmethod
    def _page_bool(value) -> bool:
        return _part_portal_base_component.PortalBaseComponent._page_bool(value)

    @staticmethod
    def _page_friend_reference_key(profile_id: str) -> str:
        return _part_portal_reference_component.PortalReferenceComponent._page_friend_reference_key(profile_id)

    @staticmethod
    def _page_reference_mime(path: _part_portal_reference_component.Path) -> str:
        return _part_portal_reference_component.PortalReferenceComponent._page_reference_mime(path)

    @staticmethod
    def _page_closet_kind(value: object) -> str:
        return _part_portal_closet_component.PortalClosetComponent._page_closet_kind(value)

    @staticmethod
    def _page_closet_text(value: object, limit: int=0) -> str:
        return _part_portal_closet_component.PortalClosetComponent._page_closet_text(value, limit)

    @staticmethod
    def _page_closet_ids(body: dict) -> list[int]:
        return _part_portal_closet_component.PortalClosetComponent._page_closet_ids(body)

    @staticmethod
    def _page_closet_mime(path: _part_portal_closet_component.Path) -> str:
        return _part_portal_closet_component.PortalClosetComponent._page_closet_mime(path)

    @staticmethod
    def _page_closet_event():
        return _part_portal_closet_component.PortalClosetComponent._page_closet_event()

    @staticmethod
    def _page_emoji_ids(body: dict) -> list[int]:
        return _part_portal_emoji_component.PortalEmojiComponent._page_emoji_ids(body)

    @staticmethod
    def _page_emoji_is_animated(path_text: str, cached_path: _part_portal_emoji_component.Path | None) -> bool:
        return _part_portal_emoji_component.PortalEmojiComponent._page_emoji_is_animated(path_text, cached_path)

    @staticmethod
    def _page_merge_restored_emoji_asset(existing: _part_portal_emoji_component.EmojiAssetRecord | None, restored: _part_portal_emoji_component.EmojiAssetRecord) -> _part_portal_emoji_component.EmojiAssetRecord:
        return _part_portal_emoji_component.PortalEmojiComponent._page_merge_restored_emoji_asset(existing, restored)

    @staticmethod
    def _page_imported_emoji_record(existing: _part_portal_emoji_component.EmojiAssetRecord | None, *, fingerprint: str, file_path: str, source_url: str, display_name: str) -> _part_portal_emoji_component.EmojiAssetRecord:
        return _part_portal_emoji_component.PortalEmojiComponent._page_imported_emoji_record(existing, fingerprint=fingerprint, file_path=file_path, source_url=source_url, display_name=display_name)

    @staticmethod
    def _page_image_mime(path: _part_portal_emoji_component.Path) -> str:
        return _part_portal_emoji_component.PortalEmojiComponent._page_image_mime(path)

    @staticmethod
    def _page_static_emoji_preview_path(path: _part_portal_emoji_component.Path) -> _part_portal_emoji_component.Path:
        return _part_portal_emoji_component.PortalEmojiComponent._page_static_emoji_preview_path(path)

    @classmethod
    def _page_emoji_placeholder_data_url(cls, label: str='') -> str:
        return _part_portal_emoji_component.PortalEmojiComponent._page_emoji_placeholder_data_url.__func__(cls, label)

    @staticmethod
    def _page_emoji_placeholder_bytes(label: str='') -> bytes:
        return _part_portal_emoji_component.PortalEmojiComponent._page_emoji_placeholder_bytes(label)

    @staticmethod
    def _page_validate_timeline(raw_timeline) -> list[_part_portal_line_component.TimelineItem]:
        return _part_portal_line_component.PortalLineComponent._page_validate_timeline(raw_timeline)

