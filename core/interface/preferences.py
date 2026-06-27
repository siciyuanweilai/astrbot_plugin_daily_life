from typing import Any, AsyncIterator

from astrbot.api import logger

from ..config.vocab import TEMPLATE_CN_MAP
from ..labels import template_label, weekday_label
from ..life.tools import format_text_list
from ..templates import DEFAULT_WEEK_TEMPLATES
from .request import CommandRequest


class SettingsCommandMixin:
    async def _time(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if not req.param1:
            yield event.plain_result("时间格式错误，请使用 HH:MM 格式，例如 /生活 时间 07:30")
            return
        try:
            raw_hour, raw_minute = map(int, req.param1.split(":", 1))
            if not (0 <= raw_hour <= 23 and 0 <= raw_minute <= 59):
                raise ValueError("小时需为0-23，分钟需为0-59")
            normalized_time = f"{raw_hour:02d}:{raw_minute:02d}"
            self.runtime.rhythm.update_daily_time(normalized_time)
            persisted = self.runtime._persist_schedule_time(normalized_time)
            suffix = "已持久保存。" if persisted else "当前运行期间生效。"
            yield event.plain_result(f"✅ 已将每日生活背景生成时间更新为 {normalized_time}，{suffix}")
            logger.info(f"[日常生活] 用户手动将日程时间更新为 {req.param1}")
        except ValueError:
            yield event.plain_result("时间格式错误，请使用 HH:MM 格式，例如 /生活 时间 07:30")
        except Exception as e:
            yield event.plain_result(f"设置失败：{e}")

    async def _config(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        config = self.runtime.config
        weather_status = "✅ 已配置" if config.weather.api_key else "❌ 未配置"
        outfit_aware = "✅ 开启" if config.weather.aware_outfit else "❌ 关闭"
        activity_aware = "✅ 开启" if config.weather.aware_activity else "❌ 关闭"
        state_status = "✅ 开启" if config.state.enabled else "❌ 关闭"
        default_provider = "当前默认模型"
        generation_provider = config.llm_provider or default_provider
        state_provider = config.state.provider or default_provider
        outfit_provider = config.outfit.provider or default_provider
        review_provider = config.lifecycle.provider or default_provider
        invite_provider = config.invite.provider or default_provider
        material_provider = config.material.provider or default_provider
        vision_provider = config.vision.provider or default_provider
        daily_keep = f"{config.storage.daily_keep_days} 天" if config.storage.daily_keep_days else "长期"
        review_keep = f"{config.storage.review_keep_days} 天" if config.storage.review_keep_days else "长期"
        quiet_hours = f"；静默 {config.state.quiet_hours}" if config.state.quiet_hours else ""
        week_day = weekday_label(config.week_plan_day) or config.week_plan_day
        yield event.plain_result(
            f"""⚙️ 配置状态
🌤️ 天气API: {weather_status}
📍 默认城市: {config.weather.default_city or '未配置'}
👔 穿搭感知: {outfit_aware}
🏃 活动感知: {activity_aware}
🫧 实时状态: {state_status}（{config.state.refresh_minutes} 分钟巡检状态与穿搭{quiet_hours}）
🧠 模型: 生成 {generation_provider} · 状态 {state_provider} · 穿搭 {outfit_provider} · 复盘 {review_provider} · 邀约 {invite_provider} · 素材 {material_provider} · 视觉 {vision_provider}
⏰ 生活背景生成时间: {config.schedule_time}
📆 周计划生成: {week_day} {config.week_plan_time}
🗃️ 数据保留: 每日 {daily_keep} · 复盘 {review_keep}"""
        )

    async def _templates(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        action = req.param1
        template_id = req.param2
        detail = " ".join(req.parts[4:]) if len(req.parts) > 4 else ""

        if not action or action == "列表":
            templates = await self.runtime.composer._get_week_templates(include_disabled=True)
            custom = await self.runtime.archive.get_custom_week_templates(include_disabled=True)
            lines = ["📚 可用周模板"]
            for tid, template in templates.items():
                mark = "自定义" if tid in custom else "内置"
                enabled = "" if template.get("enabled", True) else "（已禁用）"
                weight = template.get("weight", self.runtime.config.week_template_weights.get(tid, 0.1))
                label = template_label(tid) if mark == "内置" else tid
                weight_label = "自定义权重" if mark == "自定义" else "内置权重"
                lines.append(
                    f"- {label} [{mark}] {template.get('emoji', '')} {template.get('name', '未知')}"
                    f" · {weight_label} {weight}{enabled}"
                )
            lines.append("\n管理：/生活 模板 新建/查看/权重/启用/禁用/删除；权重指自定义模板随机权重。")
            yield event.plain_result("\n".join(lines))
            return

        if action == "查看":
            if not template_id:
                yield event.plain_result("请指定模板，如：/生活 模板 查看 恢复")
                return
            templates = await self.runtime.composer._get_week_templates(include_disabled=True)
            template = templates.get(TEMPLATE_CN_MAP.get(template_id, template_id))
            if not template:
                yield event.plain_result(f"未找到模板：{template_id}")
                return
            resolved_template_id = TEMPLATE_CN_MAP.get(template_id, template_id)
            title = template_label(resolved_template_id) or resolved_template_id
            goals = format_text_list(template.get("goals", []), default="无")
            tags = format_text_list(template.get("tags", []), default="无")
            hints = template.get("daily_hints", {})
            hint_lines = [
                f"- {weekday_label(day) or day}: {hints.get(day, '')}"
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
                if hints.get(day)
            ]
            yield event.plain_result(
                f"📚 {title}\n"
                f"{template.get('emoji', '')} {template.get('name', '未知')}\n"
                f"{template.get('description', '')}\n"
                f"目标：{goals}\n"
                f"标签：{tags}\n"
                f"提示：\n{chr(10).join(hint_lines) if hint_lines else '无'}"
            )
            return

        if action in {"新建", "创建"}:
            instruction = " ".join(req.parts[3:]).strip()
            if not instruction:
                yield event.plain_result("请描述模板，例如：/生活 模板 新建 轻恢复周：这周偏累，减少外出，早睡和轻社交")
                return
            yield event.plain_result("正在整理周模板...")
            try:
                template = await self.runtime.composer.compose_week_template_from_text(instruction)
                saved = await self.runtime.archive.save_custom_week_template(template)
                yield event.plain_result(
                    f"✅ 已保存自定义模板：{saved.template_id}\n"
                    f"{saved.emoji} {saved.name}\n"
                    f"{saved.description}\n"
                    f"目标：{format_text_list(saved.goals, default='无')}"
                )
            except Exception as e:
                yield event.plain_result(f"模板创建失败：{e}")
            return

        if action == "权重":
            if not template_id or not detail:
                yield event.plain_result("格式：/生活 模板 权重 [自定义模板ID] [数字]")
                return
            try:
                weight = max(float(detail), 0.0)
            except ValueError:
                yield event.plain_result("权重必须是数字，例如：/生活 模板 权重 [自定义模板ID] 0.3")
                return
            ok = await self.runtime.archive.set_custom_week_template_weight(template_id, weight)
            yield event.plain_result("✅ 已更新自定义模板权重" if ok else f"未找到自定义模板：{template_id}")
            return

        if action in {"启用", "禁用"}:
            if not template_id:
                yield event.plain_result("请指定模板ID")
                return
            template_id = TEMPLATE_CN_MAP.get(template_id, template_id)
            enabled = action == "启用"
            if template_id in DEFAULT_WEEK_TEMPLATES:
                ok = await self.runtime.archive.set_builtin_item_enabled("template", template_id, enabled)
            else:
                ok = await self.runtime.archive.set_custom_week_template_enabled(template_id, enabled)
            yield event.plain_result("✅ 已更新模板状态" if ok else f"未找到模板：{template_id}")
            return

        if action in {"删除", "移除"}:
            if not template_id:
                yield event.plain_result("请指定模板ID")
                return
            ok = await self.runtime.archive.delete_custom_week_template(template_id)
            yield event.plain_result("✅ 已删除自定义模板" if ok else f"未找到自定义模板：{template_id}")
            return

        yield event.plain_result("未知模板指令，支持：列表/查看/新建/权重/启用/禁用/删除")
