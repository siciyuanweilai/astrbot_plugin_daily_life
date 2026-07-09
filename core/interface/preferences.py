from typing import Any, AsyncIterator

from astrbot.api import logger

from .request import CommandRequest


class SettingsCommandMixin:
    async def _time(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if not req.param1:
            yield event.plain_result("时间格式错误，请使用 HH:MM 格式，例如 07:30。")
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
            yield event.plain_result("时间格式错误，请使用 HH:MM 格式，例如 07:30。")
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
        vision_provider = config.vision.provider or default_provider
        daily_keep = f"{config.storage.daily_keep_days} 天" if config.storage.daily_keep_days else "长期"
        review_keep = f"{config.storage.review_keep_days} 天" if config.storage.review_keep_days else "长期"
        quiet_hours = f"；静默 {config.state.quiet_hours}" if config.state.quiet_hours else ""
        yield event.plain_result(
            f"""⚙️ 配置状态
🌤️ 天气API: {weather_status}
📍 默认城市: {config.weather.default_city or '未配置'}
👔 穿搭感知: {outfit_aware}
🏃 活动感知: {activity_aware}
🫧 实时状态: {state_status}（{config.state.refresh_minutes} 分钟巡检状态与穿搭{quiet_hours}）
🧠 模型: 生成 {generation_provider} · 状态 {state_provider} · 穿搭 {outfit_provider} · 复盘 {review_provider} · 邀约 {invite_provider} · 视觉 {vision_provider}
⏰ 生活背景生成时间: {config.schedule_time}
🗃️ 数据保留: 每日 {daily_keep} · 复盘 {review_keep}"""
        )
