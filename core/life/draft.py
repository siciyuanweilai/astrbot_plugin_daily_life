from ..models import WeekPlanRecord
from ..prompts import (
    CORE_APPEARANCE_PREFERENCE_RULES,
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    cache_friendly_prompt,
)
from .wardrobe import (
    DAILY_OUTFIT_SCENE_STYLE_RULES,
    OUTFIT_SCENE_CATEGORY_ENUM,
    OUTFIT_STYLE_POOL_ENUM,
)
from .tools import format_text_list


class DailyDraftMixin:
    @staticmethod
    def _timeline_prompt_persona_section(persona: str) -> str:
        return f"## 👤 角色设定\n{persona}" if persona else "## 👤 角色设定\n无"

    @staticmethod
    def _timeline_prompt_week_section(
        week_plan: WeekPlanRecord | None,
        *,
        today_hint: str = "",
        today_suggested: str = "",
        schedule_intent: str = "",
    ) -> str:
        if not week_plan:
            return ""
        return (
            "## 📅 周计划参考"
            f"\n- 主题：{week_plan.theme or '常规周'}"
            f"\n- 目标：{format_text_list(week_plan.goals, default='无')}"
            f"\n- 今日提示：{today_hint or '按周主题安排'}"
            f"\n- 建议活动：{today_suggested or '无'}"
            "\n- 使用方式：作为生活连续性的软参考，不是必须照做的清单"
            f"\n- 日程倾向参考：{schedule_intent or '由生活决策决定'}"
        )

    def _timeline_prompt_chat_section(self, recent_chats: str) -> str:
        if not recent_chats or recent_chats == "无":
            return ""
        return (
            "## 💬 最近聊天参考\n"
            f"{self.config.chat_prompt}\n"
            f"{recent_chats}"
        )

    @staticmethod
    def _timeline_prompt_memo_section(memo_str: str) -> str:
        if not memo_str:
            return ""
        return f"## 🔔 强制备忘录/用户指令\n以下是今天必须完成或加入日程的事项：\n{memo_str}"

    @staticmethod
    def _timeline_prompt_repeat_section(history_schedules_str: str) -> str:
        if not history_schedules_str:
            return ""
        return (
            "## 🚫 需要避免的重复内容\n"
            "以下是最近几天的安排骨架；今天要有自然变化点，不要机械复刻相似的穿搭、活动、地点和心情：\n"
            f"{history_schedules_str}"
        )

    def _timeline_prompt_fixed_contract(self, expected_coverage: str) -> str:
        contract_section = self._build_contract_prompt(expected_coverage)
        contract_example = self._contract_json_text(self._coverage_contract(expected_coverage)).replace("\n", "\n  ")
        return f"""生成当前角色的自主生活背景。
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
    "day_plan": {{"schedule_type": "概括今天节奏和活动主题的日程类型标签", "schedule_intent": "home | work | study | social | rest | outing | mixed", "energy_bias": "rest | normal | active", "social_bias": "avoid | light | social"}},
    "theme": "今天自然形成的主题",
    "mood": "心情色彩标签，必须是“颜色名·情绪词”格式"
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
    "physiological_rhythm": {{
      "energy_curve": "今天精力起伏的短句概括",
      "body_condition": {{"label": "身体状态", "intensity": 0-100, "source": "依据来源", "expires_at": "YYYY-MM-DD 或空字符串"}},
      "recovery_actions": ["今天自然采用的恢复动作"],
      "social_battery": 0-100,
      "attention_state": "注意力/感官负荷状态",
      "optional_cycle": {{"enabled": "布尔值，是否存在可选周期", "label": "可选周期标签", "intensity": 0-100, "source": "依据来源"}},
      "summary": "一句话概括今天的生理节律"
    }},
    "summary": "一句话概括今天整体状态"
  }},
  "outfit": "穿搭视觉描述(禁止写动作/剧情，只写外表)...",
  "timeline": [
    {{"time": "08:15", "activity": "具体的行为描写，富有沉浸感", "status": "当前情绪/状态词"}},
    {{"time": "09:30", "activity": "...", "status": "..."}}
  ],
  "decision_summary": {{
    "decision": "一句话概括今天为什么这样过",
    "reason": "结合身体状态、天气、记忆、承诺、短期目标和重复抑制作出的内部判断",
    "continuity": "哪些近期生活惯性被自然延续；没有就写空字符串",
    "novelty": "今天主动做出的变化点；没有就写空字符串",
    "memory_used": ["真正参与判断的记忆或短期修正"],
    "avoid_repeat": ["本次刻意避开的重复主题、地点、穿搭或活动"]
  }},
  "places": [{{"name": "今天出现过的地点", "type": "地点类型", "hint": "可选备注"}}],
  "new_events": [{{"summary": "今天值得沉淀的事件", "people": ["相关人物"], "place": "相关地点", "importance": "normal"}}]
}}

【生成要求】
0. JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}
1. 先做生活决策，再写日程：
- 由我根据时间、天气、昨日记录、周计划、聊天记忆、承诺和用户指令自主决定是否睡觉/熬夜/赖床/出门/换装。
- 近期生活惯性来自历史、记忆和状态沉淀，不是模板或硬约束；可以自然延续，也可以根据今天的新条件主动变化。
- 不要因为当前时间线索偏早就强制起床或延续昨日穿搭，也不要因为时间线索偏晚就强制睡觉。
- 如果提供了连续体力、睡眠债、偏好或生活事件池，必须把它们当作生活惯性参考；但仍由 life_decision 自主决定今天如何表现。
- life_decision.mood 是心情色彩标签，只写“颜色名·情绪词”，不要写成“元气满满，准备……”这类心情句子；自然语言心情放到 state.mood。
- day_plan.schedule_type 是日程类型标签，用短语概括今天的节奏、活动主题或生活重心；不要写穿搭风格、睡衣风、发型或笼统倾向。
- decision_summary 是后台观察用的内部决策摘要，不是给用户看的旁白；只写真实参与判断的依据，不要为了填字段罗列所有资料。
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
- physiological_rhythm 是通用身体节律：包括精力曲线、身体状态、恢复动作、社交电量、注意力状态和可选周期字段。
3. outfit 要求：
- outfit 必须体现 life_decision.outfit 的决定；如果决定不换装，要写出当前保持的穿搭状态。
- 顶层 outfit 表示当前/目标时刻已经穿在身上的衣服，不是全天最终服装；如果 timeline 里写了未来“回家换上居家服/睡衣/拖鞋/赤脚”等换装节点，顶层 outfit 不能提前写成那套未来服装。
- 如果当前/目标时刻仍在外出、路上、购物、吃饭或约会中，顶层 outfit 必须是适合当下场景和天气的外出状态；只有时间轴已经到达回家、洗澡或睡前节点后，才能写居家服、睡衣、拖鞋或赤脚。
- 如果 timeline/day_plan/life_decision 包含外出、通勤、社交、看展、约会、购物、办事、运动等外出场景，必须先判断穿搭是否适合外出场景和天气；明显居家、睡衣或松散休息状态不能直接用于正式/较长外出。
- life_decision.outfit.decision=keep 只表示当前穿搭本身已经适合接下来的活动；如果从居家/睡眠状态转向外出，通常应使用 partial_change、change 或 outdoor，除非是极短、低要求外出且当前穿搭已可出门。
- 保持视觉一致性：outfit 的颜色、材质和配饰要与 life_decision.mood、天气、活动和状态自然协调；style 只写穿搭风格，色彩细节放在顶层 outfit。
- 造型偏好使用原则：
{CORE_APPEARANCE_PREFERENCE_RULES}
- 场景与风格池合同：
{DAILY_OUTFIT_SCENE_STYLE_RULES}
- 可以写发型、衣物、配饰和材质；不要写原因解释或日程流水账。
4. timeline 要求 (关键)：
{self.config.timeline_prompt}
- 系统会根据 timeline 自动检查时间覆盖，不需要输出额外时间覆盖说明。
- 正常整日生成需要形成从较早生活起点到晚间或睡前收束的自然跨度；目标时段生成只写目标时段。
5. 地点与事件要求：
{self.config.world_prompt}
"""

    def _timeline_prompt_dynamic_sections(
        self,
        *,
        date_str: str,
        period_cn: str,
        weather_section: str,
        constraint_section: str,
        inertia_section: str,
        previous_context: str,
        history_schedules_str: str,
        memo_str: str,
        persona: str,
        week_plan: WeekPlanRecord | None,
        today_hint: str,
        today_suggested: str,
        recent_chats: str,
        schedule_intent: str,
        world_context: str,
        lifecycle_context: str,
        autonomy_context: str,
        current_time_text: str,
    ) -> list[str]:
        return [
            self._timeline_prompt_persona_section(persona),
            self._timeline_prompt_week_section(
                week_plan,
                today_hint=today_hint,
                today_suggested=today_suggested,
                schedule_intent=schedule_intent,
            ),
            f"目标日期：{date_str}",
            f"当前/目标时间线索：{period_cn}",
            f"当前/目标实际时间：{current_time_text}" if current_time_text else "",
            weather_section.strip(),
            constraint_section.strip(),
            inertia_section.strip(),
            previous_context.strip(),
            self._timeline_prompt_chat_section(recent_chats),
            world_context.strip(),
            lifecycle_context.strip(),
            autonomy_context.strip(),
            self._timeline_prompt_memo_section(memo_str),
            self._timeline_prompt_repeat_section(history_schedules_str),
        ]

    def _build_timeline_prompt(
        self,
        date_str: str,
        period_cn: str,
        weather_section: str,
        constraint_section: str,
        inertia_section: str,
        previous_context: str,
        history_schedules_str: str,
        memo_str: str,
        persona: str = "",
        week_plan: WeekPlanRecord | None = None,
        today_hint: str = "",
        today_suggested: str = "",
        recent_chats: str = "",
        schedule_intent: str = "",
        world_context: str = "",
        lifecycle_context: str = "",
        autonomy_context: str = "",
        expected_coverage: str = "full_day",
        current_time_text: str = "",
    ) -> str:
        fixed = self._timeline_prompt_fixed_contract(expected_coverage)
        dynamic_sections = self._timeline_prompt_dynamic_sections(
            date_str=date_str,
            period_cn=period_cn,
            weather_section=weather_section,
            constraint_section=constraint_section,
            inertia_section=inertia_section,
            previous_context=previous_context,
            history_schedules_str=history_schedules_str,
            memo_str=memo_str,
            persona=persona,
            week_plan=week_plan,
            today_hint=today_hint,
            today_suggested=today_suggested,
            recent_chats=recent_chats,
            schedule_intent=schedule_intent,
            world_context=world_context,
            lifecycle_context=lifecycle_context,
            autonomy_context=autonomy_context,
            current_time_text=current_time_text,
        )
        dynamic = "\n\n".join(part for part in dynamic_sections if part)
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="今日生活资料")


__all__ = ["DailyDraftMixin"]
