from ..models import WeekPlanRecord
from ..prompts import CORE_AUTONOMY_RULES, CORE_JSON_OUTPUT_RULES, CORE_STATE_BEHAVIOR_RULES, cache_friendly_prompt
from .wardrobe import (
    DAILY_OUTFIT_SCENE_STYLE_RULES,
    OUTFIT_SCENE_CATEGORY_ENUM,
    OUTFIT_STYLE_POOL_ENUM,
)
from .tools import format_text_list


class DailyDraftMixin:
    def _build_timeline_prompt(
        self,
        date_str: str,
        period_cn: str,
        weather_section: str,
        constraint_section: str,
        inspiration_section: str,
        previous_context: str,
        history_schedules_str: str,
        outfit_rules: str,
        memo_str: str,
        persona: str = "",
        week_plan: WeekPlanRecord | None = None,
        today_hint: str = "",
        today_suggested: str = "",
        recent_chats: str = "",
        schedule_intent: str = "",
        world_context: str = "",
        lifecycle_context: str = "",
        expected_coverage: str = "full_day",
        current_time_text: str = "",
    ) -> str:
        persona_section = f"\n## 👤 角色设定\n{persona}" if persona else ""
        contract_section = self._build_contract_prompt(expected_coverage)

        week_section = ""
        if week_plan:
            week_section = (
                "\n## 📅 本周计划参考"
                f"\n- 主题：{week_plan.theme or '常规周'}"
                f"\n- 目标：{format_text_list(week_plan.goals, default='无')}"
                f"\n- 今日提示：{today_hint or '按周主题安排'}"
                f"\n- 建议活动：{today_suggested or '无'}"
                f"\n- 日程倾向参考：{schedule_intent or '由生活决策决定'}"
            )

        chat_section = ""
        if recent_chats and recent_chats != "无":
            chat_section = (
                "\n## 💬 最近聊天参考\n"
                f"{self.config.chat_prompt}\n"
                f"{recent_chats}"
            )

        world_section = f"\n\n{world_context}" if world_context else ""
        lifecycle_section = f"\n\n{lifecycle_context}" if lifecycle_context else ""
        memo_section_text = ""
        if memo_str:
            memo_section_text = f"\n## 🔔 强制备忘录/用户指令\n以下是今天必须完成或加入日程的事项：\n{memo_str}"
        contract_example = self._contract_json_text(self._coverage_contract(expected_coverage)).replace("\n", "\n  ")

        fixed = f"""生成当前角色的自主生活背景。
当前/目标时间线索只是现实时间提示，不是强制生活状态；睡眠、熬夜、赖床、出门和换装都由 life_decision 自主判断。

{contract_section}

【通用自主原则】
{CORE_AUTONOMY_RULES}

【通用状态行为原则】
{CORE_STATE_BEHAVIOR_RULES}

【输出格式约束】
你必须返回严格的 JSON 格式，结构如下：
{{
  "generation_contract": {contract_example},
  "life_decision": {{
    "life_mode": "awake | sleeping | late_night | all_nighter | resting | going_out | mixed",
    "sleep": {{"mode": "normal | late_night | all_nighter | nap | early_sleep", "quality": 0-100, "depth": "awake | light_rest | light_sleep | deep_sleep", "summary": "昨晚或当前睡眠状态"}},
    "outfit": {{"decision": "keep | change | partial_change | sleepwear | outdoor", "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}", "style_pool": "{OUTFIT_STYLE_POOL_ENUM}", "style": "最终穿搭风格", "hair": "最终发型", "reason": "为什么这样决定"}},
    "day_plan": {{"schedule_type": "日程类型标签，例如“拥抱阳光的元气出游”或“宅家充电的慵懒一日”", "schedule_intent": "home | work | study | social | rest | outing | mixed", "energy_bias": "rest | normal | active", "social_bias": "avoid | light | social"}},
    "theme": "今天自然形成的主题",
    "mood": "心情色彩标签，必须是“颜色名·情绪词”格式，例如“奶油黄·慵懒”"
  }},
  "state": {{
    "energy": 0-100,
    "mood": "今天的心情底色",
    "mood_score": 0-100,
    "busyness": 0-100,
    "social": 0-100,
    "stress": 0-100,
    "focus": 0-100,
    "sleepiness": 0-100,
    "outgoing": 0-100,
    "emotional_stability": 0-100,
    "interaction_capacity": 0-100,
    "boredom": 0-100,
    "fishing": 0-100,
    "attention_openness": 0-100,
    "watch_state": "blackout | peek | skim_window | active_watch | engaged",
    "interrupt_level": "ordinary | medium | high",
    "interrupt_reason": "为什么此刻适合这种消息打断等级",
    "sleep": {{"quality": 0-100, "depth": "awake | light_rest | light_sleep | deep_sleep", "summary": "昨晚睡眠概况"}},
    "summary": "一句话概括今天整体状态"
  }},
  "outfit": "穿搭视觉描述(禁止写动作/剧情，只写外表)...",
  "timeline": [
    {{"time": "08:15", "activity": "具体的行为描写，富有沉浸感", "status": "当前情绪/状态词"}},
    {{"time": "09:30", "activity": "...", "status": "..."}}
  ],
  "timeline_audit": {{
    "first_timeline_time": "timeline 第一条的 HH:MM",
    "last_timeline_time": "timeline 最后一条的 HH:MM",
    "coverage_mode": "full_day | target_period | from_current_time | partial_day",
    "start_reason": "normal_day_start | previous_day_continuation | life_decision | target_period | manual_instruction | event_focus | custom",
    "end_reason": "normal_day_end | sleep | early_sleep | rest | low_activity | life_decision | late_night | all_nighter | target_period | manual_instruction | event_focus | custom",
    "covers_full_day": true,
    "closed_loop": true,
    "summary": "一句话说明时间轴覆盖范围、起点和生活决策的关系"
  }},
  "places": [{{"name": "今天出现过的地点", "type": "地点类型", "hint": "可选备注"}}],
  "new_events": [{{"summary": "今天值得沉淀的事件", "people": ["相关人物"], "place": "相关地点", "importance": "normal"}}]
}}

【生成要求】
0. JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}
1. 先做生活决策，再写日程：
- 由我根据时间、天气、昨日记录、周计划、聊天记忆、承诺和用户指令自主决定是否睡觉/熬夜/赖床/出门/换装。
- 素材库只提供灵感，不是硬约束；可以选用，也可以自然自创。
- 不要因为当前时间线索偏早就强制起床或延续昨日穿搭，也不要因为时间线索偏晚就强制睡觉。
- 如果提供了连续体力、睡眠债、偏好或生活事件池，必须把它们当作生活惯性参考；但仍由 life_decision 自主决定今天如何表现。
- life_decision.mood 是心情色彩标签，只写“颜色名·情绪词”，不要写成“元气满满，准备……”这类心情句子；自然语言心情放到 state.mood。
- day_plan.schedule_type 是日程类型标签，只写“拥抱阳光的元气出游”“宅家充电的慵懒一日”这类日程素材；不要写穿搭风格、睡衣风、发型或笼统倾向。
2. state 要求：
{self.config.state_prompt}
- mood_score 是心情正向程度；emotional_stability 是情绪稳定，两者不要混同。
- stress 表示主观压力感，busyness 表示日程占用；sleepiness 是实时困倦度，sleep.quality 是睡眠质量。
- outgoing 表示外出意愿，interaction_capacity 表示当前场景下回应、接话、继续交流的意愿与余力。
- boredom 表示低刺激下想找点新鲜内容的倾向；fishing 表示持续低价值刺激后懒得看、懒得理、想退出的倾向。
- attention_openness 表示此刻愿意让外界消息进入主体注意力的开放度。
- watch_state 是群聊观看姿态：blackout=基本不看，peek=偶尔瞥见，skim_window=扫读一小段，active_watch=持续关注，engaged=已经参与。
- interrupt_level 是当前可打断等级：ordinary=普通消息也可自然进入注意，medium=熟悉用户/相关话题/异常热闹才进入，high=只有@、引用、提到我、高风险冲突或强相关事件才进入。
- 这些主观注意力字段由我结合生活决策、日程密度、体力、困意、社交意愿和记忆自主判断，不要套固定时间规则。
- sleep.depth 是今天此刻/该日主状态的休息层级：awake=清醒，light_rest=浅休息，light_sleep=浅睡眠，deep_sleep=深度睡眠。由 life_decision、体力、困意、昨日睡眠债、时间轴和可打断等级共同决定；不要因为出现某个具体时段就机械套用。
3. outfit 要求：
{outfit_rules}
- outfit 必须体现 life_decision.outfit 的决定；如果决定不换装，要写出当前保持的穿搭状态。
- 顶层 outfit 表示当前/目标时刻已经穿在身上的衣服，不是全天最终服装；如果 timeline 里写了未来“回家换上居家服/睡衣/拖鞋/赤脚”等换装节点，顶层 outfit 不能提前写成那套未来服装。
- 如果当前/目标时刻仍在外出、路上、购物、吃饭或约会中，顶层 outfit 必须是适合当下场景和天气的外出状态；只有时间轴已经到达回家、洗澡或睡前节点后，才能写居家服、睡衣、拖鞋或赤脚。
- 如果 timeline/day_plan/life_decision 包含外出、通勤、社交、看展、约会、购物、办事、运动等外出场景，必须先判断穿搭是否适合外出场景和天气；明显居家、睡衣或松散休息状态不能直接用于正式/较长外出。
- life_decision.outfit.decision=keep 只表示当前穿搭本身已经适合接下来的活动；如果从居家/睡眠状态转向外出，通常应使用 partial_change、change 或 outdoor，除非是极短、低要求外出且当前穿搭已可出门。
- 场景与风格池合同：
{DAILY_OUTFIT_SCENE_STYLE_RULES}
- 可以写发型、衣物、配饰和材质；不要写原因解释或日程流水账。
4. timeline 要求 (关键)：
{self.config.timeline_prompt}
- timeline_audit 必须与 timeline 的第一条和最后一条时间一致，并说明本次时间轴是完整全天、目标时段、从当前时刻开始，还是局部记录。
- 正常整日生成默认 coverage_mode=full_day；通常要补齐晚上/睡前/熬夜闭环并把 closed_loop 设为 true；若 life_decision 让时间轴较早收束，必须在 end_reason/summary 解释早睡、休息、低活动、生活决策或用户指令原因。
- 如果完整全天的第一条时间在 14:00 或更晚，不能写 start_reason=normal_day_start；要么补齐上午/中午，要么用 start_reason=life_decision/custom 并在 summary 说明晚起、补觉、低活动、用户指令或当前时刻重生成导致从下午/傍晚展开。
5. 地点与事件要求：
{self.config.world_prompt}
"""
        dynamic_sections = [
            f"目标日期：{date_str}",
            f"当前/目标时间线索：{period_cn}",
            f"当前/目标实际时间：{current_time_text}" if current_time_text else "",
            persona_section.strip() if persona_section else "## 👤 角色设定\n无",
            weather_section.strip(),
            constraint_section.strip(),
            inspiration_section.strip(),
            previous_context.strip(),
            week_section.strip(),
            chat_section.strip(),
            world_section.strip(),
            lifecycle_section.strip(),
            memo_section_text.strip(),
            (
                "## 🚫 需要避免的重复内容\n"
                "以下是最近几天的安排，今天必须有明显差异，不要重复相似的穿搭和活动：\n"
                f"{history_schedules_str}"
            ),
        ]
        dynamic = "\n\n".join(part for part in dynamic_sections if part)
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="今日生活资料")



__all__ = ["DailyDraftMixin"]
