import datetime

from ..prompts import cache_friendly_prompt
from .fashion import outfit_style_contamination_reason


GENERATION_CONTRACT_VERSION = "daily_life_v2"
FULL_DAY_LATEST_START_MINUTES = 13 * 60 + 30
FULL_DAY_EARLIEST_END_MINUTES = 21 * 60
FULL_DAY_MIN_SPAN_MINUTES = 6 * 60
NIGHT_DAY_LATEST_START_MINUTES = 20 * 60
NIGHT_DAY_EARLIEST_END_MINUTES = 23 * 60
DAY_MINUTES = 24 * 60

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
        if expected_coverage:
            self._apply_derived_timeline_audit(payload, expected_coverage)
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
    def _repeat_text(value: object, limit: int = 600) -> str:
        text = "".join(str(value or "").split())
        return text[:limit]

    @staticmethod
    def _repeat_bigrams(text: str) -> set[str]:
        text = DailyContractMixin._repeat_text(text)
        if len(text) < 2:
            return {text} if text else set()
        return {text[index:index + 2] for index in range(len(text) - 1)}

    @classmethod
    def _repeat_similarity(cls, left: object, right: object) -> float:
        left_set = cls._repeat_bigrams(str(left or ""))
        right_set = cls._repeat_bigrams(str(right or ""))
        if not left_set or not right_set:
            return 0.0
        return len(left_set & right_set) / len(left_set | right_set)

    @classmethod
    def _day_repeat_profile(cls, day) -> dict[str, str]:
        meta = getattr(day, "meta", {}) or {}
        timeline = getattr(day, "timeline", []) or []
        timeline_text = " ".join(
            f"{getattr(item, 'activity', '')} {getattr(item, 'status', '')}"
            for item in timeline
        )
        return {
            "theme": cls._repeat_text(meta.get("theme"), 120),
            "schedule_type": cls._repeat_text(meta.get("schedule_type"), 120),
            "schedule_intent": cls._repeat_text(meta.get("schedule_intent"), 80),
            "outfit_style_pool": cls._repeat_text(meta.get("outfit_style_pool") or meta.get("style"), 120),
            "outfit": cls._repeat_text(getattr(day, "outfit", ""), 280),
            "timeline": cls._repeat_text(timeline_text, 600),
        }

    @staticmethod
    def _decision_novelty_text(payload: dict) -> str:
        summary = payload.get("decision_summary") if isinstance(payload.get("decision_summary"), dict) else {}
        novelty = summary.get("novelty")
        if isinstance(novelty, list):
            novelty = "；".join(str(item).strip() for item in novelty if str(item).strip())
        return " ".join(str(novelty or "").split())

    async def _repeat_generation_issue(self, day, date: datetime.datetime, payload: dict, manual_extra: str = "") -> str:
        if str(manual_extra or "").strip():
            return ""
        novelty = self._decision_novelty_text(payload)
        current = self._day_repeat_profile(day)
        if not (current["schedule_type"] or current["outfit"] or current["timeline"]):
            return ""
        for offset in range(1, 4):
            previous_date = (date - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")
            previous_day = await self.archive.get_day(previous_date)
            if not previous_day:
                continue
            previous = self._day_repeat_profile(previous_day)
            same_schedule = current["schedule_type"] and current["schedule_type"] == previous["schedule_type"]
            same_intent = current["schedule_intent"] and current["schedule_intent"] == previous["schedule_intent"]
            same_style_pool = current["outfit_style_pool"] and current["outfit_style_pool"] == previous["outfit_style_pool"]
            outfit_similarity = self._repeat_similarity(current["outfit"], previous["outfit"])
            timeline_similarity = self._repeat_similarity(current["timeline"], previous["timeline"])
            total_similarity = self._repeat_similarity(
                " ".join(current.values()),
                " ".join(previous.values()),
            )
            has_clear_novelty = len(novelty) >= 8
            if same_schedule and outfit_similarity >= 0.92 and timeline_similarity >= 0.72 and not has_clear_novelty:
                return f"生成内容与 {previous_date} 的日程、穿搭和活动过于相似，需要主动给出新的生活变化点"
            if total_similarity >= 0.88 and sum(bool(item) for item in (same_schedule, same_intent, same_style_pool)) >= 2:
                return f"生成内容与 {previous_date} 的近期记录重复度过高，需要重写为自然延续但不机械复刻的生活安排"
        return ""

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

        if expected_coverage == "full_day" and contract.get("closed_loop_required") is not True:
            return False, "完整全天生成契约必须将 generation_contract.closed_loop_required 设为 true"
        if expected_coverage == "target_period" and contract.get("closed_loop_required") is not False:
            return False, "目标时段生成契约必须将 generation_contract.closed_loop_required 设为 false"

        return True, ""

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
    def _minutes_text(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    def _timeline_raw_minutes(self, timeline: object) -> list[int]:
        minutes_list = []
        for item in timeline if isinstance(timeline, list) else []:
            if isinstance(item, dict):
                minutes = self._timeline_minutes(item.get("time"))
            else:
                minutes = self._timeline_minutes(getattr(item, "time", ""))
            if minutes is not None:
                minutes_list.append(minutes)
        return minutes_list

    def _timeline_coverage(self, timeline: object) -> dict[str, int]:
        raw_minutes = self._timeline_raw_minutes(timeline)
        if not raw_minutes:
            return {}

        unwrapped = [raw_minutes[0]]
        offset = 0
        for minutes in raw_minutes[1:]:
            candidate = minutes + offset
            if candidate < unwrapped[-1]:
                offset += DAY_MINUTES
                candidate = minutes + offset
            unwrapped.append(candidate)

        return {
            "first": raw_minutes[0],
            "last": raw_minutes[-1],
            "first_unwrapped": unwrapped[0],
            "last_unwrapped": unwrapped[-1],
            "span": unwrapped[-1] - unwrapped[0],
        }

    def _timeline_bounds(self, timeline: object) -> tuple[int | None, int | None]:
        coverage = self._timeline_coverage(timeline)
        if not coverage:
            return None, None
        return coverage["first"], coverage["last"]

    @staticmethod
    def _full_day_coverage_profile(payload: dict) -> str:
        decision = payload.get("life_decision") if isinstance(payload.get("life_decision"), dict) else {}
        life_mode = str(decision.get("life_mode") or "").strip()
        sleep = decision.get("sleep") if isinstance(decision.get("sleep"), dict) else {}
        sleep_mode = str(sleep.get("mode") or "").strip()
        if life_mode in {"late_night", "all_nighter"} or sleep_mode == "all_nighter":
            return "night_life"
        if sleep_mode == "late_night":
            return "delayed_day"
        return "day"

    @staticmethod
    def _full_day_coverage_thresholds(profile: str) -> tuple[int, int]:
        if profile == "night_life":
            return NIGHT_DAY_LATEST_START_MINUTES, NIGHT_DAY_EARLIEST_END_MINUTES
        if profile == "delayed_day":
            return NIGHT_DAY_LATEST_START_MINUTES, FULL_DAY_EARLIEST_END_MINUTES
        return FULL_DAY_LATEST_START_MINUTES, FULL_DAY_EARLIEST_END_MINUTES

    @classmethod
    def _full_day_coverage_ok(cls, coverage: dict[str, int], profile: str = "day") -> bool:
        if not coverage:
            return False
        first_minutes = coverage["first_unwrapped"]
        last_minutes = coverage["last_unwrapped"]
        span = coverage["span"]
        latest_start, earliest_end = cls._full_day_coverage_thresholds(profile)
        return (
            first_minutes <= latest_start
            and last_minutes >= earliest_end
            and span >= FULL_DAY_MIN_SPAN_MINUTES
        )

    def _derive_timeline_audit(self, payload: dict, expected_coverage: str = "") -> dict[str, object]:
        timeline = payload.get("timeline")
        coverage = self._timeline_coverage(timeline)
        if not coverage:
            return {}
        first_minutes = coverage["first"]
        last_minutes = coverage["last"]

        expected_target = expected_coverage == "target_period"
        if expected_target:
            return {
                "first_timeline_time": self._minutes_text(first_minutes),
                "last_timeline_time": self._minutes_text(last_minutes),
                "coverage_mode": "target_period",
                "start_reason": "target_period",
                "end_reason": "target_period",
                "covers_full_day": False,
                "closed_loop": False,
                "summary": "时间轴覆盖目标时段。",
            }

        profile = self._full_day_coverage_profile(payload)
        covers_full_day = self._full_day_coverage_ok(coverage, profile)
        if first_minutes > FULL_DAY_LATEST_START_MINUTES:
            start_reason = "life_decision"
        else:
            start_reason = "normal_day_start"
        last_unwrapped = coverage["last_unwrapped"]
        if last_unwrapped >= DAY_MINUTES + 4 * 60:
            end_reason = "all_nighter"
        elif last_unwrapped >= 22 * 60:
            end_reason = "sleep"
        elif last_unwrapped >= FULL_DAY_EARLIEST_END_MINUTES:
            end_reason = "normal_day_end"
        else:
            end_reason = "low_activity"

        return {
            "first_timeline_time": self._minutes_text(first_minutes),
            "last_timeline_time": self._minutes_text(last_minutes),
            "coverage_mode": "full_day",
            "start_reason": start_reason,
            "end_reason": end_reason,
            "covers_full_day": covers_full_day,
            "closed_loop": covers_full_day,
            "summary": "时间轴审计由系统根据首尾时间和覆盖跨度生成。",
        }

    def _apply_derived_timeline_audit(self, payload: dict, expected_coverage: str = "") -> dict[str, object]:
        payload.pop("timeline_audit", None)
        audit = self._derive_timeline_audit(payload, expected_coverage)
        if audit:
            payload["timeline_audit"] = audit
        return audit

    def _validate_timeline_rhythm(self, payload: dict, expected_coverage: str = "") -> tuple[bool, str]:
        timeline = payload.get("timeline")
        coverage = self._timeline_coverage(timeline)
        if not coverage:
            return True, ""

        expected_coverage = str(expected_coverage or "").strip()
        if not expected_coverage:
            return True, ""

        if expected_coverage == "full_day":
            profile = self._full_day_coverage_profile(payload)
            first_minutes = coverage["first_unwrapped"]
            last_minutes = coverage["last_unwrapped"]
            span = coverage["span"]
            latest_start, earliest_end = self._full_day_coverage_thresholds(profile)
            if first_minutes > latest_start:
                return False, "完整全天日程的第一条时间过晚，缺少当天较早的生活起点"
            if last_minutes < earliest_end:
                return False, "完整全天日程的最后一条时间过早，缺少晚间或睡前收束"
            if span < FULL_DAY_MIN_SPAN_MINUTES:
                return False, "完整全天日程覆盖范围不足，timeline 仍像局部片段"
        return True, ""

    @staticmethod
    def _coverage_contract(expected_coverage: str) -> dict[str, object]:
        expected_coverage = "target_period" if expected_coverage == "target_period" else "full_day"
        return {
            "contract_version": GENERATION_CONTRACT_VERSION,
            "expected_coverage": expected_coverage,
            "closed_loop_required": expected_coverage == "full_day",
        }

    @staticmethod
    def _contract_json_text(contract: dict[str, object]) -> str:
        closed_loop = "true" if contract.get("closed_loop_required") is True else "false"
        return (
            "{\n"
            f'  "contract_version": "{contract["contract_version"]}",\n'
            f'  "expected_coverage": "{contract["expected_coverage"]}",\n'
            f'  "closed_loop_required": {closed_loop}\n'
            "}"
        )

    def _build_contract_prompt(self, expected_coverage: str) -> str:
        contract = self._coverage_contract(expected_coverage)
        return (
            "\n【生成契约】\n"
            "本次输出只需忠实填写 generation_contract，不要把契约展开成业务规则。\n"
            f"{self._contract_json_text(contract)}\n"
        )

    def _build_repair_prompt(
        self,
        bad_text: str,
        reason: str,
        extra: str = "",
        web_inspiration: str = "",
        expected_coverage: str = "",
    ) -> str:
        extra_section = f"用户补充要求（最高优先级）：{extra}" if extra else "用户没有额外补充要求。"
        web_section = f"\n\n联网灵感参考：\n{web_inspiration}" if web_inspiration else ""
        contract_section = self._build_contract_prompt(expected_coverage) if expected_coverage else ""
        repair_strategy = self._repair_strategy_text(reason, expected_coverage=expected_coverage)
        fixed = f"""你之前生成的日程未通过校验，请直接修复为可通过的 JSON。
{contract_section}

【输出要求】
- 只输出完整 JSON 对象，不要解释、不要 Markdown、不要补充文字。
- 修复方式：{repair_strategy}"""
        dynamic = f"""校验原因：{reason}
{extra_section}{web_section}

原始输出：
{bad_text}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="日程修复")

    @staticmethod
    def _repair_strategy_text(reason: str, expected_coverage: str = "") -> str:
        reason = str(reason or "")
        if expected_coverage == "full_day" and any(
            marker in reason
            for marker in ("第一条时间过晚", "最后一条时间过早", "覆盖范围不足", "较早的生活起点", "睡前收束")
        ):
            return "保留生活决策、状态和穿搭判断，重写 timeline 让它形成完整自然日；系统会根据 timeline 自动检查覆盖范围。"
        return "保留原结果的生活连续性，修复未通过校验的结构或内容；系统会根据 timeline 自动检查覆盖范围。"
