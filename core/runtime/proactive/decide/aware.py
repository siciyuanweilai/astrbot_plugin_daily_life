from typing import Any


class ProactiveAwareMixin:

    async def _build_recent_group_awareness_for_proactive(self, event: Any) -> str:
        environments = await self.archive.get_recent_group_environments(5)
        decisions = await self.archive.get_recent_action_decisions(5)
        visibility = await self.archive.get_recent_message_visibility(5)
        session_id = self._event_session_id(event)
        group_id, _ = self._event_group_meta(event)

        def in_scope(item: Any) -> bool:
            item_group = str(getattr(item, "group_id", "") or "").strip()
            item_session = str(getattr(item, "session_id", "") or "").strip()
            return bool((group_id and item_group == group_id) or (session_id and item_session == session_id))

        lines: list[str] = []
        for env in [item for item in environments if in_scope(item)][:2]:
            lines.append(
                f"- 群氛围：{env.atmosphere or '未知'}；话题：{env.topic or '未明确'}；"
                f"适合加入：{env.suitable_to_join or '未判断'}；参与欲 {env.participation_desire}/100；"
                f"摘要：{env.summary or '无'}"
            )
        for decision in [item for item in decisions if in_scope(item)][:2]:
            lines.append(
                f"- 最近动作：{decision.action or '观察'}；理由：{decision.reason or '无'}；"
                f"策略：{decision.reply_strategy or '无'}"
            )
        for item in [entry for entry in visibility if in_scope(entry)][:2]:
            lines.append(
                f"- 最近留意：{item.sender_name or item.sender_profile_id}，{item.visibility}，"
                f"心理新鲜度 {item.psychological_freshness}/100；{item.reason or '无理由'}"
            )
        return "\n".join(lines) if lines else "暂无近期会话感知。"
