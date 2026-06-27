import datetime
from typing import Any, AsyncIterator

from ..labels import event_status_label, preference_category_label
from ..life.condition import format_state_display
from ..life.tools import (
    format_text_list,
    format_timeline_to_text,
    get_current_timeline_status,
    get_time_period_cn,
    get_week_id,
    resolve_daily_hint,
    resolve_daily_suggested,
)
from ..life.surroundings import format_world_display
from ..models import TimelineItem
from ..clock import now as life_now
from .request import CommandRequest
from ..config.vocab import PERIOD_ORDER


class DisplayCommandMixin:
    async def _help(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        yield event.plain_result(
            """📅 日常生活背景引擎
🕐 时段：凌晨 | 早晨 | 上午 | 中午 | 下午 | 傍晚 | 晚上 | 深夜

📋 常用指令：
/生活 显示 - 查看今日日程
/生活 显示 历史 - 查看今日穿搭历史
/生活 显示 周计划 - 查看周计划
/生活 时间 [HH:MM] - 设置每日生成时间
/生活 状态 - 查看此刻正在做什么
/生活 刷新状态 - 立即更新体力、心情、忙碌度等状态
/生活 剧透 - 偷看接下来未发生的安排
/生活 邀约 [事情] - 尝试约她打断计划
/生活 备忘录 [内容] - 强制安排明天的任务
/生活 承诺 - 查看未完成约定
/生活 承诺 添加/完成/取消/延期 - 管理未来约定
/生活 重置 - 重新生成穿搭、日程
/生活 重置 [补充指令] - 附带额外指令重生成
/生活 重置 保持 - 重新生成穿搭，保留日程
/生活 清空 - 清空全部日常生活背景数据
/生活 重置 [时段] - 以目标时间线索触发自主状态/穿搭更新
  时段可选: 凌晨/早晨/上午/中午/下午/傍晚/晚上/深夜
/生活 重置 [模板] [目标] - 生成新周计划
  模板可选: 常规/冲刺/放松/社交/恢复/假期/随机
/生活 天气 [城市] - 查询天气
/生活 模板 - 查看可用周模板
/生活 模板 新建 [描述] - 用自然语言创建自定义周模板
/生活 模板 查看 [模板] - 查看模板详情
/生活 模板 权重 [模板] [数字] - 调整自定义模板随机权重
/生活 模板 启用/禁用/删除 [模板] - 管理自定义模板
/生活 历史 [天数] - 查看历史日程
/生活 世界 - 查看日常生活世界档案
/生活 时间轴 - 查看当前时间轴
/生活 复盘 [生成] - 查看或生成每日复盘
/生活 偏好 - 查看已学习偏好
/生活 事件 - 查看生活事件
/生活 存储 - 查看数据分类与占用
/生活 存储 清理 [分类] [天数] - 按保留天数清理分类
/生活 存储 清空 [分类] - 清空指定分类"""
        )

    async def _status(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        data = await self.runtime.archive.get_day(req.target_date_str)
        if not data or not data.timeline:
            yield event.plain_result("今天还没有安排日程哦。")
            return

        curr, next_act = get_current_timeline_status(data.timeline, req.now, data.date)
        if curr:
            activity_text = (
                f"当前是 {req.now.strftime('%H:%M')}，现状：\n"
                f"{curr.activity}（状态：{curr.status or '平和'}）"
            )
        elif next_act:
            activity_text = (
                f"当前是 {req.now.strftime('%H:%M')}，还没有进入第一项安排。\n"
                f"下一项：{next_act.time} {next_act.activity}"
            )
        else:
            activity_text = "当前是休息时间。"
        yield event.plain_result(f"{activity_text}\n\n{format_state_display(data.state)}")

    async def _refresh_state(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        data = await self.runtime.archive.get_day(req.target_date_str)
        if not data or not data.timeline:
            yield event.plain_result("今天还没有安排日程哦，暂时无法刷新状态。")
            return
        data = await self.runtime.refresh_state_for_day(
            req.target_date_str,
            data,
            req.now,
            source="manual",
            detail=req.param_full,
            force=True,
        )
        yield event.plain_result(format_state_display(data.state if data else None))

    async def _show(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if not req.param1:
            data = await self.runtime.archive.get_day(req.target_date_str)
            if data and data.timeline:
                tl_text = format_timeline_to_text(data.timeline)
                meta = data.meta
                w_info = data.weather_info
                w_str = f"{data.weather or '未知'} ({w_info.temp_desc})"
                yield event.plain_result(
                    f"📅 今日安排 ({meta.get('theme', '日常')})\n"
                    f"🌤️ {w_str}\n"
                    f"👔 {data.outfit or '无'}\n\n"
                    f"📍 全天时间轴：\n{tl_text}"
                )
            else:
                yield event.plain_result("今天暂无记录。")
            return

        if req.param1 == "周计划":
            plan = await self.runtime.composer._get_week_plan()
            prog = await self.runtime.composer._get_week_progress()
            goals = format_text_list(plan.goals, default="无")
            today_hint = resolve_daily_hint(plan, req.now, default="无")
            today_suggested = resolve_daily_suggested(plan, req.now, default="无")
            yield event.plain_result(
                f"📅 本周 ({get_week_id()})\n"
                f"🎯 计划: {plan.theme or '未设定'}\n"
                f"📌 目标: {goals}\n"
                f"📍 今日: {today_hint}\n"
                f"💡 建议: {today_suggested}\n\n"
                f"✅ 进度:\n{prog}"
            )
            return

        if req.param1 == "历史":
            data = await self.runtime.archive.get_day(req.target_date_str)
            if data and data.outfit_history:
                history = data.outfit_history
                lines = [f"📅 {req.target_date_str} 穿搭历史"]
                for key in sorted(history, key=lambda item: PERIOD_ORDER.get(item, 999)):
                    if key in history:
                        lines.append(f"{get_time_period_cn(key)}:\n{history[key]}")
                yield event.plain_result("\n\n".join(lines))
            else:
                yield event.plain_result(f"{req.target_date_str} 暂无穿搭历史记录")
            return

        yield event.plain_result("未知显示项，使用 /生活 帮助 查看帮助")

    async def _spoiler(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        data = await self.runtime.archive.get_day(req.target_date_str)
        if not data or not data.timeline:
            yield event.plain_result("今天还没有安排日程哦，无法剧透。")
            return

        now_mins = req.now.hour * 60 + req.now.minute
        future_timeline = [
            item
            for item in data.timeline
            if self._timeline_minutes(item) > now_mins
        ]
        if future_timeline:
            yield event.plain_result(f"🤫 嘘...剧透一下接下来的安排：\n\n{format_timeline_to_text(future_timeline)}")
            return

        tomorrow = req.now + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        week_plan = await self.runtime.composer._get_week_plan()
        tomorrow_hint = resolve_daily_hint(week_plan, tomorrow, default="暂无特殊安排")
        tomorrow_data = await self.runtime.archive.get_day(tomorrow_str)
        memo = tomorrow_data.memo if tomorrow_data else ""
        msg = f"🌙 今天的计划已经全部结束啦。\n\n📅 【明日前瞻】\n💡 主题走向：{tomorrow_hint}"
        if memo:
            msg += f"\n📌 强制待办：\n{memo}"
        msg += "\n\n(详细的具体时间轴将在明天起床时生成~)"
        yield event.plain_result(msg)

    @staticmethod
    def _timeline_minutes(item: TimelineItem) -> int:
        try:
            h, m = map(int, item.time.split(":"))
            return h * 60 + m
        except (TypeError, ValueError):
            return -1

    async def _history(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        days = int(req.param1) if req.param1.isdigit() else 7
        results = []
        now = life_now()
        for i in range(days):
            day = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            info = await self.runtime.archive.get_day(day)
            if info and info.timeline:
                w_info = info.weather_info
                w_mark = (
                    f" [{w_info.temp}°C {w_info.condition}]"
                    if w_info.temp is not None
                    else ""
                )
                p_mark = f" ({get_time_period_cn(info.time_period or 'unknown')})"
                first_act = info.timeline[0].activity[:80]
                results.append(f"📅 {day}{p_mark}{w_mark}\n{first_act}...")
        yield event.plain_result("\n\n".join(results) if results else f"最近 {days} 天没有记录")

    async def _world(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        yield event.plain_result(
            format_world_display(
                await self.runtime.archive.get_recent_relationships(8),
                await self.runtime.archive.get_recent_places(10),
                await self.runtime.archive.get_recent_events(10),
                await self.runtime.archive.get_recent_chat_summaries(8),
                await self.runtime.archive.get_recent_group_environments(5),
                await self.runtime.archive.get_recent_action_decisions(5),
                await self.runtime.archive.get_recent_message_visibility(5),
            )
        )

    async def _timeline(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        data = await self.runtime.archive.get_day(req.target_date_str)
        if not data or not data.timeline:
            yield event.plain_result("当前没有时间轴记录。")
            return
        lines = [f"🧭 时间轴 ({req.target_date_str})"]
        for index, item in enumerate(data.timeline, start=1):
            status = f" [{item.status}]" if item.status else ""
            lines.append(f"{index}. {item.time} - {item.activity}{status}")
        lines.append("\n编辑时间轴请在插件设置页的时间轴编辑器中保存。")
        yield event.plain_result("\n".join(lines))

    async def _review(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        target = req.param2 or req.target_date_str
        if req.param1 in {"生成", "刷新", "重做"}:
            review = await self.runtime.composer.compose_daily_review(target, force=True)
        else:
            review = await self.runtime.archive.get_daily_review(target)
            if not review:
                review = await self.runtime.composer.compose_daily_review(target)
        if not review:
            yield event.plain_result(f"{target} 暂无可复盘的生活记录。")
            return
        lines = [f"🌙 每日复盘 {review.date}", review.summary or "无摘要"]
        if review.memory_points:
            lines.append("\n记忆沉淀：")
            lines.extend(f"- {item}" for item in review.memory_points[:6])
        if review.preference_points:
            lines.append("\n偏好学习：")
            lines.extend(f"- [{preference_category_label(item.category)}] {item.content}" for item in review.preference_points[:6])
        if review.life_events:
            lines.append("\n生活事件：")
            lines.extend(f"- {item.title}" for item in review.life_events[:6])
        yield event.plain_result("\n".join(lines))

    async def _preferences(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        items = await self.runtime.archive.get_preferences(20)
        if not items:
            yield event.plain_result("暂时还没有学习到稳定偏好。")
            return
        lines = ["🧭 已学习偏好"]
        lines.extend(
            f"- [{preference_category_label(item.category)}] {item.content}（权重 {item.weight:.1f}）"
            for item in items
        )
        yield event.plain_result("\n".join(lines))

    async def _life_events(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        items = await self.runtime.archive.get_life_events(limit=20)
        if not items:
            yield event.plain_result("暂时还没有生活事件。")
            return
        lines = ["✨ 生活事件"]
        lines.extend(
            f"#{item.id} [{event_status_label(item.status)}] {item.date or '未定'} {item.title}\n- {item.effect or item.detail or '等待自然影响'}"
            for item in items
        )
        yield event.plain_result("\n".join(lines))

    async def _storage(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        action = req.param1
        category = req.param2
        keep_days = None
        if len(req.parts) > 4:
            try:
                keep_days = max(int(float(req.parts[4])), 0)
            except ValueError:
                yield event.plain_result("保留天数必须是数字，例如：/生活 存储 清理 日常记录 30")
                return

        try:
            if action == "清理":
                if category:
                    result = await self.runtime.archive.cleanup_storage_category(category, keep_days)
                    await self._cleanup_emoji_cache()
                    yield event.plain_result(
                        f"✅ 已清理{result['label']}：删除 {result['deleted_rows']} 行，保留 {result['keep_days']} 天内数据。"
                    )
                else:
                    result = await self.runtime.archive.cleanup_by_storage_policy(self.runtime.config.storage)
                    await self._cleanup_emoji_cache()
                    yield event.plain_result(f"✅ 已按存储策略清理：删除 {result['deleted_rows']} 行。")
                return

            if action == "清空":
                if not category:
                    yield event.plain_result("请指定要清空的分类，例如：/生活 存储 清空 日常记录")
                    return
                result = await self.runtime.archive.clear_storage_category(category)
                await self._cleanup_emoji_cache()
                yield event.plain_result(f"✅ 已清空{result['label']}：删除 {result['deleted_rows']} 行。")
                return

            overview = await self.runtime.archive.get_storage_overview(self.runtime.config.storage)
            lines = [f"🗃️ 数据分区（共 {overview.get('total_rows', 0)} 行）"]
            for item in overview.get("categories", []):
                days = int(item.get("retention_days") or 0)
                retention = f"保留 {days} 天" if days > 0 else "长期保留"
                lines.append(
                    f"- {item.get('label', item.get('key'))}: {item.get('total_rows', 0)} 行 · {retention}"
                )
                for group in item.get("groups") or []:
                    rows = int(group.get("total_rows") or 0)
                    if rows > 0:
                        lines.append(f"  · {group.get('label', group.get('key'))}: {rows} 行")
            lines.append("\n维护：/生活 存储 清理 [分类] [天数]；/生活 存储 清空 [分类]")
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            yield event.plain_result(f"存储维护失败：{exc}")

    async def _cleanup_emoji_cache(self) -> None:
        maintain = getattr(self.runtime, "maintain_emoji_assets", None)
        if callable(maintain):
            await maintain()
            return
        cleanup = getattr(self.runtime, "cleanup_emoji_asset_cache", None)
        if callable(cleanup):
            await cleanup()
