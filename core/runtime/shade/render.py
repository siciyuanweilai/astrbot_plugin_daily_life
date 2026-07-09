from typing import Any, Callable


class HiddenExperienceMixin:
    def _hidden_first(self, item: Any, *names: str, limit: int = 120) -> str:
        for name in names:
            value = self._hidden_text(getattr(item, name, ""), limit)
            if value:
                return value
        return ""

    def _hidden_join(self, values: Any, *, limit: int, count: int) -> str:
        return "；".join(self._hidden_text(text, limit) for text in list(values or [])[:count])

    @staticmethod
    def _hidden_lines(items: list[Any] | None, count: int, render: Callable[[Any], str]) -> list[str]:
        return [line for item in list(items or [])[:count] if (line := render(item))]

    @staticmethod
    def _hidden_section(sections: list[str], title: str, lines: list[str], intro: str = "") -> None:
        if lines:
            body = "\n".join(lines)
            sections.append(f"{title}\n{intro}{body}" if intro else f"{title}\n{body}")

    def _hidden_episode_line(self, item: Any) -> str:
        title = self._hidden_first(item, "title", limit=60)
        body = self._hidden_first(item, "correction", "summary", "impact", limit=120)
        return f"- {title or '生活片段'}：{body or '无摘要'}" if title or body else ""

    def _hidden_focus_target_line(self, item: Any) -> str:
        label = self._hidden_first(item, "label", "target_id", limit=60)
        if not label:
            return ""
        reason = self._hidden_first(item, "reason", limit=100)
        priority = self._hidden_first(item, "priority", limit=8)
        return f"- {label}: 优先级{priority or 0}; {reason or '近期自然多留意'}"

    def _hidden_behavior_feedback_line(self, item: Any) -> str:
        action = self._hidden_first(item, "action", limit=50)
        result = self._hidden_first(item, "feedback", "result", limit=100)
        if not (action or result):
            return ""
        score = self._hidden_first(item, "score", limit=8)
        return f"- {action or '行为'}: {result or '无反馈'}; 分值{score or 0}"

    def _hidden_emotion_line(self, item: Any) -> str:
        label = self._hidden_first(item, "label", limit=60)
        if not label:
            return ""
        evidence = self._hidden_first(item, "evidence", limit=90)
        influence = self._hidden_first(item, "influence", limit=90)
        intensity = self._hidden_first(item, "intensity", limit=8)
        valence = self._hidden_first(item, "valence", limit=8)
        body = "；".join(
            part
            for part in (
                f"强度{intensity or 0}/100",
                f"正负向{valence or 0}",
                evidence,
                influence,
            )
            if part
        )
        return f"- {label}: {body}"

    def _hidden_rhythm_line(self, item: Any) -> str:
        lifecycle = self._hidden_first(item, "lifecycle_kind", limit=30)
        if lifecycle == "transient":
            return ""
        label = self._hidden_first(item, "body_label", "summary", limit=70)
        energy = self._hidden_first(item, "energy_curve", limit=90)
        attention = self._hidden_first(item, "attention_state", limit=70)
        social = self._hidden_first(item, "social_battery", limit=8)
        body = "；".join(
            part
            for part in (
                energy,
                label,
                attention,
                f"社交电量{social}/100" if social else "",
                lifecycle,
            )
            if part
        )
        return f"- {self._hidden_first(item, 'date', limit=20) or '近期'}：{body}" if body else ""

    def _hidden_reply_effect_line(self, item: Any) -> str:
        text = self._hidden_first(item, "reply_text", limit=70)
        evidence = self._hidden_first(item, "evidence", "reason", limit=100)
        if not (text or evidence):
            return ""
        outcome = self._hidden_first(item, "outcome", limit=30)
        return f"- {text or '闲时回应'}: {outcome or '待观察'}；{evidence or '无补充'}"

    def _hidden_memory_correction_line(self, item: Any) -> str:
        target = self._hidden_first(item, "target_id", limit=60)
        correction = self._hidden_first(item, "correction", limit=120)
        return f"- {target}: {correction}" if target and correction else ""

    def _hidden_expression_profile_line(self, item: Any) -> str:
        label = self._hidden_first(item, "label", "scope", limit=60)
        tone = self._hidden_first(item, "tone", limit=100)
        habits = self._hidden_join(getattr(item, "habits", []), limit=60, count=3)
        return f"- {label}: {tone or '表达习惯'}；{habits}" if label and (tone or habits) else ""

    def _hidden_expression_review_line(self, item: Any) -> str:
        passed = "通过" if bool(getattr(item, "passed", True)) else "不宜发送"
        risk = self._hidden_first(item, "risk", limit=60)
        suggestion = self._hidden_first(item, "suggestion", "reason", limit=100)
        if not (risk or suggestion):
            return ""
        return f"- 表达审核{passed}: {risk or suggestion}" + (f"；建议 {suggestion}" if risk and suggestion else "")

    def _hidden_behavior_pattern_line(self, item: Any) -> str:
        scene = self._hidden_first(item, "scene", limit=60)
        pattern = self._hidden_first(item, "pattern", limit=120)
        if not (scene and pattern):
            return ""
        action = self._hidden_first(item, "suggested_action", limit=40)
        return f"- {scene}: {pattern}" + (f"；倾向 {action}" if action else "")

    def _hidden_behavior_scene_line(self, item: Any) -> str:
        scene = self._hidden_first(item, "scene", limit=60)
        if not scene:
            return ""
        cues = self._hidden_join(getattr(item, "cues", []), limit=50, count=3)
        action = self._hidden_first(item, "preferred_action", limit=40)
        avoid = self._hidden_first(item, "avoid_action", limit=50)
        return f"- {scene}: {cues or '语义场景'}；倾向{action or '观察'}" + (f"；避免{avoid}" if avoid else "")

    def _hidden_focus_slot_line(self, item: Any) -> str:
        label = self._hidden_first(item, "label", "focus_key", limit=60)
        if not label:
            return ""
        reason = self._hidden_first(item, "reason", limit=100)
        priority = self._hidden_first(item, "priority", limit=8)
        return f"- {label}: 注意槽{priority or 0}/100；{reason or '短期仍会想起'}"

    def _hidden_mid_summary_line(self, item: Any) -> str:
        label = self._hidden_first(item, "scope_label", "session_id", limit=60)
        body = "；".join(
            part
            for part in (
                self._hidden_first(item, "topic", limit=70),
                self._hidden_first(item, "mood", limit=70),
                self._hidden_first(item, "summary", limit=140),
            )
            if part
        )
        return f"- {label}: {body}" if label and body else ""

    def _hidden_temporary_expression_line(self, item: Any) -> str:
        label = self._hidden_first(item, "label", limit=60)
        expires_at = self._hidden_first(item, "expires_at", limit=40)
        body = "；".join(
            part
            for part in (
                self._hidden_first(item, "tone", limit=100),
                self._hidden_first(item, "reason", limit=120),
                f"强度{self._hidden_first(item, 'intensity', limit=8) or 0}/100",
                f"到期{expires_at}" if expires_at else "",
            )
            if part
        )
        return f"- {label}: {body}" if label and body else ""

    def _hidden_expression_intent_line(self, item: Any) -> str:
        emotion = self._hidden_first(item, "emotion", limit=50)
        category = self._hidden_first(item, "emotion_category", limit=20)
        emoji = self._hidden_first(item, "emoji_intent", limit=50)
        action = self._hidden_first(item, "action_intent", limit=80)
        return (
            f"- 情绪:{emotion or '无'}；分类:{category or '无'}；表情:{emoji or '无'}；动作:{action or '无'}"
            if emotion or category or emoji or action
            else ""
        )

    def _hidden_term_line(self, item: Any) -> str:
        term = self._hidden_first(item, "term", limit=40)
        meaning = self._hidden_first(item, "meaning", limit=100)
        if not (term and meaning):
            return ""
        detail = "；".join(
            part
            for part in (
                meaning,
                self._hidden_first(item, "scene", limit=80),
                f"熟悉度{self._hidden_first(item, 'familiarity', limit=8) or 0}/100",
                self._hidden_join(getattr(item, "examples", []), limit=60, count=2),
            )
            if part
        )
        return f"- {term}: {detail}"

    def _hidden_memory_boundary_line(self, item: Any) -> str:
        source = self._hidden_first(item, "source_scope", limit=60)
        target = self._hidden_first(item, "target_scope", limit=60)
        if not (source and target):
            return ""
        policy = self._hidden_first(item, "policy", limit=20)
        reason = self._hidden_first(item, "reason", limit=100)
        return f"- {source} -> {target}: {policy or 'ask'}; {reason or '谨慎判断'}"

    def _hidden_life_sections(
        self,
        sections: list[str],
        *,
        episodes: list[Any] | None = None,
        focus_targets: list[Any] | None = None,
    ) -> None:
        self._hidden_section(sections, "[HiddenLifeEpisodes]", self._hidden_lines(episodes, 3, self._hidden_episode_line))
        self._hidden_section(sections, "[HiddenFocusTargets]", self._hidden_lines(focus_targets, 4, self._hidden_focus_target_line))

    def _hidden_behavior_sections(
        self,
        sections: list[str],
        *,
        feedback: list[Any] | None = None,
        behavior_patterns: list[Any] | None = None,
        behavior_scenes: list[Any] | None = None,
    ) -> None:
        self._hidden_section(sections, "[HiddenBehaviorFeedback]", self._hidden_lines(feedback, 3, self._hidden_behavior_feedback_line))
        self._hidden_section(sections, "[HiddenBehaviorPatterns]", self._hidden_lines(behavior_patterns, 4, self._hidden_behavior_pattern_line))
        self._hidden_section(sections, "[HiddenBehaviorScenes]", self._hidden_lines(behavior_scenes, 4, self._hidden_behavior_scene_line))

    def _hidden_emotion_sections(self, sections: list[str], *, emotion_arcs: list[Any] | None = None) -> None:
        self._hidden_section(
            sections,
            "[HiddenEmotionArc]",
            self._hidden_lines(emotion_arcs, 4, self._hidden_emotion_line),
            "短期情绪脉络只用于判断回应节奏、活动强度和生活连续性，不要暴露后台字段。\n",
        )

    def _hidden_rhythm_sections(
        self,
        sections: list[str],
        *,
        physiological_rhythm_logs: list[Any] | None = None,
        physiological_rhythm_trend: dict[str, Any] | None = None,
    ) -> None:
        rhythm_intro = ""
        if isinstance(physiological_rhythm_trend, dict):
            summary = self._hidden_text(physiological_rhythm_trend.get("summary", ""), 180)
            if summary:
                rhythm_intro = f"{summary}\n"
        self._hidden_section(
            sections,
            "[HiddenPhysiologicalRhythm]",
            self._hidden_lines(physiological_rhythm_logs, 2, self._hidden_rhythm_line),
            rhythm_intro,
        )

    def _hidden_expression_sections(
        self,
        sections: list[str],
        *,
        reply_effects: list[Any] | None = None,
        expression_profiles: list[Any] | None = None,
        expression_reviews: list[Any] | None = None,
        temporary_expression_states: list[Any] | None = None,
        expression_intents: list[Any] | None = None,
    ) -> None:
        self._hidden_section(sections, "[HiddenReplyEffects]", self._hidden_lines(reply_effects, 4, self._hidden_reply_effect_line))
        self._hidden_section(sections, "[HiddenExpressionHabits]", self._hidden_lines(expression_profiles, 4, self._hidden_expression_profile_line))
        self._hidden_section(sections, "[HiddenExpressionReviews]", self._hidden_lines(expression_reviews, 3, self._hidden_expression_review_line))
        self._hidden_section(
            sections,
            "[HiddenTemporaryExpressionState]",
            self._hidden_lines(temporary_expression_states, 3, self._hidden_temporary_expression_line),
        )
        self._hidden_section(sections, "[HiddenExpressionIntents]", self._hidden_lines(expression_intents, 3, self._hidden_expression_intent_line))

    def _hidden_memory_sections(
        self,
        sections: list[str],
        *,
        memory_corrections: list[Any] | None = None,
        focus_slots: list[Any] | None = None,
        mid_summaries: list[Any] | None = None,
        terms: list[Any] | None = None,
        boundaries: list[Any] | None = None,
    ) -> None:
        self._hidden_section(sections, "[HiddenMemoryCorrections]", self._hidden_lines(memory_corrections, 3, self._hidden_memory_correction_line))
        self._hidden_section(sections, "[HiddenFocusSlots]", self._hidden_lines(focus_slots, 4, self._hidden_focus_slot_line))
        self._hidden_section(sections, "[HiddenMidConversationSummary]", self._hidden_lines(mid_summaries, 3, self._hidden_mid_summary_line))
        self._hidden_section(sections, "[HiddenSceneTerms]", self._hidden_lines(terms, 6, self._hidden_term_line))
        self._hidden_section(
            sections,
            "[HiddenMemoryBoundaries]",
            self._hidden_lines(boundaries, 4, self._hidden_memory_boundary_line),
            "deny 表示不跨域引用；ask 表示只在用户明确引导或上下文必要时谨慎使用。\n",
        )

    def _format_hidden_experience_context(
        self,
        episodes: list[Any] | None = None,
        focus_targets: list[Any] | None = None,
        feedback: list[Any] | None = None,
        emotion_arcs: list[Any] | None = None,
        physiological_rhythm_logs: list[Any] | None = None,
        physiological_rhythm_trend: dict[str, Any] | None = None,
        expression_profiles: list[Any] | None = None,
        behavior_patterns: list[Any] | None = None,
        reply_effects: list[Any] | None = None,
        memory_corrections: list[Any] | None = None,
        expression_reviews: list[Any] | None = None,
        behavior_scenes: list[Any] | None = None,
        focus_slots: list[Any] | None = None,
        expression_intents: list[Any] | None = None,
        mid_summaries: list[Any] | None = None,
        temporary_expression_states: list[Any] | None = None,
        terms: list[Any] | None = None,
        boundaries: list[Any] | None = None,
    ) -> str:
        sections: list[str] = []
        self._hidden_life_sections(sections, episodes=episodes, focus_targets=focus_targets)
        self._hidden_behavior_sections(sections, feedback=feedback)
        self._hidden_emotion_sections(sections, emotion_arcs=emotion_arcs)
        self._hidden_rhythm_sections(
            sections,
            physiological_rhythm_logs=physiological_rhythm_logs,
            physiological_rhythm_trend=physiological_rhythm_trend,
        )
        self._hidden_expression_sections(sections, reply_effects=reply_effects)
        self._hidden_memory_sections(sections, memory_corrections=memory_corrections)
        self._hidden_expression_sections(
            sections,
            expression_profiles=expression_profiles,
            expression_reviews=expression_reviews,
        )
        self._hidden_behavior_sections(sections, behavior_patterns=behavior_patterns, behavior_scenes=behavior_scenes)
        self._hidden_memory_sections(sections, focus_slots=focus_slots, mid_summaries=mid_summaries)
        self._hidden_expression_sections(
            sections,
            temporary_expression_states=temporary_expression_states,
            expression_intents=expression_intents,
        )
        self._hidden_memory_sections(sections, terms=terms, boundaries=boundaries)
        return "\n".join(sections)
