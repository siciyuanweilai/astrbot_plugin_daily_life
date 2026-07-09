import datetime
from collections import Counter
from typing import Any

from astrbot.api import logger

from ..models import LifeDecisionRecord
from ..models.coerce import compact_text


def _compact(value: object, limit: int = 120) -> str:
    return compact_text(value, limit)


def _lines(title: str, lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return f"## {title}\n{body}" if body else ""


def _decision_evidence_field(value: object, limit: int = 180) -> str:
    text = _compact(value, limit)
    if not text:
        return ""
    normalized = text.replace("\n", "；").replace(";", "；")
    return "、".join(part.strip() for part in normalized.split("；") if part.strip())


class LifeAutonomyMixin:
    @staticmethod
    def _counter_line(counter: Counter[str], label: str, limit: int = 4) -> str:
        pairs = [(key, count) for key, count in counter.most_common(limit) if key]
        if not pairs:
            return ""
        return f"- {label}：" + "、".join(f"{key}×{count}" for key, count in pairs)

    async def _build_recent_pattern_context(self, date: datetime.datetime) -> str:
        lookback_days = max(int(getattr(self.config, "reference_history_days", 3) or 0), 7)
        lookback_days = min(lookback_days, 14)
        modes: Counter[str] = Counter()
        intents: Counter[str] = Counter()
        styles: Counter[str] = Counter()
        places: Counter[str] = Counter()
        moods: Counter[str] = Counter()
        daily_lines: list[str] = []

        for offset in range(1, lookback_days + 1):
            day_str = (date - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")
            day = await self.archive.get_day(day_str)
            if not day:
                continue
            meta = day.meta or {}
            for key, counter in (
                ("life_mode", modes),
                ("schedule_intent", intents),
                ("outfit_style_pool", styles),
                ("mood", moods),
            ):
                value = _compact(meta.get(key), 60)
                if value:
                    counter[value] += 1
            for place in day.places[:3]:
                name = _compact(getattr(place, "name", ""), 60)
                if name:
                    places[name] += 1

            first = day.timeline[0] if day.timeline else None
            last = day.timeline[-1] if day.timeline else None
            daily_bits = [
                _compact(meta.get("theme"), 48),
                _compact(meta.get("schedule_type") or meta.get("schedule_intent"), 48),
                _compact(meta.get("outfit_style_pool") or meta.get("style"), 48),
            ]
            if first:
                daily_bits.append(f"起点 {first.time} {_compact(first.activity, 36)}")
            if last and last is not first:
                daily_bits.append(f"收束 {last.time} {_compact(last.activity, 36)}")
            body = "；".join(bit for bit in daily_bits if bit)
            if body:
                daily_lines.append(f"- {day_str}：{body}")

        summary_lines = [
            self._counter_line(modes, "近期生活模式"),
            self._counter_line(intents, "近期活动倾向"),
            self._counter_line(styles, "近期穿搭风格池"),
            self._counter_line(moods, "近期心情色彩"),
            self._counter_line(places, "近期常出现地点"),
        ]
        if daily_lines:
            summary_lines.append("近期逐日片段：")
            summary_lines.extend(daily_lines[:8])
        return _lines("🧭 近期生活惯性", [line for line in summary_lines if line])

    async def _build_repeat_guard_context(self, date: datetime.datetime) -> str:
        lines: list[str] = []
        for offset in range(1, 6):
            day_str = (date - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")
            day = await self.archive.get_day(day_str)
            if not day:
                continue
            meta = day.meta or {}
            timeline_bits = [
                _compact(item.activity, 32)
                for item in (day.timeline[:2] + day.timeline[-2:] if len(day.timeline) > 2 else day.timeline)
                if _compact(item.activity, 32)
            ]
            parts = [
                _compact(meta.get("theme"), 48),
                _compact(meta.get("schedule_type"), 48),
                _compact(meta.get("mood"), 48),
                _compact(meta.get("outfit_style_pool") or meta.get("style"), 48),
                _compact(day.outfit, 60),
                " / ".join(dict.fromkeys(timeline_bits)),
            ]
            body = "；".join(part for part in parts if part)
            if body:
                lines.append(f"- {day_str}：{body}")
        if not lines:
            return ""
        return (
            "## 🚫 重复抑制参考\n"
            "下面是最近生活的主题、穿搭、活动和心情骨架。今天可以延续生活逻辑，但要主动给出新的变化点；"
            "只有承诺、天气、身体状态或用户指令需要延续时，才自然保留相同元素。\n"
            + "\n".join(lines)
        )

    async def _build_short_term_life_context(self) -> str:
        sections: list[str] = []
        focus_slots = await self.archive.get_focus_slots(limit=6)
        if focus_slots:
            lines = [
                f"- {item.label or item.focus_key}：优先级 {item.priority}/100；{item.reason or '近期需要留意'}"
                + (f"；到期 {item.expires_at}" if item.expires_at else "")
                for item in focus_slots
            ]
            sections.append(_lines("🎯 短期目标与注意槽", lines))

        corrections = await self.archive.get_memory_corrections(limit=6, unapplied_only=True)
        if corrections:
            lines = [
                f"- {item.target_type or 'memory'}:{item.target_id or 'unknown'}：{item.correction}"
                + (f"（证据：{item.evidence}）" if item.evidence else "")
                for item in corrections
            ]
            sections.append(
                _lines(
                    "🧷 待应用修正",
                    [
                        "以下是用户明确修正或尚未完全吸收的记忆，只影响相关事实判断；不要把它们扩写成永久性格或固定剧情。",
                        *lines,
                    ],
                )
            )
        return "\n\n".join(section for section in sections if section)

    async def _build_emotion_arc_context(self, date: datetime.datetime) -> str:
        getter = getattr(self.archive, "get_emotion_arcs", None)
        if not callable(getter):
            return ""
        arcs = await getter(limit=5, date=date.strftime("%Y-%m-%d"), include_global=True)
        if not arcs:
            arcs = await getter(limit=5, include_global=True)
        lines: list[str] = []
        for item in arcs[:5]:
            label = _compact(getattr(item, "label", ""), 60)
            if not label:
                continue
            parts = [
                f"强度 {getattr(item, 'intensity', 0)}/100",
                f"正负向 {getattr(item, 'valence', 0)}",
                _compact(getattr(item, "evidence", ""), 100),
                _compact(getattr(item, "influence", ""), 100),
            ]
            lines.append(f"- {label}：" + "；".join(part for part in parts if part))
        if not lines:
            return ""
        return _lines(
            "🌫️ 近期情绪脉络",
            [
                "这是短期情绪状态和证据，不是永久人设；用于调整节奏、互动余力和活动强度。",
                *lines,
            ],
        )

    async def _build_recent_life_decision_context(self, limit: int = 8) -> str:
        get_decisions = getattr(self.archive, "get_life_decisions", None)
        if not callable(get_decisions):
            return ""
        decisions = await get_decisions(limit=limit)
        lines = [
            f"- {item.date or item.created_at[:10]}｜{item.kind}｜{item.decision}"
            + (f"；原因：{item.reason}" if item.reason else "")
            for item in decisions
            if item.decision or item.reason
        ]
        return _lines("🧾 近期生活决策记录", lines[:limit])

    async def _build_execution_review_context(self, date: datetime.datetime) -> str:
        previous_date = (date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        previous_day = await self.archive.get_day(previous_date)
        previous_review = await self.archive.get_daily_review(previous_date)
        decisions = await self.archive.get_life_decisions(limit=8, kind="daily_plan")
        previous_decision = next((item for item in decisions if item.date == previous_date), None)
        lines: list[str] = []
        if previous_decision:
            if previous_decision.decision:
                lines.append(f"- 昨日决策：{_compact(previous_decision.decision, 120)}")
            if previous_decision.outcome:
                lines.append(f"- 原安排：{_compact(previous_decision.outcome, 140)}")
        if previous_day and previous_day.timeline:
            first = previous_day.timeline[0]
            last = previous_day.timeline[-1]
            lines.append(
                f"- 实际时间轴：{first.time} {_compact(first.activity, 48)}"
                + (f"；{last.time} {_compact(last.activity, 48)}" if last is not first else "")
            )
        if previous_review and previous_review.summary:
            lines.append(f"- 夜间复盘：{_compact(previous_review.summary, 160)}")
        if not lines:
            return ""
        lines.append("- 今天可以延续真实生活惯性，也可以根据复盘、天气、承诺和状态自然调整；不要机械复刻昨天。")
        return _lines("🧩 执行复盘与今天调整", lines)

    async def _build_autonomous_life_context(self, date: datetime.datetime) -> str:
        sections = [
            await self._build_short_term_life_context(),
            await self._build_emotion_arc_context(date),
            await self._build_recent_life_decision_context(),
            await self._build_execution_review_context(date),
            await self._build_repeat_guard_context(date),
        ]
        return "\n\n".join(section for section in sections if section)

    @staticmethod
    def _focus_slot_used_by_decision(slot, decision_text: str) -> bool:
        combined = _compact(decision_text, 1200)
        if not combined:
            return False
        markers = [
            _compact(getattr(slot, "label", ""), 120),
            _compact(getattr(slot, "focus_key", ""), 120),
        ]
        return any(marker and marker in combined for marker in markers)

    async def _absorb_used_focus_slots(
        self,
        *,
        date: str,
        kind: str,
        decision: str = "",
        reason: str = "",
        evidence: str = "",
        outcome: str = "",
        source_id: str = "",
        focus_scope: str = "",
    ) -> None:
        get_focus_slots = getattr(self.archive, "get_focus_slots", None)
        upsert_focus_slot = getattr(self.archive, "upsert_focus_slot", None)
        save_memory_evidence = getattr(self.archive, "save_memory_evidence", None)
        if not (callable(get_focus_slots) and callable(upsert_focus_slot)):
            return
        decision_text = "；".join(item for item in (decision, reason, evidence, outcome) if item)
        focus_scope = _compact(focus_scope, 180)
        slots = await get_focus_slots(limit=12, scope=focus_scope) if focus_scope else await get_focus_slots(limit=12)
        if not slots:
            return
        from ..models import FocusSlotRecord, MemoryEvidenceRecord

        for slot in slots:
            if not self._focus_slot_used_by_decision(slot, decision_text):
                continue
            next_priority = max(10, int(getattr(slot, "priority", 50) or 50) - 24)
            expires_at = getattr(slot, "expires_at", "") or date
            label = _compact(getattr(slot, "label", "") or getattr(slot, "focus_key", ""), 120)
            summary = _compact(
                f"{label} 已参与 {date} 的{kind or '生活'}决策：{decision or reason or evidence}",
                240,
            )
            saved_slot = await upsert_focus_slot(
                FocusSlotRecord(
                    id=getattr(slot, "id", 0),
                    scope=getattr(slot, "scope", ""),
                    focus_key=getattr(slot, "focus_key", ""),
                    label=getattr(slot, "label", "") or getattr(slot, "focus_key", ""),
                    priority=next_priority,
                    reason=summary,
                    last_active_at=date,
                    expires_at=expires_at,
                )
            )
            if saved_slot and callable(save_memory_evidence):
                await save_memory_evidence(
                    MemoryEvidenceRecord(
                        target_type="focus",
                        target_id=str(getattr(saved_slot, "id", "")),
                        evidence_type="decision",
                        source_table="life_decisions",
                        source_id=source_id,
                        date=date,
                        summary=summary,
                    )
                )

    async def _save_life_decision_record(
        self,
        *,
        kind: str,
        date: str,
        subject: str = "",
        decision: str = "",
        reason: str = "",
        evidence: str = "",
        outcome: str = "",
        confidence: float = 1.0,
        source: str = "autonomous_life",
        focus_scope: str = "",
    ) -> LifeDecisionRecord | None:
        saver = getattr(self.archive, "save_life_decision", None)
        if not callable(saver):
            return None
        try:
            saved = await saver(
                LifeDecisionRecord(
                    date=date,
                    kind=kind,
                    subject=subject,
                    decision=decision,
                    reason=reason,
                    evidence=evidence,
                    outcome=outcome,
                    confidence=confidence,
                    source=source,
                    )
            )
            await self._absorb_used_focus_slots(
                date=date,
                kind=kind,
                decision=decision,
                reason=reason,
                evidence=evidence,
                outcome=outcome,
                source_id=str(getattr(saved, "id", "")) if saved else "",
                focus_scope=focus_scope,
            )
            evidence_saver = getattr(self.archive, "save_memory_evidence", None)
            if saved and callable(evidence_saver):
                from ..models import MemoryEvidenceRecord

                summary_fields = [
                    ("原因", _decision_evidence_field(reason, 160)),
                    ("依据", _decision_evidence_field(evidence, 160)),
                    ("结果", _decision_evidence_field(outcome, 120)),
                ]
                summary = _compact("；".join(f"{label}：{value}" for label, value in summary_fields if value), 360)
                if summary:
                    await evidence_saver(
                        MemoryEvidenceRecord(
                            target_type="life_decision",
                            target_id=str(saved.id),
                            evidence_type="decision",
                            source_table="life_decisions",
                            source_id=str(saved.id),
                            date=date,
                            summary=summary,
                            confidence=confidence,
                        )
                    )
            return saved
        except Exception as exc:
            logger.debug(f"[日常生活] 生活决策记录写入失败：{exc}")
            return None

    @staticmethod
    def _daily_decision_text(result: dict, day) -> tuple[str, str, str]:
        decision = result.get("life_decision") if isinstance(result.get("life_decision"), dict) else {}
        plan = decision.get("day_plan") if isinstance(decision.get("day_plan"), dict) else {}
        outfit = decision.get("outfit") if isinstance(decision.get("outfit"), dict) else {}
        summary = result.get("decision_summary") if isinstance(result.get("decision_summary"), dict) else {}
        decision_text = _compact(
            summary.get("decision")
            or f"{decision.get('theme') or plan.get('schedule_type') or '自主生活'}；"
            f"{plan.get('schedule_intent') or decision.get('life_mode') or ''}；"
            f"{outfit.get('decision') or ''}",
            300,
        )
        reason = _compact(
            summary.get("reason")
            or summary.get("continuity")
            or outfit.get("reason")
            or getattr(getattr(day, "state", None), "summary", ""),
            360,
        )
        evidence_parts = [
            _compact(item, 180)
            for item in (
                summary.get("memory_used"),
                summary.get("avoid_repeat"),
            )
        ]
        evidence = _compact("；".join(item for item in evidence_parts if item), 500)
        return decision_text, reason, evidence

    @staticmethod
    def _daily_decision_outcome(result: dict) -> str:
        summary = result.get("decision_summary") if isinstance(result.get("decision_summary"), dict) else {}
        for key in ("outcome", "result", "novelty"):
            text = _compact(summary.get(key), 180)
            if text:
                return text
        return ""
