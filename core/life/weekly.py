import datetime
import random
import uuid

from astrbot.api import logger

from ..config.vocab import WEEKDAY_CN, WEEKDAY_NAMES
from ..models import WeekPlanRecord, WeekTemplateRecord
from ..prompts import cache_friendly_prompt, json_output_section
from ..clock import now as life_now
from ..templates import DEFAULT_WEEK_TEMPLATES
from .tools import extract_json_from_text, get_monday_of_week, get_week_id


class WeekMixin:
    async def _get_week_templates(self, include_disabled: bool = False) -> dict[str, dict]:
        builtin_states = await self.archive.get_builtin_item_states("template")
        templates = {
            template_id: {**dict(template), "enabled": builtin_states.get(template_id, True)}
            for template_id, template in DEFAULT_WEEK_TEMPLATES.items()
            if isinstance(template, dict) and (include_disabled or builtin_states.get(template_id, True))
        }
        custom_templates = await self.archive.get_custom_week_templates(include_disabled=include_disabled)
        for template_id, template in custom_templates.items():
            if include_disabled or template.enabled:
                templates[template_id] = template.as_template_dict()
        return templates

    async def _get_week_plan(self) -> WeekPlanRecord:
        week_id = get_week_id()
        plans = await self.archive.get_all_week_plans()
        if week_id in plans:
            return plans[week_id]

        week_templates = await self._get_week_templates()
        template = (
            week_templates.get(self.config.default_week_template)
            or week_templates.get("regular")
            or next(iter(week_templates.values()), {})
        )
        if not template:
            template = {
                "emoji": "📊",
                "name": "日常周",
                "description": "暂无启用模板",
                "theme": "日常周",
                "goals": [],
                "daily_hints": {},
                "suggested_activities": {},
            }

        return WeekPlanRecord(
            week_id=week_id,
            theme=f"{template.get('emoji','')} {template.get('name','')}",
            goals=["按日常节奏"],
            daily_hints=template.get("daily_hints", {}),
            suggested_activities=template.get("suggested_activities", {}),
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

    async def generate_week_plan(self, template_id=None, goals="", web_inspiration: str = ""):
        async with self._gen_lock:
            week_templates = await self._get_week_templates()
            templates_pool = list(week_templates.keys())
            if not templates_pool:
                return None

            if template_id is None:
                template_id = self.config.default_week_template

            if template_id == "random":
                lookback_weeks = 3
                used_templates = set()
                current_monday = get_monday_of_week()
                all_plans = await self.archive.get_all_week_plans()

                for i in range(1, lookback_weeks + 1):
                    past_monday = current_monday - datetime.timedelta(weeks=i)
                    past_week_id = past_monday.strftime("%Y-W%W")
                    if past_week_id in all_plans:
                        past_template_id = all_plans[past_week_id].template_id
                        if past_template_id:
                            used_templates.add(past_template_id)

                available = [item for item in templates_pool if item not in used_templates]
                candidate_pool = available or templates_pool
                weights_config = self.config.week_template_weights
                weights = [
                    max(float(weights_config.get(item, week_templates.get(item, {}).get("weight", 0.1))), 0.0)
                    for item in candidate_pool
                ]
                template_id = (
                    random.choices(candidate_pool, weights=weights, k=1)[0]
                    if sum(weights) > 0
                    else random.choice(candidate_pool)
                )

            template = week_templates.get(template_id, week_templates.get("regular"))
            if not template and week_templates:
                template = list(week_templates.values())[0]
            if not template:
                return None

            week_id = get_week_id()
            monday = get_monday_of_week()
            sunday = monday + datetime.timedelta(days=6)
            persona = await self._get_persona()
            inspiration = str(web_inspiration or "").strip()
            inspiration_section = (
                f"\n联网灵感参考：\n{inspiration}\n说明：这些内容只作为周计划节奏、目标和活动灵感参考，不是必须执行的事项。\n"
                if inspiration
                else ""
            )
            fixed = f"""生成当前角色的本周计划，主题、目标、每日提示和建议活动都要贴合模板、人设和用户目标。

返回JSON：
{{
    "theme": "主题",
    "goals": ["目标"],
    "daily_hints": {{
        "{monday.strftime('%Y-%m-%d')}": "周一（{monday.strftime('%m.%d')}）的提示/定位",
        ...
        "{sunday.strftime('%Y-%m-%d')}": "周日（{sunday.strftime('%m.%d')}）的提示/定位"
    }},
    "suggested_activities": {{...}}
}}"""
            dynamic = f"""本周范围：{monday.strftime('%Y年%m月%d日')}至{sunday.strftime('%Y年%m月%d日')}
模板：{template.get('name')} - {template.get('description')}
人设：{persona}
目标：{goals if goals else '无特别指定'}
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
                if not completion_text:
                    return None

                result = extract_json_from_text(completion_text)
                if not result:
                    result = {
                        "theme": f"{template.get('emoji','')} {template.get('name','')}",
                        "goals": ["按模板节奏"],
                        "daily_hints": template.get("daily_hints", {}),
                        "suggested_activities": template.get("suggested_activities", {}),
                    }
                result["template_id"] = template_id
                result["generated"] = True
                plan = WeekPlanRecord.from_value(result, week_id=week_id)
                await self.archive.save_week_plan(plan)
                return plan
            except Exception as e:
                logger.error(f"[周计划生成] 生成失败：{e}")
            finally:
                await self._cleanup_conversation(week_session_id)
            return None

    async def compose_week_template_from_text(self, instruction: str, use_web: bool = False) -> WeekTemplateRecord:
        text = str(instruction or "").strip()
        if not text:
            raise ValueError("模板描述不能为空")
        web_section = await self._web_week_template_inspiration(text, use_web=use_web)
        session_id = f"daily_life_template_{uuid.uuid4().hex[:8]}"
        fixed = f"""把用户描述整理成一个周计划模板。

{json_output_section()}

输出结构：
{{
  "template_id": "短英文或拼音标识，只能包含小写字母数字下划线",
  "name": "模板名称",
  "emoji": "一个合适 emoji",
  "description": "一句话描述这个周的节奏",
  "weight": 0.1,
  "cooldown_weeks": 3,
  "goals": ["目标1", "目标2", "目标3"],
  "daily_hints": {{
    "monday": "周一提示",
    "tuesday": "周二提示",
    "wednesday": "周三提示",
    "thursday": "周四提示",
    "friday": "周五提示",
    "saturday": "周六提示",
    "sunday": "周日提示"
  }},
  "suggested_activities": {{
    "weekday": ["工作日建议1", "工作日建议2"],
    "weekend": ["周末建议1", "周末建议2"]
  }},
  "tags": ["标签1", "标签2"]
}}

        要求：
- 只生成这个单个模板对象。
- daily_hints 必须覆盖 monday 到 sunday。
- suggested_activities 至少包含 weekday 和 weekend。
- 模板要贴合用户描述，不要套用默认模板。"""
        dynamic = f"""用户描述：
{text}
{web_section}"""
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="周模板需求")
        try:
            provider_id = self._generation_provider_id()
            provider = await self._get_provider(provider_id)
            if provider:
                completion_text = await self._call_llm_text(
                    provider,
                    prompt,
                    session_id,
                    primary_provider_id=provider_id,
                )
                result = extract_json_from_text(completion_text)
                template = WeekTemplateRecord.from_value(result)
                if template:
                    return self._normalize_week_template(template, fallback_text=text)
        finally:
            await self._cleanup_conversation(session_id)
        return self._fallback_week_template(text)

    async def _web_week_template_inspiration(self, text: str, use_web: bool = False) -> str:
        if not use_web:
            return ""
        summary = await self.web_inspiration.search(
            text,
            self.config.web_inspiration.week_template_prompt,
            category="周计划模板",
            persona=await self._get_persona(),
        )
        return f"\n\n{summary}" if summary else ""

    def _normalize_week_template(self, template: WeekTemplateRecord, fallback_text: str = "") -> WeekTemplateRecord:
        template.template_id = self._normalize_template_id(template.template_id or template.name)
        template.name = template.name[:32]
        template.description = template.description or fallback_text[:80] or template.name
        template.goals = template.goals[:6] or [template.description]
        template.tags = template.tags[:8]
        template.daily_hints = dict(template.daily_hints or {})
        for day_key in WEEKDAY_NAMES:
            template.daily_hints.setdefault(day_key, f"{template.emoji} {template.name}")
        template.suggested_activities = dict(template.suggested_activities or {})
        template.suggested_activities.setdefault("weekday", template.goals[:2])
        template.suggested_activities.setdefault("weekend", template.goals[2:4] or template.goals[:2])
        template.enabled = True
        template.source = "custom"
        return template

    def _fallback_week_template(self, text: str) -> WeekTemplateRecord:
        name, desc = self._split_template_text(text)
        template = WeekTemplateRecord(
            template_id=self._normalize_template_id(name),
            name=name,
            description=desc,
            emoji="📅",
            weight=0.1,
            cooldown_weeks=3,
            goals=[item for item in self._split_template_items(desc)[:4]] or [desc],
            daily_hints={day: f"📅 {name}：{desc[:24]}" for day in WEEKDAY_NAMES},
            suggested_activities={},
            tags=[],
        )
        return self._normalize_week_template(template, fallback_text=text)

    @staticmethod
    def _split_template_text(text: str) -> tuple[str, str]:
        for sep in ("：", ":", "，", ","):
            if sep in text:
                left, right = text.split(sep, 1)
                name = left.strip()[:32] or "自定义周"
                desc = right.strip() or text.strip()
                return name, desc
        return text[:12] or "自定义周", text

    @staticmethod
    def _split_template_items(text: str) -> list[str]:
        parts = []
        for chunk in text.replace("、", "，").replace(",", "，").split("，"):
            item = chunk.strip()
            if item:
                parts.append(item)
        return parts

    @staticmethod
    def _normalize_template_id(value: str) -> str:
        text = str(value or "").strip().lower()
        result = []
        for char in text:
            if char.isascii() and (char.isalnum() or char == "_"):
                result.append(char)
            elif char in {"-", " ", "."}:
                result.append("_")
        normalized = "".join(result).strip("_")
        if normalized:
            return normalized[:40]
        stable = sum((idx + 1) * ord(char) for idx, char in enumerate(text))
        return f"custom_{stable % 1000000:06d}"
