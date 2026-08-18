from ..models import WeekPlanRecord
from ..prompts import (
    CORE_APPEARANCE_PREFERENCE_RULES,
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    cache_friendly_prompt,
)
from .appearance import CURRENT_APPEARANCE_GENERATION_RULES
from .tools import format_text_list
from .wardrobe import (
    OUTFIT_CONTINUITY_RULES,
    OUTFIT_SCENE_CATEGORY_ENUM,
    OUTFIT_STYLE_POOL_ENUM,
)


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
        return f"## 💬 最近聊天参考\n{self.config.chat_prompt}\n{recent_chats}"

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
            "以下只列出最近几天的日程骨架，用于识别重复；不要把它当作今天的素材池，"
            "今天应根据当前天气、状态、承诺和人物关系形成新的活动组合：\n"
            f"{history_schedules_str}"
        )

    def _timeline_prompt_fixed_contract(self, expected_coverage: str) -> str:
        contract_section = self._build_contract_prompt(expected_coverage)
        contract_example = self._contract_json_text(
            self._coverage_contract(expected_coverage)
        ).replace("\n", "\n  ")
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
    "outfit": {{"decision": "keep | change | partial_change | sleepwear | outdoor", "scene_category": "{OUTFIT_SCENE_CATEGORY_ENUM}", "style_pool": "{OUTFIT_STYLE_POOL_ENUM}", "style": "简短的最终穿搭风格", "hair_style": "简短发型名称", "hair": "当前可见的详细发型", "makeup_style": "简短妆容名称", "makeup": "当前实际妆容细节或空字符串", "nails_style": "简短美甲名称", "nails": "当前实际美甲细节或空字符串", "catalog_reference_ids": ["实际采用的视觉衣橱候选编号"], "reason": "为什么这样决定"}},
    "day_plan": {{"schedule_type": "概括今天节奏和活动主题的日程类型标签", "schedule_intent": "home | work | study | social | rest | outing | travel | mixed", "energy_bias": "rest | normal | active", "social_bias": "avoid | light | social"}},
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
  "outfit": "当前实际穿着的详细视觉描述，只写此刻身上的服装、实际鞋袜和已佩戴/携带的必要配饰；放在玄关或为稍后出门准备的鞋包属于外出备选，不写入这里，不混入发型、妆容、美甲、动作或剧情",
  "timeline": [
    {{"time": "08:15", "activity": "具体的行为描写，富有沉浸感", "status": "当前情绪/状态词", "place": "家", "place_kind": "home | poi | generic | transit | online | none", "place_scope": "local | travel", "place_city": "跨城安排的目标城市，否则为空字符串", "place_hint": "同名地点消歧所需的区县、商圈或地址，否则为空字符串", "travel_mode": "walking | cycling | driving | transit 或空字符串"}},
    {{"time": "09:30", "activity": "...", "status": "...", "place": "...", "place_kind": "...", "place_scope": "...", "place_city": "...", "place_hint": "...", "travel_mode": "..."}}
  ],
  "planned_actions": [
    {{
      "action_id": "包含目标日期的唯一动作编号",
      "action_type": "rest | meal | cook | order_food | purchase | move | travel | work | study | chore | exercise | groom | change_outfit | social | chat | photo | video",
      "target": "动作目标；change_outfit、travel、chore、exercise 必须填写明确目标，其余动作按实际需要填写",
      "timeline_index": 0,
      "duration_minutes": 30,
      "preconditions": [{{"field": "state.energy", "operator": "gte", "expected": 20}}],
      "effects": [{{"field": "energy", "operation": "add", "value": -5}}],
      "payload": {{"结构化领域参数": "只填写该动作实际需要的数据"}},
      "evidence": "对应的日程节点和生活决策依据",
      "source": "daily_plan"
    }}
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
- 日程安排保持开放：近期地点只是连续性参考，不是固定路线；在休息日、外出意愿较高、周计划/聊天/承诺支持时，可以低频安排周边游、短途旅行或新的本地探索，schedule_intent 可使用 travel。旅行不是每天必须出现的轮换任务；没有可靠目标城市时使用本地游览或泛化场景，不虚构跨城事实。
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
- outfit 必须体现 life_decision.outfit 的决定；顶层 outfit 只表示当前/目标时刻已经穿在身上的衣服，未来换装不能提前覆盖。
{OUTFIT_CONTINUITY_RULES}
- 单纯“回家”不能默认补写换衣；timeline 只有在真实发生换衣动作时才写换装。
- 当前/目标时刻仍在外出、路上、购物、吃饭或约会中时，穿搭必须适合当下场景和天气。
- 当前/目标时刻在家或睡眠/休息状态时，外出鞋、单肩包、雨伞、相机等只作为未来离家节点的外出备选；不得因为下午安排出门就提前写入顶层 outfit 或早晨居家 change_outfit 的 target。
- 保持视觉一致性：outfit 的颜色、材质和配饰要与 life_decision.mood、天气、活动和状态自然协调；style 只写穿搭风格，色彩细节放在顶层 outfit。
- 当前外观描述规则：
{CURRENT_APPEARANCE_GENERATION_RULES}
- 造型偏好使用原则：
{CORE_APPEARANCE_PREFERENCE_RULES}
- 视觉衣橱候选只在当前确实需要新造型时使用；采用后必须忠实沿用候选的服装细节，并把真实使用的编号写入 catalog_reference_ids，未采用写空数组。长期偏好只用于比较候选，不能绕过衣橱另编一套衣服；编号不能出现在可见穿搭正文中。
- 不要把 hair_style 或 hair 重复塞进顶层 outfit；不要写原因解释或日程流水账。
- 清醒整日计划如果从睡眠穿搭开始，应在首个持续家务、学习、工作、运动、社交、拍摄或外出活动前自然安排晨间换装；恢复日、继续睡眠或明确休养可以保持睡眠穿搭。换装必须同时写入 timeline 与 planned_actions，并从视觉衣橱选择日间造型。
4. timeline 要求 (关键)：
{self.config.timeline_prompt}
- 系统会根据 timeline 自动检查时间覆盖，不需要输出额外时间覆盖说明。
- 正常整日生成需要形成从较早生活起点到晚间或睡前收束的自然跨度；目标时段生成只写目标时段。
- 每个节点都必须填写 place_kind。home 表示居住地，poi 表示需要地图确认的具体场所，generic 表示不绑定具体商家的泛化场景，transit 表示途中，online 表示线上空间，none 表示没有地点含义。
- place_kind 为 home 时 place 固定写“家”；为 poi 或 generic 时必须填写 place；为 transit、online 或 none 时不要虚构精确地点。
- 普通本地生活使用 place_scope=local，place_city 留空；明确的出差、旅行、返乡或跨城安排使用 place_scope=travel 并填写 place_city；同城景点游览仍使用 local，不要把“从家前往某个公园”的交通路线误标成 travel。
- travel_mode 表示从上一处可定位地点前往当前地点的交通方式；地点未变化或无法形成实际路线时留空。不要为了补字段制造出行动作。
- 具体店铺、场馆、景点、车站和机场使用 poi；“附近街区”“河边散步区域”“线上群聊”等不应强行绑定随机 POI。
4.1 planned_actions 要求：
- 只为确实需要状态结算的 timeline 节点输出，可为空数组，不要为了填满而制造动作。
- action_type、timeline_index、前置条件和影响必须显式填写；不得要求系统从 activity 文案猜动作。
- timeline 只要明确发生了换装，就必须为对应节点输出 action_type=change_outfit，target 写换装后实际穿搭；不能只在 activity 文案中描述换衣。
- 换鞋、挎上或放下随身包、穿脱外层都会改变当前穿搭组成，同样必须在实际发生的节点输出 change_outfit；不得把下午/晚些时候出门才使用的鞋包提前并入早晨或居家的 change_outfit target。
- 本轮提供视觉衣橱候选时，change_outfit 的 payload.catalog_reference_ids 必须填写该次换装实际采用的衣橱服装编号；如果组合上装和下装，至少同时填写对应的上装与下装编号。系统会用衣橱详细描述校正 target，不能只把衣橱当作灵感后另写一套衣服。
- action_id 在不同日期和节点间必须唯一；effects 只写该动作真实会改变的数值状态。
- payload 只用于明确的领域数据：cook 的 ingredients、purchase 的 items 使用 {{"name":"名称","quantity":1,"unit":"可选单位"}} 数组；只有明确属于家庭食材、会用于后续烹饪的采购项才放入 purchase.payload.pantry_items，格式同上；普通物品、纪念品、家居用品和杂货仍放在 items，不得写入 pantry_items；meal/cook/order_food 可填 meal_type 和 place；move/travel 可填 origin、destination、travel_mode；chore 的 cadence_days 使用非负整数、effort 使用 1-5 整数；exercise 的 intensity 使用 1-5 整数。
- 现有可用食材库存是会变化的生活事实，不得长期只当作背景。若在家自制、现做、加热、调配，或明确使用其中食材，优先生成 cook；从库存中选择实际用到的名称并填写正数 ingredients，系统会按此扣减。不要为了清库存机械安排做饭，也不要虚构库存里没有的食材。
- meal 表示外食、现成餐食或无法确认用料的直接用餐，不校验或扣减家庭库存；cook 表示实际动手烹饪，必须填写至少一项 ingredients，会按库存校验并扣减，同时由系统自动沉淀食谱；order_food 表示点餐或外卖。不要用 meal 代替实际在家烹饪，也不要为 meal/order_food 填写 ingredients 或自行编造 recipe_id。
- 不要从 activity 文案隐含领域参数；没有可靠参数就保留空 payload。
- 做生活决策时独立评估今天是否适合有目的的身体活动：结合体力、身体状态、天气、近期运动负荷、日程密度和角色兴趣自主决定，也可以合理地不安排；不得为了填充生活实况面板机械增加运动。
- 只有节点的主要目的确实是锻炼、舒展身体或运动恢复时才使用 exercise。通勤、普通出行、购物、逛街、游览、社交和拍照过程中的走动仍按各自真实目的记录，不能仅因存在步行或体力消耗就算作运动。
- 安排 exercise 时，timeline 必须有语义一致的明确运动节点，并填写具体 target、合理 duration_minutes 和 payload.intensity；没有对应运动节点时不得单独制造 exercise 动作。
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
        person_fact_context: str,
        replacement_context: str,
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
            person_fact_context.strip(),
            replacement_context.strip(),
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
        person_fact_context: str = "",
        replacement_context: str = "",
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
            person_fact_context=person_fact_context,
            replacement_context=replacement_context,
            current_time_text=current_time_text,
        )
        dynamic = "\n\n".join(part for part in dynamic_sections if part)
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="今日生活资料")


__all__ = ["DailyDraftMixin"]
