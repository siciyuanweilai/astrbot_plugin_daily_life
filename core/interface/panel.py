from ..runtime import PLUGIN_ID
from .portal import (
    PortalActionMixin,
    PortalBaseMixin,
    PortalClosetMixin,
    PortalEmojiMixin,
    PortalLineMixin,
    PortalMemoryMixin,
    PortalReferenceMixin,
)
from .view import PageViewMixin


class DailyLifeDashboardMixin(
    PageViewMixin,
    PortalBaseMixin,
    PortalReferenceMixin,
    PortalClosetMixin,
    PortalEmojiMixin,
    PortalActionMixin,
    PortalLineMixin,
    PortalMemoryMixin,
):
    """日常生活插件的轻量页面面板。"""

    def _register_page_web_apis(self) -> None:
        dashboard_routes = (
            ("page/status", self.page_status, ["GET"], "日常生活工作台状态"),
            (
                "page/status/wait",
                self.page_status_wait,
                ["GET"],
                "等待日常生活工作台状态更新",
            ),
            (
                "page/action/refresh-state",
                self.page_refresh_state,
                ["POST"],
                "刷新日常生活状态",
            ),
            ("page/action/reset-day", self.page_reset_day, ["POST"], "重生成日常生活"),
            ("page/timeline/save", self.page_timeline_save, ["POST"], "保存时间轴"),
            ("page/config", self.page_config, ["GET", "POST"], "日常生活设置"),
            (
                "page/config/character-reference",
                self.page_character_reference_upload,
                ["POST"],
                "上传角色形象参考图",
            ),
            (
                "page/config/character-reference/preview",
                self.page_character_reference_preview,
                ["POST"],
                "预览角色形象参考图",
            ),
            (
                "page/config/character-reference/delete",
                self.page_character_reference_delete,
                ["POST"],
                "删除角色形象参考图",
            ),
            # AstrBot 按注册顺序匹配插件接口，具体操作必须排在贪婪路径之前。
            (
                "page/config/friend-reference/preview/<path:profile_id>",
                self.page_friend_reference_preview,
                ["POST"],
                "预览好友参考图",
            ),
            (
                "page/config/friend-reference/delete/<path:profile_id>",
                self.page_friend_reference_delete,
                ["POST"],
                "删除好友参考图",
            ),
            (
                "page/config/friend-reference/<path:profile_id>",
                self.page_friend_reference_upload,
                ["POST"],
                "上传好友参考图",
            ),
            ("page/emoji/list", self.page_emoji_list, ["GET"], "表情素材列表"),
            ("page/emoji/import", self.page_emoji_import, ["POST"], "导入表情素材"),
            ("page/emoji/preview", self.page_emoji_preview, ["POST"], "预览表情素材"),
            ("page/emoji/delete", self.page_emoji_delete, ["POST"], "删除表情素材"),
            (
                "page/emoji/sendable",
                self.page_emoji_sendable,
                ["POST"],
                "切换表情可发送状态",
            ),
            ("page/emoji/backup", self.page_emoji_backup, ["GET"], "备份表情素材"),
            ("page/emoji/restore", self.page_emoji_restore, ["POST"], "还原表情素材"),
            ("page/closet/list", self.page_closet_list, ["GET"], "视觉衣橱列表"),
            ("page/closet/import", self.page_closet_import, ["POST"], "导入衣橱图片"),
            ("page/closet/browse", self.page_closet_browse, ["POST"], "联网学习衣橱"),
            ("page/closet/preview", self.page_closet_preview, ["POST"], "预览衣橱图片"),
            ("page/closet/status", self.page_closet_status, ["POST"], "更新衣橱状态"),
            ("page/closet/feedback", self.page_closet_feedback, ["POST"], "保存衣橱反馈"),
            ("page/closet/review", self.page_closet_review, ["POST"], "重新识别衣橱素材"),
            ("page/closet/delete", self.page_closet_delete, ["POST"], "删除衣橱素材"),
            ("page/closet/backup", self.page_closet_backup, ["GET"], "备份视觉衣橱"),
            ("page/closet/restore", self.page_closet_restore, ["POST"], "还原视觉衣橱"),
        )
        # 这些接口供自动流程、命令或外部管理调用，不在 Dashboard 中重复放置入口。
        programmatic_routes = (
            (
                "page/action/generate-week",
                self.page_generate_week,
                ["POST"],
                "生成周计划",
            ),
            (
                "page/experience/episode/correct",
                self.page_experience_episode_correct,
                ["POST"],
                "纠正生活片段",
            ),
            (
                "page/experience/episode/protect",
                self.page_experience_episode_protect,
                ["POST"],
                "保护生活片段",
            ),
            (
                "page/experience/focus",
                self.page_experience_focus,
                ["POST"],
                "保存关注目标",
            ),
            (
                "page/experience/boundary",
                self.page_experience_boundary,
                ["POST"],
                "保存记忆边界",
            ),
            (
                "page/experience/feedback",
                self.page_experience_feedback,
                ["POST"],
                "保存行为反馈",
            ),
        )
        for endpoint, handler, methods, desc in (
            dashboard_routes + programmatic_routes
        ):
            self.context.register_web_api(
                f"/{PLUGIN_ID}/{endpoint}", handler, methods, desc
            )
