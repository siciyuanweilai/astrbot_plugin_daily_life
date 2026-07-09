import datetime
import uuid

from astrbot.api import logger

from ..config.vocab import WEEKDAY_CN
from ..models import WeekPlanRecord
from ..prompts import cache_friendly_prompt, json_output_section
from ..clock import now as life_now
from .tools import extract_json_from_text, get_monday_of_week, get_week_id


class WeekMixin:
    async def _get_week_plan(self) -> WeekPlanRecord:
        week_id = get_week_id()
        plan = await self.archive.get_week_plan(week_id)
        if plan:
            return plan
        return WeekPlanRecord(
            week_id=week_id,
            theme="自主生活周",
            goals=["根据近期状态自然安排"],
            daily_hints={},
            suggested_activities={},
            generated=False,
        )

    async def _get_week_progress(self):
        monday = get_monday_of_week()
        today = life_now()
        lines = []
        for i in range(7):
            day = monday + datetime.timedelta(days=i)
            if day.date() > today.date():
                break
            date_str = day.strftime("%Y-%m-%d")
            data = await self.archive.get_day(date_str)
            if data and data.timeline:
                first_act = data.timeline[0].activity[:50]
                lines.append(f"- {WEEKDAY_CN[i]}: {first_act}...")
        return "\n".join(lines) if lines else "本周暂无记录"

    async def _ensure_week_plan(self, goals: str = "", web_inspiration: str = "") -> WeekPlanRecord:
        plan = await self.archive.get_week_plan(get_week_id())
        if plan and plan.generated:
            return plan
        generated = await self._generate_week_plan_unlocked(goals=goals, web_inspiration=web_inspiration)
        return generated or await self._get_week_plan()

    async def generate_week_plan(self, goals: str = "", web_inspiration: str = ""):
        async with self._gen_lock:
            return await self._generate_week_plan_unlocked(goals=goals, web_inspiration=web_inspiration)

    async def _generate_week_plan_unlocked(self, goals: str = "", web_inspiration: str = ""):
        week_id = get_week_id()
        monday = get_monday_of_week()
        sunday = monday + datetime.timedelta(days=6)
        persona = await self._get_persona()
        progress = await self._get_week_progress()
        inertia = await self._build_life_inertia_context(life_now())
        autonomy = await self._build_autonomous_life_context(life_now())
        goals = str(goals or "").strip()
        web_inspiration = str(web_inspiration or "").strip()
        inspiration_section = (
            f"\n联网灵感参考：\n{web_inspiration}\n说明：这些内容只作为周计划的新鲜参考，不是必须执行的事项。\n"
            if web_inspiration
            else ""
        )
        date_keys = ", ".join(
            (monday + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(7)
        )
        fixed = f"""生成当前角色的本周自主生活周计划。
周计划是给每日生活生成使用的软参考，不是固定模板、打卡表或必须执行清单。
不要套用固定周模板，也不要按素材库抽签；请像在做一周生活反思和轻量规划一样，根据人设、近期生活惯性、记忆、已发生进度、天气季节感和用户目标自然决定。
{json_output_section()}

返回 JSON：
{{
  "theme": "本周主题，一句话，贴合角色近期状态",
  "goals": ["本周目标1", "本周目标2", "本周目标3"],
  "daily_hints": {{
    "YYYY-MM-DD": "当天提示",
    "...": "必须覆盖本周每天"
  }},
  "suggested_activities": {{
    "YYYY-MM-DD": ["活动1", "活动2"],
    "weekday": ["可复用工作日建议"],
    "weekend": ["可复用周末建议"]
  }}
}}

要求：
- daily_hints 优先使用本周资料里的日期键，必须覆盖本周每天。
- suggested_activities 可以使用日期键、weekday、weekend；每组 1 到 4 条即可。
- 目标和提示要给日程生成留出自主空间，不要写成每天必须照做的清单。
- 不要生成固定模板、权重或任何工坊字段。
"""
        dynamic = f"""人设：
{persona or '无'}

本周范围：{monday.strftime('%Y年%m月%d日')} 至 {sunday.strftime('%Y年%m月%d日')}
本周日期键：{date_keys}

用户目标：
{goals or '无特别指定'}

本周已发生进度：
{progress}
{inertia}
{autonomy}
{inspiration_section}"""
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="本周计划资料")
        week_session_id = f"daily_life_week_{uuid.uuid4().hex[:8]}"
        try:
            provider_id = self._generation_provider_id()
            provider = await self._get_provider(provider_id)
            if not provider:
                return None
            completion_text = await self._call_llm_text(
                provider,
                prompt,
                week_session_id,
                primary_provider_id=provider_id,
            )
            result = extract_json_from_text(completion_text)
            if not isinstance(result, dict):
                return None
            result["generated"] = True
            plan = WeekPlanRecord.from_value(result, week_id=week_id)
            if not plan.theme:
                plan.theme = "自主生活周"
            if not plan.goals:
                plan.goals = ["根据近期状态自然安排"]
            await self.archive.save_week_plan(plan)
            await self._save_life_decision_record(
                kind="weekly_plan",
                date=monday.strftime("%Y-%m-%d"),
                subject=week_id,
                decision=plan.theme,
                reason="结合人设、近期生活惯性、本周进度、短期目标和可用灵感维护自主周计划。",
                evidence="；".join(plan.goals[:4]),
                outcome="；".join(
                    f"{key}:{value}" for key, value in list(plan.daily_hints.items())[:4]
                ),
            )
            return plan
        except Exception as e:
            logger.error(f"[周计划生成] 生成失败：{e}")
        finally:
            await self._cleanup_conversation(week_session_id)
        return None
