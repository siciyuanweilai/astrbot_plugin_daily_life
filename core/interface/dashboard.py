from ..runtime import PLUGIN_ID
from .portal import (
    PortalActionMixin,
    PortalBaseMixin,
    PortalHairdoMixin,
    PortalLineMixin,
    PortalMemoryMixin,
    PortalPoolMixin,
    PortalReferenceMixin,
    PortalWeekMixin,
)
from .view import PageViewMixin
from .workshop import PageWorkshopMixin


class DailyLifeDashboardMixin(
    PageViewMixin,
    PageWorkshopMixin,
    PortalBaseMixin,
    PortalReferenceMixin,
    PortalActionMixin,
    PortalLineMixin,
    PortalMemoryMixin,
    PortalWeekMixin,
    PortalPoolMixin,
    PortalHairdoMixin,
):
    """日常生活插件的轻量页面面板。"""

    def _register_page_web_apis(self) -> None:
        routes = (
            ("page/status", self.page_status, ["GET"], "日常生活面板状态"),
            ("page/status/wait", self.page_status_wait, ["GET"], "等待日常生活面板状态更新"),
            ("page/action/refresh-state", self.page_refresh_state, ["POST"], "刷新日常生活状态"),
            ("page/action/reset-day", self.page_reset_day, ["POST"], "重生成日常生活"),
            ("page/action/generate-week", self.page_generate_week, ["POST"], "生成周计划"),
            ("page/timeline/save", self.page_timeline_save, ["POST"], "保存时间轴"),
            ("page/config", self.page_config, ["GET", "POST"], "日常生活设置"),
            ("page/config/character-reference", self.page_character_reference_upload, ["POST"], "上传角色形象参考图"),
            ("page/config/character-reference/preview", self.page_character_reference_preview, ["POST"], "预览角色形象参考图"),
            ("page/config/character-reference/delete", self.page_character_reference_delete, ["POST"], "删除角色形象参考图"),
            ("page/experience/episode/correct", self.page_experience_episode_correct, ["POST"], "纠正生活片段"),
            ("page/experience/episode/protect", self.page_experience_episode_protect, ["POST"], "保护生活片段"),
            ("page/experience/focus", self.page_experience_focus, ["POST"], "保存关注目标"),
            ("page/experience/boundary", self.page_experience_boundary, ["POST"], "保存记忆边界"),
            ("page/experience/feedback", self.page_experience_feedback, ["POST"], "保存行为反馈"),
            ("page/template/create", self.page_template_create, ["POST"], "创建周模板"),
            ("page/template/save", self.page_template_save, ["POST"], "保存周模板"),
            ("page/template/weight", self.page_template_weight, ["POST"], "调整自定义模板权重"),
            ("page/template/enabled", self.page_template_enabled, ["POST"], "启用或禁用周模板"),
            ("page/template/delete", self.page_template_delete, ["POST"], "删除周模板"),
            ("page/catalog/create", self.page_catalog_create, ["POST"], "智能创建创意素材"),
            ("page/catalog/save", self.page_catalog_save, ["POST"], "保存创意素材"),
            ("page/catalog/enabled", self.page_catalog_enabled, ["POST"], "启用或禁用创意素材"),
            ("page/catalog/delete", self.page_catalog_delete, ["POST"], "删除创意素材"),
            ("page/hair/create", self.page_hair_create, ["POST"], "智能创建发型组"),
            ("page/hair/save", self.page_hair_save, ["POST"], "保存发型组"),
            ("page/hair/enabled", self.page_hair_enabled, ["POST"], "启用或禁用发型组"),
            ("page/hair/delete", self.page_hair_delete, ["POST"], "删除发型组"),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(f"/{PLUGIN_ID}/{endpoint}", handler, methods, desc)
