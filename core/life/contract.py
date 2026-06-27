from ..prompts import cache_friendly_prompt
from .style import outfit_style_contamination_reason


GENERATION_CONTRACT_VERSION = "daily_life_v2"
EARLY_FULL_DAY_END_MINUTES = 20 * 60
VALID_TIMELINE_COVERAGE_MODES = {"full_day", "target_period", "from_current_time", "partial_day"}
VALID_TIMELINE_START_REASONS = {
    "normal_day_start",
    "previous_day_continuation",
    "life_decision",
    "target_period",
    "manual_instruction",
    "event_focus",
    "custom",
}
VALID_TIMELINE_END_REASONS = {
    "normal_day_end",
    "sleep",
    "early_sleep",
    "rest",
    "low_activity",
    "life_decision",
    "late_night",
    "all_nighter",
    "target_period",
    "manual_instruction",
    "event_focus",
    "custom",
}
EARLY_FULL_DAY_END_REASONS = {
    "sleep",
    "early_sleep",
    "rest",
    "low_activity",
    "life_decision",
    "manual_instruction",
    "event_focus",
    "custom",
}


class DailyContractMixin:
    def _validate_daily_payload(
        self,
        payload: dict | None,
        manual_extra: str = "",
        expected_coverage: str = "",
        current_minutes: int | None = None,
    ) -> tuple[bool, str]:
        if not payload:
            return False, "未能解析出 JSON 对象"
        if not str(payload.get("outfit", "")).strip():
            return False, "outfit 不能为空"
        style_ok, style_reason = self._validate_outfit_style(payload)
        if not style_ok:
            return False, style_reason
        timeline = payload.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            return False, "timeline 不能为空"
        contract_ok, contract_reason = self._validate_generation_contract(
            payload,
            expected_coverage=expected_coverage,
        )
        if not contract_ok:
            return False, contract_reason
        timeline_ok, timeline_reason = self._validate_timeline_rhythm(
            payload,
            expected_coverage=expected_coverage,
        )
        if not timeline_ok:
            return False, timeline_reason
        if current_minutes is not None:
            from .future import future_outfit_timing_issue

            outfit_issue = future_outfit_timing_issue(payload.get("outfit", ""), timeline, current_minutes)
            if outfit_issue:
                return False, outfit_issue
        return True, ""

    @staticmethod
    def _validate_outfit_style(payload: dict) -> tuple[bool, str]:
        decision = payload.get("life_decision") if isinstance(payload.get("life_decision"), dict) else {}
        outfit = decision.get("outfit") if isinstance(decision.get("outfit"), dict) else {}
        day_plan = decision.get("day_plan") if isinstance(decision.get("day_plan"), dict) else {}
        reason = outfit_style_contamination_reason(
            outfit.get("style"),
            theme=decision.get("theme") or day_plan.get("theme"),
            mood=decision.get("mood"),
            schedule_type=day_plan.get("schedule_type") or day_plan.get("type"),
        )
        return (False, reason) if reason else (True, "")

    def _validate_generation_contract(self, payload: dict, expected_coverage: str = "") -> tuple[bool, str]:
        expected_coverage = str(expected_coverage or "").strip()
        if not expected_coverage:
            return True, ""
        contract = payload.get("generation_contract")
        if not isinstance(contract, dict):
            return False, "缺少 generation_contract，无法确认本次生成契约"
        if str(contract.get("contract_version") or "").strip() != GENERATION_CONTRACT_VERSION:
            return False, f"generation_contract.contract_version 必须为 {GENERATION_CONTRACT_VERSION}"
        if str(contract.get("expected_coverage") or "").strip() != expected_coverage:
            return False, f"generation_contract.expected_coverage 必须为 {expected_coverage}"

        contract_mode = str(contract.get("timeline_audit_coverage_mode") or "").strip()
        if contract_mode != expected_coverage:
            return False, f"generation_contract.timeline_audit_coverage_mode 必须为 {expected_coverage}"
        if expected_coverage == "full_day" and contract.get("closed_loop_required") is not True:
            return False, "完整全天生成契约必须将 generation_contract.closed_loop_required 设为 true"
        if expected_coverage == "target_period" and contract.get("closed_loop_required") is not False:
            return False, "目标时段生成契约必须将 generation_contract.closed_loop_required 设为 false"

        audit = payload.get("timeline_audit")
        if not isinstance(audit, dict):
            return False, "生成契约要求必须填写 timeline_audit"
        if str(audit.get("coverage_mode") or "").strip() != contract_mode:
            return False, "timeline_audit.coverage_mode 必须与 generation_contract.timeline_audit_coverage_mode 一致"
        if expected_coverage == "target_period" and audit.get("covers_full_day") is True:
            return False, "目标时段生成不能将 timeline_audit.covers_full_day 设为 true"
        return True, ""

    @staticmethod
    def _audit_enum_error(scope: str, field: str, value: str, allowed: set[str]) -> str:
        actual = value or "空"
        allowed_text = "、".join(sorted(allowed))
        return f"{scope}：timeline_audit.{field}={actual} 无效，必须为：{allowed_text}"

    @staticmethod
    def _timeline_minutes(value: object) -> int | None:
        raw = str(value or "").strip()
        try:
            hour, minute = raw.split(":", 1)
            hour_int = int(hour)
            minute_int = int(minute)
        except (TypeError, ValueError):
            return None
        if 0 <= hour_int <= 23 and 0 <= minute_int <= 59:
            return hour_int * 60 + minute_int
        return None

    @staticmethod
    def _audit_summary(audit: dict) -> str:
        return " ".join(str(audit.get("summary") or "").split())

    @staticmethod
    def _allows_early_full_day_end(audit: dict, end_reason: str) -> bool:
        return end_reason in EARLY_FULL_DAY_END_REASONS and bool(DailyContractMixin._audit_summary(audit))

    def _validate_timeline_rhythm(self, payload: dict, expected_coverage: str = "") -> tuple[bool, str]:
        timeline = payload.get("timeline")
        timed_items = []
        for item in timeline if isinstance(timeline, list) else []:
            if not isinstance(item, dict):
                continue
            minutes = self._timeline_minutes(item.get("time"))
            if minutes is not None:
                timed_items.append((minutes, item))
        if not timed_items:
            return True, ""

        first_minutes, _ = min(timed_items, key=lambda value: value[0])
        last_minutes, _ = max(timed_items, key=lambda value: value[0])

        audit = payload.get("timeline_audit") if isinstance(payload.get("timeline_audit"), dict) else {}
        audit_first = self._timeline_minutes(audit.get("first_timeline_time"))
        if audit_first is not None and audit_first != first_minutes:
            return False, "timeline_audit.first_timeline_time 与 timeline 第一条时间不一致"
        audit_last = self._timeline_minutes(audit.get("last_timeline_time"))
        if audit_last is not None and audit_last != last_minutes:
            return False, "timeline_audit.last_timeline_time 与 timeline 最后一条时间不一致"

        expected_coverage = str(expected_coverage or "").strip()
        coverage_mode = str(audit.get("coverage_mode") or "").strip()
        start_reason = str(audit.get("start_reason") or "").strip()
        end_reason = str(audit.get("end_reason") or "").strip()

        expected_full_day = expected_coverage == "full_day"
        expected_target_period = expected_coverage == "target_period"
        declared_full_day = coverage_mode == "full_day" or audit.get("covers_full_day") is True

        if expected_coverage and not audit:
            return False, "日程必须填写 timeline_audit，不能只返回无审计时间轴"
        if expected_coverage and audit_first is None:
            return False, "日程必须填写 timeline_audit.first_timeline_time"
        if expected_coverage and audit_last is None:
            return False, "日程必须填写 timeline_audit.last_timeline_time"
        if expected_full_day and audit.get("covers_full_day") is not True:
            return False, "完整全天日程必须将 timeline_audit.covers_full_day 设为 true"
        if expected_target_period and coverage_mode != "target_period":
            return False, "目标时段日程必须将 timeline_audit.coverage_mode 设为 target_period"

        if first_minutes >= 14 * 60:
            if coverage_mode not in VALID_TIMELINE_COVERAGE_MODES:
                return False, self._audit_enum_error(
                    "timeline 起点较晚，但 timeline_audit.coverage_mode/start_reason 缺少有效的结构化说明",
                    "coverage_mode",
                    coverage_mode,
                    VALID_TIMELINE_COVERAGE_MODES,
                )
            if start_reason not in VALID_TIMELINE_START_REASONS:
                return False, self._audit_enum_error(
                    "timeline 起点较晚，但 timeline_audit.coverage_mode/start_reason 缺少有效的结构化说明",
                    "start_reason",
                    start_reason,
                    VALID_TIMELINE_START_REASONS,
                )
            if coverage_mode == "full_day" and start_reason == "normal_day_start":
                return False, "timeline 声明为完整全天，但较晚的第一条时间缺少对应的生活决策说明"

        if expected_full_day or declared_full_day:
            if coverage_mode not in VALID_TIMELINE_COVERAGE_MODES:
                return False, self._audit_enum_error(
                    "完整全天日程缺少有效的 timeline_audit.coverage_mode/start_reason",
                    "coverage_mode",
                    coverage_mode,
                    VALID_TIMELINE_COVERAGE_MODES,
                )
            if start_reason not in VALID_TIMELINE_START_REASONS:
                return False, self._audit_enum_error(
                    "完整全天日程缺少有效的 timeline_audit.coverage_mode/start_reason",
                    "start_reason",
                    start_reason,
                    VALID_TIMELINE_START_REASONS,
                )
            if audit_first is None:
                return False, "完整全天日程必须填写 timeline_audit.first_timeline_time"
            if audit_last is None:
                return False, "完整全天日程必须填写 timeline_audit.last_timeline_time"
            if end_reason not in VALID_TIMELINE_END_REASONS:
                return False, self._audit_enum_error(
                    "完整全天日程缺少有效的 timeline_audit.end_reason",
                    "end_reason",
                    end_reason,
                    VALID_TIMELINE_END_REASONS,
                )
            if audit.get("closed_loop") is not True:
                return False, "完整全天日程必须将 timeline_audit.closed_loop 设为 true"
            if last_minutes < EARLY_FULL_DAY_END_MINUTES and not self._allows_early_full_day_end(audit, end_reason):
                return False, "完整全天日程较早收束时，需要在 timeline_audit.end_reason/summary 说明早睡、休息、低活动、局部指令或生活决策原因"
        return True, ""

    @staticmethod
    def _coverage_contract(expected_coverage: str) -> dict[str, object]:
        expected_coverage = "target_period" if expected_coverage == "target_period" else "full_day"
        return {
            "contract_version": GENERATION_CONTRACT_VERSION,
            "expected_coverage": expected_coverage,
            "timeline_audit_coverage_mode": expected_coverage,
            "closed_loop_required": expected_coverage == "full_day",
        }

    @staticmethod
    def _contract_json_text(contract: dict[str, object]) -> str:
        closed_loop = "true" if contract.get("closed_loop_required") is True else "false"
        return (
            "{\n"
            f'  "contract_version": "{contract["contract_version"]}",\n'
            f'  "expected_coverage": "{contract["expected_coverage"]}",\n'
            f'  "timeline_audit_coverage_mode": "{contract["timeline_audit_coverage_mode"]}",\n'
            f'  "closed_loop_required": {closed_loop}\n'
            "}"
        )

    def _build_contract_prompt(self, expected_coverage: str) -> str:
        contract = self._coverage_contract(expected_coverage)
        if contract["expected_coverage"] == "full_day":
            audit_rule = (
                '- timeline_audit.coverage_mode 必须为 "full_day"，covers_full_day 必须为 true，'
                'start_reason 必须从 "normal_day_start|previous_day_continuation|life_decision|target_period|manual_instruction|event_focus|custom" 中选择，'
                '不要写 day_start、morning_start 这类自造值；end_reason 必须从 "normal_day_end|sleep|early_sleep|rest|low_activity|life_decision|late_night|all_nighter|target_period|manual_instruction|event_focus|custom" 中选择。'
                "closed_loop 必须为 true；通常应写到晚上/睡前/熬夜收尾，若较早收束必须在 end_reason/summary 说明早睡、休息、低活动、生活决策或用户指令原因。"
                "如果 timeline 第一条在 14:00 或更晚，不能再写 start_reason=normal_day_start；要么补齐上午/中午使时间轴真正覆盖全天，"
                "要么保持 coverage_mode=full_day、covers_full_day=true，并使用 start_reason=life_decision/custom，在 summary 说明晚起、补觉、低活动、用户指令或当前时刻重生成导致从下午/傍晚展开。"
            )
            scene = "本次是普通每日生活背景生成，不是局部片段，也不是从当前时刻开始续写。"
        else:
            audit_rule = (
                '- timeline_audit.coverage_mode 必须为 "target_period"，covers_full_day 必须为 false，'
                "只覆盖目标时段，不伪装成完整全天。"
            )
            scene = "本次是目标时段生成，只需要覆盖指定时段。"
        return (
            "\n【生成契约】\n"
            f"{scene}\n"
            "输出 JSON 顶层必须包含 generation_contract，且必须精确使用以下值：\n"
            f"{self._contract_json_text(contract)}\n"
            f"{audit_rule}\n"
            "- 如果无法满足契约，也必须重写到满足契约为止；不要把其它覆盖模式写进本次结果。\n"
        )

    def _build_repair_prompt(
        self,
        bad_text: str,
        reason: str,
        extra: str = "",
        web_inspiration: str = "",
        expected_coverage: str = "",
    ) -> str:
        extra_section = f"用户补充要求（最高优先级）：{extra}" if extra else "用户没有额外补充要求，请优先修复校验原因并保持生活决策自然合理。"
        web_section = (
            f"\n\n联网灵感参考（只作灵感，不是硬性规则）：\n{web_inspiration}"
            if web_inspiration
            else ""
        )
        contract_section = self._build_contract_prompt(expected_coverage) if expected_coverage else ""
        fixed = f"""你之前生成的日程未通过校验，需要重写为更合理的自主生活背景。
{contract_section}

必须遵循：
- 最终可见输出必须直接从 JSON 对象开始；第一个非空字符必须是 {{，最后一个非空字符必须是 }}。
- 不要在 JSON 前后写“我……”内心独白、解释、旁白、校验说明或任何补充文字。
- 如果有用户补充要求，它高于生活决策、素材参考、天气建议和历史日程。
- 不得忽略、替换或弱化用户指定的具体穿搭、场景和活动。
- 如果用户说“不要/别/禁止/避免”，必须避免对应穿搭或活动。
- 只输出 JSON 对象本体，不要 Markdown，不要解释。
- JSON 必须包含 generation_contract、life_decision、state、outfit、timeline、timeline_audit、places、new_events。
- timeline 必须是数组，每项包含 time、activity、status。
- timeline.activity 可以写睡觉、赖床、起床、换装、出门等生活动作；穿搭细节仍主要写入 outfit，避免在 timeline 里堆服装清单。
- 顶层 outfit 表示当前/目标时刻已经穿在身上的衣服，不能提前使用 timeline 里未来回家、洗澡或睡前才会换上的居家服、睡衣、拖鞋、棉袜或赤脚状态。
- timeline_audit 要说明时间轴覆盖范围：完整全天、目标时段、从当前时刻开始，或局部记录；完整全天必须写 first_timeline_time、last_timeline_time、end_reason、closed_loop。
- timeline_audit.start_reason 只能写 normal_day_start、previous_day_continuation、life_decision、target_period、manual_instruction、event_focus、custom；不要写 day_start。
- timeline_audit.end_reason 只能写 normal_day_end、sleep、early_sleep、rest、low_activity、life_decision、late_night、all_nighter、target_period、manual_instruction、event_focus、custom；不要写 day_end。
- 如果 coverage_mode 是 full_day，通常要补齐晚上或睡前/熬夜闭环；若较早收束，必须用 end_reason/summary 说明早睡、休息、低活动、局部指令或生活决策原因。
- 如果 timeline 第一条在 14:00 或更晚，不能写 start_reason=normal_day_start；必须二选一：补齐上午/中午，或用 start_reason=life_decision/custom 并在 summary 说明为什么完整全天从下午/傍晚展开。
- state 是今天整体身体和情绪底色；places 只记录今天实际出现过的地点，new_events 只记录值得未来引用的事件。"""
        dynamic = f"""校验原因：{reason}
{extra_section}{web_section}

你之前的输出（供参考，可能不合规）：
{bad_text}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="日程修复资料")
