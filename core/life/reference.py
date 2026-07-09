import asyncio
import inspect

from astrbot.api import logger

from ..sources import SavedHistoryReader
from ..sources.platforms import call_bot_action, first_onebot_client, has_bot_action, parse_unified_origin
from ..clock import timestamp as life_timestamp


class ReferenceMixin:
    async def _get_persona(self, umo: str = ""):
        manager = getattr(self.context, "persona_manager", None)
        if manager:
            persona = await self._get_default_persona(manager, umo=umo)
            prompt = self._extract_persona_prompt(persona)
            if prompt:
                return prompt

            prompt = self._extract_persona_prompt(getattr(manager, "selected_default_persona_v3", None))
            if prompt:
                return prompt

        return "一个热爱生活的人"

    async def _get_default_persona(self, manager, umo: str = ""):
        getter = getattr(manager, "get_default_persona_v3", None)
        if callable(getter):
            try:
                try:
                    persona = getter(umo) if umo else getter()
                except TypeError:
                    persona = getter()
                if inspect.isawaitable(persona):
                    persona = await persona
                return persona
            except Exception as e:
                logger.debug(f"[日常生活] 读取默认人设失败：{e}")
        return None
    @staticmethod
    def _extract_persona_prompt(persona) -> str:
        if not persona:
            return ""
        if isinstance(persona, dict):
            for key in ("prompt", "system_prompt", "content"):
                text = str(persona.get(key) or "").strip()
                if text:
                    return text
            return ""
        for attr in ("prompt", "system_prompt", "content"):
            text = str(getattr(persona, attr, "") or "").strip()
            if text:
                return text
        return ""
    async def _cleanup_conversation(self, session_id: str) -> None:
        manager = getattr(self.context, "conversation_manager", None)
        if not manager:
            return
        try:
            cid = await manager.get_curr_conversation_id(session_id)
            if cid:
                await manager.delete_conversation(session_id, cid)
        except Exception as e:
            logger.debug(f"[日常生活] 清理会话失败（{session_id}）：{e}")
    async def _resolve_reference_user_name(self, target_id: str) -> str:
        target_s = str(target_id or "").strip()
        if not target_s:
            return ""
        if target_s in self._reference_name_cache:
            return self._reference_name_cache[target_s]

        name = ""
        resolver = self.contact_resolver
        if resolver:
            try:
                name = resolver.get_relationship_alias(target_s)
                if not name:
                    name = await resolver.get_onebot_nickname(target_s)
            except Exception as e:
                logger.debug(f"[日常生活] 解析参考私聊用户称呼失败：{e}")

        name = str(name or "").strip()
        self._reference_name_cache[target_s] = name
        return name
    async def _resolve_reference_user_profile(
        self,
        target_id: str,
        persona: str = "",
        candidate_name: str = "",
    ) -> dict[str, str]:
        target_s = str(target_id or "").strip()
        if not target_s:
            return {}
        resolver = self.contact_resolver
        name = await self._resolve_reference_user_name(target_s)
        if not name:
            name = self._clean_reference_name_candidate(candidate_name)
        if not name:
            return {}
        persona_hint = self._extract_reference_persona(persona, name)
        return {
            "name": name,
            "persona": persona_hint,
        }
    @staticmethod
    def _format_reference_profile(profile: dict[str, str]) -> str:
        name = str(profile.get("name") or "").strip()
        persona = str(profile.get("persona") or "").strip()
        if not name or not persona:
            return ""
        return f"称呼：{name}；人设线索：{persona}"
    @staticmethod
    def _extract_reference_persona(persona: str, name: str) -> str:
        persona_text = str(persona or "").strip()
        target_name = str(name or "").strip()
        if not persona_text or not target_name or target_name not in persona_text:
            return ""

        clauses = ReferenceMixin._split_persona_clauses(persona_text)
        matched = []
        for index, clause in enumerate(clauses):
            if target_name not in clause:
                continue
            for neighbor in (index - 1, index, index + 1):
                if 0 <= neighbor < len(clauses):
                    matched.append(clauses[neighbor])

        if not matched:
            return ""
        compact = " ".join(dict.fromkeys(item.strip() for item in matched if item.strip()))
        return compact[:300]
    @staticmethod
    def _split_persona_clauses(text: str) -> list[str]:
        clauses = []
        buffer = []
        for char in str(text or ""):
            buffer.append(char)
            if char in "。！？!?；;\n\r":
                clause = "".join(buffer).strip()
                if clause:
                    clauses.append(clause)
                buffer = []
        tail = "".join(buffer).strip()
        if tail:
            clauses.append(tail)
        return clauses
    @staticmethod
    def _clean_reference_name_candidate(value: str) -> str:
        name = str(value or "").strip()
        if not name:
            return ""
        lowered = name.lower()
        if lowered.endswith("@im.wechat") or lowered.endswith("@chatroom"):
            return ""
        if ":" in name and name.count(":") >= 2:
            return ""
        return name
    async def _fetch_deep_history(self, target_id: int, is_group: bool, hours: int, max_count: int) -> list:
        bot = first_onebot_client(self.context)
        target_type = "群聊" if is_group else "私聊"
        if not bot:
            logger.warning("[日常生活] 未找到可用于获取聊天历史的平台实例。")
            return []

        if not has_bot_action(bot):
            logger.warning("[日常生活] 当前平台实例不支持动作调用。")
            return []

        logger.debug(f"[日常生活] 正在获取 {target_type}（{target_id}）的聊天历史记录……")

        all_messages = []
        seen_ids = set()
        per_page = min(max_count + 20, 100)
        cursor_seq = 0
        cutoff_time = life_timestamp() - (hours * 3600)
        max_rounds = 20
        
        action = "get_group_msg_history" if is_group else "get_friend_msg_history"
        id_key = "group_id" if is_group else "user_id"

        for round_idx in range(max_rounds):
            if len(all_messages) >= max_count:
                break
            
            try:
                if round_idx > 0:
                    await asyncio.sleep(0.5)

                params = {
                    id_key: target_id,
                    "count": per_page
                }
                if cursor_seq > 0:
                    params["message_seq"] = cursor_seq

                resp = await call_bot_action(bot, action, **params)
                
                if isinstance(resp, dict):
                    batch_msgs = resp.get("messages", [])
                elif isinstance(resp, list):
                    batch_msgs = resp
                else:
                    break
                    
                if not batch_msgs:
                    break

                batch_seqs = []
                added_count = 0 
                
                for msg in batch_msgs:
                    seq = msg.get("message_seq") or msg.get("message_id")
                    if seq is not None:
                        try:
                            batch_seqs.append(int(seq))
                        except (TypeError, ValueError):
                            logger.debug(f"[日常生活] 跳过无法解析的消息序号：{seq}")

                    mid = msg.get("message_id")
                    if mid is None:
                        mid = f"{msg.get('time')}-{msg.get('sender',{}).get('user_id')}"
                    
                    mid_str = str(mid)
                    
                    if mid_str not in seen_ids:
                        seen_ids.add(mid_str)
                        msg_time = int(msg.get("time", 0))
                        if msg_time >= cutoff_time:
                            all_messages.append(msg)
                            added_count += 1

                if not batch_seqs:
                    break 

                if len(batch_msgs) < per_page:
                    break
                
                min_seq_in_batch = min(batch_seqs)
                
                if added_count == 0 and round_idx > 0:
                    break
                
                if cursor_seq != 0 and min_seq_in_batch >= cursor_seq:
                    break
                
                cursor_seq = min_seq_in_batch
                
            except Exception as e:
                err_str = str(e)
                if "不存在" in err_str or getattr(e, 'retcode', 0) == 1200:
                    pass
                else:
                    logger.warning(f"[日常生活] 获取历史中断：{e}")
                break
        
        all_messages.sort(key=lambda x: x.get("time", 0))
        final_msgs = all_messages[-max_count:]
        logger.debug(f"[日常生活] 获取 {target_type}（{target_id}）完成：{len(final_msgs)} 条符合条件的聊天历史记录。")
        return final_msgs
    async def _get_recent_chats(
        self,
        target_id: str,
        is_group: bool,
        hours: int,
        max_count: int,
        persona: str = "",
    ) -> str:
        target_s = str(target_id or "").strip()
        if not target_s:
            return "无"

        _, real_id = parse_unified_origin(target_s)
        probe_id = real_id or target_s
        if probe_id.isdigit():
            return await self._get_onebot_recent_chats(target_s, is_group, hours, max_count, persona=persona)

        target_umo = self.saved_history.resolve_target_umo(target_s, is_group)
        if not target_umo:
            return "无"

        try:
            messages = await self.saved_history.fetch(target_umo, max_count=max_count, hours=hours)
            if not messages:
                return "无"
            return await self._format_saved_recent_chats(messages, target_s, is_group, persona=persona)
        except Exception as e:
            logger.warning(f"[日常生活] 读取框架保存的聊天历史失败：{e}")
            return "无"
    async def _get_onebot_recent_chats(
        self,
        target_id: str,
        is_group: bool,
        hours: int,
        max_count: int,
        persona: str = "",
    ) -> str:
        try:
            if not target_id: return "无"
            _, real_id = parse_unified_origin(target_id)
            tid = int(real_id or target_id)
            raw_msgs = await self._fetch_deep_history(tid, is_group, hours, max_count)
            if not raw_msgs: return "无"

            formatted = []
            target_id_s = str(tid)
            target_profile = {}
            target_display_name = ""
            if not is_group:
                target_profile = await self._resolve_reference_user_profile(target_id_s, persona=persona)
                target_display_name = target_profile.get("name", "")
                if not target_display_name:
                    logger.warning(f"[日常生活] 参考私聊 {target_id_s} 未解析到可靠称呼，跳过该私聊历史。")
                    return "无"

            for msg in raw_msgs:
                sender_data = msg.get("sender", {})
                sender_id = str(sender_data.get("user_id", "") or "").strip()
                if not is_group and (not sender_id or sender_id == target_id_s):
                    nickname = target_display_name
                else:
                    nickname = (
                        str(sender_data.get("card", "") or "").strip()
                        or str(sender_data.get("nickname", "") or "").strip()
                        or "用户"
                    )
                
                raw_content = ""
                if "message" in msg and isinstance(msg["message"], list):
                    raw_content = "".join(
                        str(seg["data"]["text"]) for seg in msg["message"] if seg["type"] == "text" and "data" in seg and "text" in seg["data"]
                    ).strip()
                elif "raw_message" in msg:
                    raw_content = msg["raw_message"]

                if not raw_content: continue
                
                formatted.append(f"{nickname}: {raw_content}")
                
            if not formatted:
                return "无"
            profile_text = self._format_reference_profile(target_profile)
            if profile_text:
                return f"【参考对象人设线索】{profile_text}\n" + "\n".join(formatted)
            return "\n".join(formatted)
        except Exception as e:
            logger.warning(f"[日常生活] 获取聊天历史记录出错：{e}")
            return "无"
    async def _format_saved_recent_chats(
        self,
        messages: list,
        target_id: str,
        is_group: bool,
        persona: str = "",
    ) -> str:
        formatted = []
        target_profile = {}
        target_display_name = ""
        if not is_group:
            candidate_name = self._saved_private_chat_name(messages, target_id)
            target_profile = await self._resolve_reference_user_profile(
                target_id,
                persona=persona,
                candidate_name=candidate_name,
            )
            target_display_name = target_profile.get("name", "")
            target_display_name = target_display_name or "用户"

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            content = str(msg.get("content") or "").strip()
            if not content:
                continue

            role = str(msg.get("role") or "user").lower()
            if role == "assistant":
                nickname = "我"
            elif is_group:
                nickname = (
                    str(msg.get("name") or "").strip()
                    or str(msg.get("user_id") or "").strip()
                    or "用户"
                )
            else:
                nickname = target_display_name

            formatted.append(f"{nickname}: {content}")

        if not formatted:
            return "无"
        profile_text = self._format_reference_profile(target_profile)
        if profile_text:
            return f"【参考对象人设线索】{profile_text}\n" + "\n".join(formatted)
        return "\n".join(formatted)
    def _saved_private_chat_name(self, messages: list, target_id: str) -> str:
        _, real_id = parse_unified_origin(str(target_id or "").strip())
        target_keys = {item for item in (str(target_id or "").strip(), real_id) if item}
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "user").lower() == "assistant":
                continue
            name = self._clean_reference_name_candidate(str(msg.get("name") or "").strip())
            if name:
                return name
            user_id = str(msg.get("user_id") or "").strip()
            if user_id and user_id not in target_keys:
                cleaned = self._clean_reference_name_candidate(user_id)
                if cleaned:
                    return cleaned
        return ""
    async def _get_persona_user_name(self) -> str:
        resolver = self.contact_resolver
        if resolver and hasattr(resolver, "get_persona_user_name"):
            try:
                return str(await resolver.get_persona_user_name() or "").strip()
            except Exception as e:
                logger.debug(f"[日常生活] 读取人格用户称呼失败：{e}")
        return ""
