import datetime
import json
import uuid

from astrbot.api import logger

from ..models import CommitmentRecord, LifeState, PlaceRecord, TimelineItem
from ..prompts import (
    CORE_AUTONOMY_RULES,
    CORE_JSON_OUTPUT_RULES,
    CORE_STATE_BEHAVIOR_RULES,
    LIFE_PREFERENCE_CATEGORY_ENUM,
    cache_friendly_prompt,
)
from .condition import format_state_prompt
from .people import INVITE_PERSON_TEXT_PATHS
from .tools import extract_json_from_text


class InviteMixin:
    @staticmethod
    def _serialized_current_places(current_places: list | None) -> list[dict]:
        """序列化当天已有地点，避免地图校正跳过时丢失记录。"""

        return [
            place.as_dict()
            for place in (
                PlaceRecord.from_value(value) for value in current_places or []
            )
            if place is not None
        ]

    @staticmethod
    def _reusable_location_candidates(current_places: list | None) -> list[dict]:
        """把当天已确认的地点转换为地图审计可复用的候选项。"""

        candidates: list[dict] = []
        for value in current_places or []:
            place = PlaceRecord.from_value(value)
            if (
                place is None
                or place.latitude is None
                or place.longitude is None
                or place.type == "home"
                or place.name == "家"
            ):
                continue
            candidates.append(
                {
                    "name": place.name,
                    "address": place.hint,
                    "place_hint": place.hint,
                    "category": place.type,
                    "coordinate": (float(place.latitude), float(place.longitude)),
                }
            )
        return candidates

    @staticmethod
    def _split_timeline_at(
        current_timeline: list,
        current_time: datetime.datetime,
    ) -> tuple[list[TimelineItem], list[TimelineItem]]:
        """按当前时间拆分已经发生和尚未发生的时间轴。

        Args:
            current_timeline: 当天完整时间轴。
            current_time: 用于划分时间轴的当前时间。

        Returns:
            已发生节点和未来节点组成的二元组。
        """

        now_mins = current_time.hour * 60 + current_time.minute
        past_timeline: list[TimelineItem] = []
        future_timeline: list[TimelineItem] = []
        for item in current_timeline:
            timeline_item = TimelineItem.from_value(item)
            try:
                hour, minute = map(int, timeline_item.time.split(":"))
                if hour * 60 + minute <= now_mins:
                    past_timeline.append(timeline_item)
                else:
                    future_timeline.append(timeline_item)
            except (TypeError, ValueError):
                future_timeline.append(timeline_item)
        return past_timeline, future_timeline

    @staticmethod
    def _timeline_minutes(value: str) -> int | None:
        try:
            hour, minute = map(int, str(value or "").strip().split(":"))
        except (TypeError, ValueError):
            return None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return hour * 60 + minute

    @staticmethod
    def _copy_timeline_item(value: TimelineItem) -> TimelineItem:
        return TimelineItem.from_value(TimelineItem.from_value(value).as_dict())

    @classmethod
    def _legacy_timeline_edits(
        cls,
        current_future: list[TimelineItem],
        raw_future: list,
    ) -> list[dict]:
        """把旧版完整时间轴结果收敛为最小编辑集合。"""

        candidates: list[TimelineItem] = []
        for raw in raw_future:
            item = TimelineItem.from_value(raw)
            if cls._timeline_minutes(item.time) is None or not item.activity:
                continue
            candidates.append(item)
        if not candidates:
            return []
        originals = {item.time: item for item in current_future}
        candidate_by_time = {item.time: item for item in candidates}
        candidate_minutes = [cls._timeline_minutes(item.time) for item in candidates]
        valid_minutes = [value for value in candidate_minutes if value is not None]
        lower = min(valid_minutes)
        upper = max(valid_minutes)
        edits: list[dict] = []
        for original in current_future:
            candidate = candidate_by_time.get(original.time)
            if candidate is None:
                minute = cls._timeline_minutes(original.time)
                if minute is not None and lower <= minute <= upper:
                    edits.append({"operation": "remove", "target_time": original.time})
                continue
            material_change = (
                candidate.activity != original.activity
                or candidate.status != original.status
                or (candidate.place and candidate.place != original.place)
                or (
                    candidate.travel_mode
                    and candidate.travel_mode != original.travel_mode
                )
            )
            if material_change:
                edits.append(
                    {
                        "operation": "replace",
                        "target_time": original.time,
                        "item": candidate.as_dict(),
                    }
                )
        for candidate in candidates:
            if candidate.time not in originals:
                edits.append(
                    {
                        "operation": "insert",
                        "target_time": "",
                        "item": candidate.as_dict(),
                    }
                )
        return edits

    @classmethod
    def _apply_timeline_edits(
        cls,
        current_future: list[TimelineItem],
        raw_edits: list,
    ) -> tuple[list[TimelineItem] | None, list[TimelineItem], str]:
        """把模型编辑应用到未来时间轴，并返回必须保持原样的节点。"""

        if not isinstance(raw_edits, list) or not raw_edits:
            return None, [], "模型没有返回可用的时间轴编辑"
        original_items = [cls._copy_timeline_item(item) for item in current_future]
        working = [cls._copy_timeline_item(item) for item in current_future]
        original_times = {item.time for item in original_items}
        targeted_times: set[str] = set()
        applied = 0
        for raw in raw_edits[:24]:
            if not isinstance(raw, dict):
                continue
            operation = str(raw.get("operation") or "").strip().lower()
            target_time = str(raw.get("target_time") or "").strip()
            if operation == "remove":
                if target_time not in original_times or target_time in targeted_times:
                    continue
                targeted_times.add(target_time)
                working = [item for item in working if item.time != target_time]
                applied += 1
                continue
            if operation == "replace":
                if target_time not in original_times or target_time in targeted_times:
                    continue
                item = TimelineItem.from_value(raw.get("item"))
                if cls._timeline_minutes(item.time) is None or not item.activity:
                    continue
                targeted_times.add(target_time)
                working = [value for value in working if value.time != target_time]
                working.append(item)
                applied += 1
                continue
            if operation != "insert":
                continue
            item = TimelineItem.from_value(raw.get("item"))
            if cls._timeline_minutes(item.time) is None or not item.activity:
                continue
            working.append(item)
            applied += 1
        if not applied:
            return None, [], "模型返回的时间轴编辑均无效"
        seen_times: set[str] = set()
        for item in working:
            if item.time in seen_times:
                return None, [], f"时间轴编辑产生重复时间：{item.time}"
            seen_times.add(item.time)
        working.sort(key=lambda item: cls._timeline_minutes(item.time) or 0)
        protected = [
            cls._copy_timeline_item(item)
            for item in original_items
            if item.time not in targeted_times
        ]
        return working, protected, ""

    @classmethod
    def _restore_protected_timeline(
        cls,
        timeline: list[TimelineItem],
        protected: list[TimelineItem],
    ) -> list[TimelineItem]:
        """地图审计后恢复未参与编辑的原节点及其完整结构化字段。"""

        protected_by_time = {item.time: item for item in protected}
        restored: list[TimelineItem] = []
        seen_times: set[str] = set()
        for value in timeline:
            item = TimelineItem.from_value(value)
            if item.time in protected_by_time:
                item = cls._copy_timeline_item(protected_by_time[item.time])
            if item.time in seen_times:
                continue
            seen_times.add(item.time)
            restored.append(item)
        for time, item in protected_by_time.items():
            if time not in seen_times:
                restored.append(cls._copy_timeline_item(item))
        restored.sort(key=lambda item: cls._timeline_minutes(item.time) or 0)
        return restored

    async def handle_invite(
        self,
        date_str,
        current_timeline: list,
        invite_text: str,
        current_time: datetime.datetime,
        user_name: str = "用户",
        current_state: LifeState | None = None,
        current_places: list | None = None,
    ):
        past_timeline, future_timeline = self._split_timeline_at(
            current_timeline, current_time
        )

        persona = await self._get_persona()
        person_facts = await self._build_person_fact_context(
            persona=persona,
            explicit_instruction=invite_text,
        )
        autonomy_context = await self._build_autonomous_life_context(current_time)
        fixed = f"""对方向我提出了共同活动或陪伴请求。我要结合自己的真实意愿、当前状态、已经确认的安排和双方关系，决定自然答应、换个时间，还是拒绝。

通用自主原则：
{CORE_AUTONOMY_RULES}

通用状态行为原则：
{CORE_STATE_BEHAVIOR_RULES}

裁定要求：
1. 严格符合我的【性格设定】，结合真实意愿、原计划的重要程度和当前时间，决定是否接受邀约。
   - 如果体力低、社交意愿低或睡眠质量差，可以更自然地拒绝或改为低负担安排。
   - 如果心情放松、社交意愿高且忙碌度不高，可以更愿意接受。
2. 简短地给出我决定接受、拒绝或改约的【内心真实理由】（reason）。注意：不要写成直接回复的台词；写成我的主观理由或现实顾虑。
3. 如果接受，只返回 timeline_edits，不得重写完整未来时间轴。未受邀约影响的节点禁止出现在编辑列表中。
   - replace：target_time 必须是原计划中需要替换的准确时间，并在 item 中给出完整新节点。
   - remove：target_time 必须是原计划中需要删除的准确时间，item 留空。
   - insert：target_time 留空，在 item 中给出需要新增的完整节点。
   - 同一段出行需要同步调整准备、出发、同行活动和返程时，应分别列出必要编辑；无关的用餐、休息和晚间安排保持原样。
   - item.activity 要自然写清楚和邀请者一起做什么，并填写结构化地点。
   - place_kind 只能是 home、poi、generic、transit、online 或 none。
   - 普通本地活动使用 place_scope=local；明确跨城活动使用 place_scope=travel 并填写 place_city。
   - 从上一处可定位地点移动到当前地点时填写 travel_mode；地点未变化时留空。
4. 如果不接受但愿意改约，请给出 alternative_time；如果完全不想去则留空。
5. 允许输出 preference_points 和 life_events，但只能基于当前邀约和状态，不要编造。

严格返回 JSON：
{{
  "decision": "accept | reject | propose_alternative",
  "accept": true/false,
  "reason": "我的内心理由/现实顾虑（千万不要写成对白）",
  "response_stance": "最终回复应表达的态度，例如开心答应、温和改约、自然拒绝",
  "response_tone": "符合关系和当下状态的简短语气描述，不写最终台词",
  "alternative_time": "可选改约时间或空字符串",
  "impact": "这次邀约对今日状态、社交意愿或后续日程的影响",
  "timeline_edits": [{{"operation": "replace | remove | insert", "target_time": "被替换/删除节点的 HH:MM，insert 时为空", "item": {{"time": "HH:MM", "activity": "...", "status": "...", "place": "地点或空字符串", "place_kind": "home | poi | generic | transit | online | none", "place_scope": "local | travel", "place_city": "跨城目标城市或空字符串", "place_hint": "同名地点消歧信息或空字符串", "travel_mode": "walking | cycling | driving | transit 或空字符串"}}}}],
  "preference_points": [{{"category": "{LIFE_PREFERENCE_CATEGORY_ENUM}", "content": "可复用偏好", "weight": 0.1-1.0, "evidence": "依据"}}],
  "life_events": [{{"title": "邀约相关生活事件", "detail": "细节", "effect": "未来影响", "status": "open"}}]
}}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}
"""
        dynamic = f"""我的性格设定：
{persona}

当前时间：{current_time.strftime("%H:%M")}
当前身体和情绪状态：{format_state_prompt(current_state)}
朋友/用户：{user_name}
邀约/打断内容：{invite_text}

我原本接下来的计划：
{json.dumps([item.as_dict() for item in future_timeline], ensure_ascii=False)}

短期目标、修正和近期决策参考：
{autonomy_context or "暂无"}"""
        if person_facts.has_external_people:
            dynamic += "\n\n" + person_facts.format_for_generation()
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="邀约现场")
        session_id = ""
        try:
            provider_id = self._task_provider_id(self.config.invite.provider)
            provider = await self._get_provider(provider_id)
            if not provider:
                return "当前没有可用的 LLM，暂时不想改变计划。", None, {}
            session_id = f"daily_life_invite_{uuid.uuid4().hex[:8]}"
            completion_text = await self._call_llm_text(
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
            )
            result = extract_json_from_text(completion_text)

            if isinstance(result, dict):
                audit = await self._audit_person_payload(
                    result,
                    context=person_facts,
                    patterns=INVITE_PERSON_TEXT_PATHS,
                    provider=provider,
                    provider_id=provider_id,
                    subject="邀约裁定与改排日程",
                )
                if audit.unresolved:
                    logger.warning("[邀约处理] 人物事实存在未解决冲突，保持原日程。")
                    return "人物关系信息暂时没有核对清楚，先不改变计划。", None, {}
                result = audit.payload
                decision = str(result.get("decision") or "").strip()
                accepted = result.get("accept") is True or decision == "accept"
                new_timeline = None
                if accepted:
                    raw_edits = result.get("timeline_edits")
                    if not isinstance(raw_edits, list) or not raw_edits:
                        raw_future = result.get("new_future_timeline")
                        if isinstance(raw_future, list):
                            raw_edits = self._legacy_timeline_edits(
                                future_timeline, raw_future
                            )
                    merged_future, protected_future, edit_issue = (
                        self._apply_timeline_edits(future_timeline, raw_edits)
                    )
                    if merged_future is None:
                        accepted = False
                        decision = "propose_alternative"
                        result["accept"] = False
                        result["decision"] = decision
                        result["reason"] = f"日程调整暂时无法确认：{edit_issue}"
                        result["timeline_issue"] = edit_issue
                    else:
                        candidate_timeline = past_timeline + merged_future
                        protected_timeline = past_timeline + protected_future
                    location_auditor = getattr(
                        getattr(self, "domains", None),
                        "audit_daily_locations",
                        None,
                    )
                    if accepted and callable(location_auditor):
                        audit_kwargs = {"allow_safe_corrections": True}
                        reusable_places = self._reusable_location_candidates(
                            current_places
                        )
                        if reusable_places:
                            audit_kwargs["preselected_places"] = reusable_places
                        audited, location_reason = await location_auditor(
                            {
                                "timeline": [
                                    item.as_dict() for item in candidate_timeline
                                ],
                                "planned_actions": [],
                                "places": self._serialized_current_places(
                                    current_places
                                ),
                            },
                            **audit_kwargs,
                        )
                        if location_reason:
                            accepted = False
                            decision = "propose_alternative"
                            result["accept"] = False
                            result["decision"] = decision
                            result["reason"] = (
                                f"地点安排暂时无法确认：{location_reason}"
                            )
                            result["location_issue"] = location_reason
                        else:
                            new_timeline = self._restore_protected_timeline(
                                [
                                    TimelineItem.from_value(item)
                                    for item in audited.get("timeline", [])
                                ],
                                protected_timeline,
                            )
                            result["_audited_places"] = audited.get("places", [])
                            result["_location_audit"] = audited.get(
                                "location_audit", {}
                            )
                    elif accepted:
                        new_timeline = candidate_timeline
                await self._save_life_decision_record(
                    kind="invite",
                    date=date_str,
                    subject=user_name,
                    decision=decision or ("accept" if accepted else "reject"),
                    reason=str(result.get("reason") or "").strip(),
                    evidence=invite_text,
                    outcome=str(
                        result.get("impact") or result.get("response_stance") or ""
                    ).strip(),
                    source="invite",
                )
                if accepted:
                    return (
                        result.get("reason", "内心觉得提议不错，顺其自然地答应了。"),
                        new_timeline,
                        result,
                    )
                return (
                    result.get("reason", "感觉当前日程安排太紧了，没有精力去。"),
                    None,
                    result,
                )
        except Exception as e:
            logger.error(f"[邀约处理] 处理失败：{e}")
        finally:
            if session_id:
                await self._cleanup_conversation(session_id)
        return "感觉脑子有点乱，目前不想改变计划。", None, {}

    async def reconcile_commitment_with_timeline(
        self,
        date_str: str,
        current_timeline: list,
        commitment: CommitmentRecord,
        current_time: datetime.datetime,
        *,
        owner_hint: str = "",
        current_state: LifeState | None = None,
        current_places: list | None = None,
    ) -> tuple[list[TimelineItem] | None, dict]:
        """判断新承诺是否需要合并到已经生成的当天时间轴。

        Args:
            date_str: 当前生活日日期。
            current_timeline: 已生成的当天时间轴。
            commitment: 刚保存的结构化承诺。
            current_time: 进行协调判断的当前时间。
            owner_hint: 保存入口提供的人物归属判断。
            current_state: 当前角色的实时生活状态。
            current_places: 当天已经由地图确认的地点。

        Returns:
            合并后的完整时间轴和结构化协调结果；无需调整时，时间轴为空。
        """

        past_timeline, future_timeline = self._split_timeline_at(
            current_timeline, current_time
        )
        persona = await self._get_persona()
        person_facts = await self._build_person_fact_context(
            persona=persona,
            explicit_instruction=commitment.content,
        )
        autonomy_context = await self._build_autonomous_life_context(current_time)
        fixed = f"""一条聊天中已经保存的未来约定或承诺刚刚出现。请判断它是否属于当前角色、是否已经确认，以及是否应当修改今天尚未发生的生活安排。

通用自主原则：
{CORE_AUTONOMY_RULES}

通用状态行为原则：
{CORE_STATE_BEHAVIOR_RULES}

裁定要求：
1. 只有当前角色承担或双方共同承担、已经确认且今天仍可执行的安排，才设置 should_apply=true。
2. 说话人自己的单方计划、随口设想、未确认提议、纯偏好或无法确定日期的内容，不得写入当前角色日程。
3. 不得修改已经发生或正在发生的节点；只返回 timeline_edits，不得重写完整未来时间轴。
   - replace：target_time 必须是原计划中需要替换的准确时间，并在 item 中给出完整新节点。
   - remove：target_time 必须是原计划中需要删除的准确时间，item 留空。
   - insert：target_time 留空，在 item 中给出需要新增的完整节点。
   - 未受承诺影响的节点禁止出现在编辑列表中，不要改写无关文案、状态和结构化地点字段。
4. 若承诺包含同行、地点、交通或准备事项，应分别编辑必要的准备、出发、移动、活动和返回节点；不受影响的后续生活保持原样。
5. 若承诺明确包含穿搭要求，输出 outfit_instruction，并给出适合开始换装的 outfit_effective_time；没有明确要求则留空。穿搭要求不能凭空扩写。
6. 地点字段规则与全天日程一致：place_kind 只能是 home、poi、generic、transit、online 或 none；跨城才使用 place_scope=travel；发生移动时填写 travel_mode。

严格返回 JSON：
{{
  "should_apply": true/false,
  "reason": "是否进入当天生活的依据",
  "timeline_edits": [{{"operation": "replace | remove | insert", "target_time": "被替换/删除节点的 HH:MM，insert 时为空", "item": {{"time": "HH:MM", "activity": "...", "status": "...", "place": "地点或空字符串", "place_kind": "home | poi | generic | transit | online | none", "place_scope": "local | travel", "place_city": "跨城目标城市或空字符串", "place_hint": "消歧信息或空字符串", "travel_mode": "walking | cycling | driving | transit 或空字符串"}}}}],
  "outfit_instruction": "承诺中明确确认的穿搭要求或空字符串",
  "outfit_effective_time": "HH:MM 或空字符串",
  "impact": "这项安排对当天生活的实际影响"
}}

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}
"""
        dynamic = f"""我的性格设定：
{persona}

当前日期时间：{current_time.strftime("%Y-%m-%d %H:%M")}
当前身体和情绪状态：{format_state_prompt(current_state)}
承诺记录：{json.dumps(commitment.as_dict(), ensure_ascii=False)}
保存时的归属判断：{owner_hint or "未提供，由证据判断"}

今天尚未发生的原计划：
{json.dumps([item.as_dict() for item in future_timeline], ensure_ascii=False)}

短期目标、修正和近期决策参考：
{autonomy_context or "暂无"}"""
        if person_facts.has_external_people:
            dynamic += "\n\n" + person_facts.format_for_generation()
        prompt = cache_friendly_prompt(fixed, dynamic, dynamic_title="承诺执行协调")
        session_id = ""
        try:
            provider_id = self._task_provider_id(self.config.commitments.provider)
            provider = await self._get_provider(provider_id)
            if not provider:
                return None, {}
            session_id = f"daily_life_commitment_reconcile_{uuid.uuid4().hex[:8]}"
            completion_text = await self._call_llm_text(
                provider,
                prompt,
                session_id,
                primary_provider_id=provider_id,
            )
            result = extract_json_from_text(completion_text)
            if not isinstance(result, dict) or result.get("should_apply") is not True:
                return None, result if isinstance(result, dict) else {}
            audit = await self._audit_person_payload(
                result,
                context=person_facts,
                patterns=INVITE_PERSON_TEXT_PATHS,
                provider=provider,
                provider_id=provider_id,
                subject="当天承诺与日程协调",
            )
            if audit.unresolved:
                logger.warning("[承诺协调] 人物事实存在未解决冲突，保持原日程。")
                return None, {}
            result = audit.payload
            raw_edits = result.get("timeline_edits")
            if not isinstance(raw_edits, list) or not raw_edits:
                raw_future = result.get("new_future_timeline")
                if isinstance(raw_future, list):
                    raw_edits = self._legacy_timeline_edits(future_timeline, raw_future)
            merged_future, protected_future, edit_issue = self._apply_timeline_edits(
                future_timeline, raw_edits
            )
            if merged_future is None:
                result["_retryable"] = True
                result["reconcile_issue"] = edit_issue
                return None, result
            candidate_timeline = past_timeline + merged_future
            protected_timeline = past_timeline + protected_future
            location_auditor = getattr(
                getattr(self, "domains", None),
                "audit_daily_locations",
                None,
            )
            if callable(location_auditor):
                audit_kwargs = {"allow_safe_corrections": True}
                reusable_places = self._reusable_location_candidates(current_places)
                if reusable_places:
                    audit_kwargs["preselected_places"] = reusable_places
                audited, location_reason = await location_auditor(
                    {
                        "timeline": [item.as_dict() for item in candidate_timeline],
                        "planned_actions": [],
                        "places": self._serialized_current_places(current_places),
                    },
                    **audit_kwargs,
                )
                if location_reason:
                    result["reason"] = f"地点安排暂时无法确认：{location_reason}"
                    result["location_issue"] = location_reason
                    result["_retryable"] = True
                    return None, result
                candidate_timeline = self._restore_protected_timeline(
                    [
                        TimelineItem.from_value(item)
                        for item in audited.get("timeline", [])
                    ],
                    protected_timeline,
                )
                result["_audited_places"] = audited.get("places", [])
                result["_location_audit"] = audited.get("location_audit", {})
            await self._save_life_decision_record(
                kind="commitment_reconcile",
                date=date_str,
                subject=str(commitment.id or commitment.content[:80]),
                decision="apply",
                reason=str(result.get("reason") or "").strip(),
                evidence=commitment.content,
                outcome=str(result.get("impact") or "").strip(),
                source="commitment",
            )
            return candidate_timeline, result
        except Exception as exc:
            logger.warning(f"[承诺协调] 处理失败：{exc}")
            return None, {}
        finally:
            if session_id:
                await self._cleanup_conversation(session_id)
