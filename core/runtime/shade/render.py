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

    def _format_hidden_experience_context(
        self,
        episodes: list[Any] | None = None,
        focus_targets: list[Any] | None = None,
        feedback: list[Any] | None = None,
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
        add = self._hidden_section
        lines = self._hidden_lines
        first = self._hidden_first
        join = self._hidden_join

        def episode_line(item: Any) -> str:
            title = first(item, "title", limit=60)
            body = first(item, "correction", "summary", "impact", limit=120)
            return f"- {title or '生活片段'}：{body or '无摘要'}" if title or body else ""

        def focus_line(item: Any) -> str:
            label = first(item, "label", "target_id", limit=60)
            if not label:
                return ""
            reason = first(item, "reason", limit=100)
            priority = first(item, "priority", limit=8)
            return f"- {label}: 优先级{priority or 0}; {reason or '近期自然多留意'}"

        def feedback_line(item: Any) -> str:
            action = first(item, "action", limit=50)
            result = first(item, "feedback", "result", limit=100)
            if not (action or result):
                return ""
            score = first(item, "score", limit=8)
            return f"- {action or '行为'}: {result or '无反馈'}; 分值{score or 0}"

        def effect_line(item: Any) -> str:
            text = first(item, "reply_text", limit=70)
            evidence = first(item, "evidence", "reason", limit=100)
            if not (text or evidence):
                return ""
            outcome = first(item, "outcome", limit=30)
            return f"- {text or '闲时回应'}: {outcome or '待观察'}；{evidence or '无补充'}"

        def correction_line(item: Any) -> str:
            target = first(item, "target_id", limit=60)
            correction = first(item, "correction", limit=120)
            return f"- {target}: {correction}" if target and correction else ""

        def expression_line(item: Any) -> str:
            label = first(item, "label", "scope", limit=60)
            tone = first(item, "tone", limit=100)
            habits = join(getattr(item, "habits", []), limit=60, count=3)
            return f"- {label}: {tone or '表达习惯'}；{habits}" if label and (tone or habits) else ""

        def review_line(item: Any) -> str:
            passed = "通过" if bool(getattr(item, "passed", True)) else "不宜发送"
            risk = first(item, "risk", limit=60)
            suggestion = first(item, "suggestion", "reason", limit=100)
            if not (risk or suggestion):
                return ""
            return f"- 表达审核{passed}: {risk or suggestion}" + (f"；建议 {suggestion}" if risk and suggestion else "")

        def pattern_line(item: Any) -> str:
            scene = first(item, "scene", limit=60)
            pattern = first(item, "pattern", limit=120)
            if not (scene and pattern):
                return ""
            action = first(item, "suggested_action", limit=40)
            return f"- {scene}: {pattern}" + (f"；倾向 {action}" if action else "")

        def scene_line(item: Any) -> str:
            scene = first(item, "scene", limit=60)
            if not scene:
                return ""
            cues = join(getattr(item, "cues", []), limit=50, count=3)
            action = first(item, "preferred_action", limit=40)
            avoid = first(item, "avoid_action", limit=50)
            return f"- {scene}: {cues or '语义场景'}；倾向{action or '观察'}" + (f"；避免{avoid}" if avoid else "")

        def slot_line(item: Any) -> str:
            label = first(item, "label", "focus_key", limit=60)
            if not label:
                return ""
            reason = first(item, "reason", limit=100)
            priority = first(item, "priority", limit=8)
            return f"- {label}: 注意槽{priority or 0}/100；{reason or '短期仍会想起'}"

        def mid_line(item: Any) -> str:
            label = first(item, "scope_label", "session_id", limit=60)
            body = "；".join(
                part
                for part in (
                    first(item, "topic", limit=70),
                    first(item, "mood", limit=70),
                    first(item, "summary", limit=140),
                )
                if part
            )
            return f"- {label}: {body}" if label and body else ""

        def temp_line(item: Any) -> str:
            label = first(item, "label", limit=60)
            expires_at = first(item, "expires_at", limit=40)
            body = "；".join(
                part
                for part in (
                    first(item, "tone", limit=100),
                    first(item, "reason", limit=120),
                    f"强度{first(item, 'intensity', limit=8) or 0}/100",
                    f"到期{expires_at}" if expires_at else "",
                )
                if part
            )
            return f"- {label}: {body}" if label and body else ""

        def intent_line(item: Any) -> str:
            emotion = first(item, "emotion", limit=50)
            category = first(item, "emotion_category", limit=20)
            emoji = first(item, "emoji_intent", limit=50)
            action = first(item, "action_intent", limit=80)
            return (
                f"- 情绪:{emotion or '无'}；分类:{category or '无'}；表情:{emoji or '无'}；动作:{action or '无'}"
                if emotion or category or emoji or action
                else ""
            )

        def term_line(item: Any) -> str:
            term = first(item, "term", limit=40)
            meaning = first(item, "meaning", limit=100)
            if not (term and meaning):
                return ""
            detail = "；".join(
                part
                for part in (
                    meaning,
                    first(item, "scene", limit=80),
                    f"熟悉度{first(item, 'familiarity', limit=8) or 0}/100",
                    join(getattr(item, "examples", []), limit=60, count=2),
                )
                if part
            )
            return f"- {term}: {detail}"

        def boundary_line(item: Any) -> str:
            source = first(item, "source_scope", limit=60)
            target = first(item, "target_scope", limit=60)
            if not (source and target):
                return ""
            policy = first(item, "policy", limit=20)
            reason = first(item, "reason", limit=100)
            return f"- {source} -> {target}: {policy or 'ask'}; {reason or '谨慎判断'}"

        add(sections, "[HiddenLifeEpisodes]", lines(episodes, 3, episode_line))
        add(sections, "[HiddenFocusTargets]", lines(focus_targets, 4, focus_line))
        add(sections, "[HiddenBehaviorFeedback]", lines(feedback, 3, feedback_line))
        add(sections, "[HiddenReplyEffects]", lines(reply_effects, 4, effect_line))
        add(sections, "[HiddenMemoryCorrections]", lines(memory_corrections, 3, correction_line))
        add(sections, "[HiddenExpressionHabits]", lines(expression_profiles, 4, expression_line))
        add(sections, "[HiddenExpressionReviews]", lines(expression_reviews, 3, review_line))
        add(sections, "[HiddenBehaviorPatterns]", lines(behavior_patterns, 4, pattern_line))
        add(sections, "[HiddenBehaviorScenes]", lines(behavior_scenes, 4, scene_line))
        add(sections, "[HiddenFocusSlots]", lines(focus_slots, 4, slot_line))
        add(sections, "[HiddenMidConversationSummary]", lines(mid_summaries, 3, mid_line))
        add(sections, "[HiddenTemporaryExpressionState]", lines(temporary_expression_states, 3, temp_line))
        add(sections, "[HiddenExpressionIntents]", lines(expression_intents, 3, intent_line))
        add(sections, "[HiddenSceneTerms]", lines(terms, 6, term_line))
        add(
            sections,
            "[HiddenMemoryBoundaries]",
            lines(boundaries, 4, boundary_line),
            "deny 表示不跨域引用；ask 表示只在用户明确引导或上下文必要时谨慎使用。\n",
        )
        return "\n".join(sections)
