from pathlib import Path

from astrbot.api.star import Context, Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_tools import StarTools

from .core.interface import DailyLifeCommandCenter, DailyLifeDashboardMixin
from .core.runtime import PLUGIN_ID, DailyLifeRuntime


class DailyLifePlugin(DailyLifeDashboardMixin, Star):
    """日常生活引擎适配器。"""

    _SEND_PIPELINE_STOP_HOOKS = (
        "suppress_recalled_event_result",
        "suppress_intermediate_tool_result",
        "suppress_sight_note_followup",
        "hold_life_video_final_text",
    )
    _SEND_PIPELINE_APPLY_HOOKS = (
        ("apply_chat_style_before_send", False),
        ("apply_voice_switch_before_send", True),
        ("apply_group_addressing_before_send", False),
        ("send_chat_style_segments_if_needed", True),
    )
    _AFTER_SENT_NOTE_HOOKS = (
        "note_structured_sent_result",
        "note_media_source_event",
    )
    _INCOMING_NOTE_HOOKS = (
        "note_structured_incoming_message",
        "schedule_emoji_capture_from_event",
        "schedule_visual_context_from_event",
        "schedule_video_context_from_event",
    )

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        data_path = self._prepare_database()
        self.runtime = DailyLifeRuntime(context, config, data_path)
        self.commands = DailyLifeCommandCenter(self.runtime)
        self._register_page_web_apis()

    def _prepare_database(self) -> Path:
        data_dir = StarTools.get_data_dir(PLUGIN_ID)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "daily_life.db"

    async def terminate(self):
        await self.runtime.terminate()

    async def get_life_context(self) -> dict:
        return await self.runtime.get_life_context()

    @staticmethod
    def _tool_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _tool_int(value: object, default: int = 0) -> int:
        try:
            return int(float(str(value or "").strip()))
        except (TypeError, ValueError):
            return default

    @filter.llm_tool(name="accept_user_invite")
    async def tool_accept_user_invite(
        self,
        event: AstrMessageEvent,
        invite_details: str = "",
    ):
        """
        处理用户对当前日常生活背景的邀约，并更新今天的后续时间轴。

        Args:
            invite_details(string): 用户提出的邀约内容，例如“晚上8点一起看电影”或“现在上号双排”。
        """
        return await self.runtime.accept_user_invite(event, str(invite_details or "").strip())

    @filter.llm_tool(name="add_memo_for_tomorrow")
    async def tool_add_memo_for_tomorrow(
        self,
        event: AstrMessageEvent,
        memo_details: str = "",
    ):
        """
        记录用户关于明天或未来一天的计划，供后续生成生活背景时强制参考。

        Args:
            memo_details(string): 用户提到的明天或未来计划，例如“和用户去草坪野餐，负责做三明治”。
        """
        return await self.runtime.add_memo_for_tomorrow(event, str(memo_details or "").strip())

    @filter.llm_tool(name="life_query")
    async def tool_life_query(
        self,
        event: AstrMessageEvent,
        target: str = "status",
        days: int = 7,
        date: str = "",
    ):
        """
        查询当前角色的日常生活资料，包括状态、今日安排、后续安排、历史、世界记忆、时间轴、偏好、生活事件和配置概览。

        Args:
            target(string): 查询类型，只选一种：status 当前状态；today 今日安排；week 本周计划；future 后续安排；history 最近历史；world 世界记忆；timeline 今日时间轴；preferences 偏好；events 生活事件；config 配置概览。
            days(int): target=history 时查询最近几天，默认 7。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        return await self.commands.query_life(
            event,
            str(target or "status").strip(),
            days=self._tool_int(days, 7),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_adjust")
    async def tool_life_adjust(
        self,
        event: AstrMessageEvent,
        action: str,
        detail: str = "",
        period: str = "",
        schedule_time: str = "",
        date: str = "",
    ):
        """
        调整当前角色的日常生活状态或生成节奏。

        Args:
            action(string): 调整动作：refresh_state 刷新实时状态；reset_day 重新生成当天；update_outfit 保持日程只更新穿搭；set_schedule_time 设置每日生成时间。
            detail(string): 用户补充的自然语言要求，例如“今天少出门”“刚刚聊了很久所以状态偏困”。
            period(string): 可选目标时段，例如 凌晨/早晨/上午/中午/下午/傍晚/晚上/深夜；用于重生成某时段状态或穿搭。
            schedule_time(string): action=set_schedule_time 时使用，格式 HH:MM。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        return await self.commands.adjust_life(
            event,
            str(action or "").strip(),
            detail=str(detail or "").strip(),
            period=str(period or "").strip(),
            schedule_time=str(schedule_time or "").strip(),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_commitment")
    async def tool_life_commitment(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        content: str = "",
        commitment_id: int = 0,
        target_date: str = "",
    ):
        """
        管理未来承诺、约定和明日备忘。

        Args:
            action(string): 操作：list 查看未完成承诺；add 新增承诺；done 标记完成；cancel 取消；reschedule 延期；memo_tomorrow 写入明日强制备忘。
            content(string): 新增承诺、明日备忘或延期说明。
            commitment_id(int): done/cancel/reschedule 时的承诺编号。
            target_date(string): reschedule 时的新日期，格式 YYYY-MM-DD；也可用 content 写“明天/周末/日期”。
        """
        return await self.commands.manage_commitment(
            event,
            str(action or "list").strip(),
            content=str(content or "").strip(),
            commitment_id=self._tool_int(commitment_id, 0),
            target_date=str(target_date or "").strip(),
        )

    @filter.llm_tool(name="life_weather")
    async def tool_life_weather(
        self,
        event: AstrMessageEvent,
        city: str = "",
    ):
        """
        查询天气；查询默认居住地天气时会同步到当前生活日。

        Args:
            city(string): 可选城市名；留空使用配置或人设里的默认居住地。
        """
        return await self.commands.query_weather(event, str(city or "").strip())

    @filter.llm_tool(name="life_review")
    async def tool_life_review(
        self,
        event: AstrMessageEvent,
        action: str = "show",
        date: str = "",
    ):
        """
        查看或生成每日复盘。

        Args:
            action(string): show 查看已有复盘；generate 重新生成复盘。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        return await self.commands.review_life(
            event,
            str(action or "show").strip(),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_memory_search")
    async def tool_life_memory_search(
        self,
        event: AstrMessageEvent,
        query: str = "",
        mode: str = "search",
        category: str = "",
        limit: int = 5,
    ):
        """
        检索当前角色已经沉淀的长期生活记忆，用于回答用户追问、确认偏好、查找纠偏、人物关系、生活事件或最近影响判断的依据。
        Args:
            query(string): 自然语言检索内容，例如“最近别总出门”“她喜欢什么生活节奏”“未来酱相关记忆”。
            mode(string): search 按内容检索；recent 查看最近沉淀的长期记忆。
            category(string): 可选分类过滤，例如 correction、short_term、relationship、chat_summary、feedback、expression。
            limit(int): 返回条数，默认 5，最多 12。
        """
        return await self.runtime.life_memory_search(
            event,
            query=str(query or "").strip(),
            mode=str(mode or "search").strip(),
            category=str(category or "").strip(),
            limit=self._tool_int(limit, 5),
        )

    @filter.llm_tool(name="life_image_generate")
    async def tool_life_image_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        subject_route: str = "free",
        use_last_reverse_prompt: bool = False,
    ):
        """
        根据当前角色生活场景生成并发送一张图片。
        适合用户想看当前状态、穿搭、环境、自拍/生活照，或普通聊天里用画面展示此刻更自然的时候。
        如果用户本轮已经给出完整图片提示词，prompt 必须原样保留用户全文，不要改写、摘要或另想场景。
        使用 subject_route 明确图片主体：current_character 当前角色本人入镜；scene 环境/氛围/状态；object 物品/食物；free 不限定主体或完整自由提示词。
        没有当前消息、引用消息或显式 reference_image 可作为真实参考图时，新增画面请求使用本工具，不要调用 edit_life_image。
        如果用户要求使用上一条图片反推结果生成，设置 use_last_reverse_prompt=true；插件会从本会话缓存读取上一条反推提示词原文，不会自动把反推原图作为图生图参考。
        有真实参考图时，如果用户明确要求按原图或参考图生成、改图或保持原图人物/构图，请调用 edit_life_image。

        Args:
            prompt(string): 图片画面要求；用户给出完整提示词时必须填完整原文，例如“雨夜沙发上随手拍的一张生活照，暖色台灯，慵懒居家感，半身生活照”；use_last_reverse_prompt=true 时此参数不参与生成。
            subject_route(string): 图片主体路线，只能填 current_character、scene、object 或 free；用户想看当前角色本人、自拍、生活照或穿搭照时填 current_character。
            use_last_reverse_prompt(bool): 是否使用本会话上一条图片反推提示词原文。
        """
        use_reverse_cache = self._tool_bool(use_last_reverse_prompt)
        return await self.runtime.life_image_generate(
            event,
            "" if use_reverse_cache else str(prompt or "").strip(),
            use_last_reverse_prompt=use_reverse_cache,
            subject_route=str(subject_route or "free").strip(),
        )

    @filter.llm_tool(name="edit_life_image")
    async def tool_edit_life_image(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        reference_image: str = "",
        generate_without_reference: bool = False,
    ):
        """
        根据参考图生成并发送一张新的生活图片；适合用户发图、引用图或明确给出图片链接/路径后再改图。
        reference_image 留空时会自动尝试当前消息或引用消息里的图片。
        只有真实参考图存在时才优先使用本工具；如果没有真实参考图，默认只提醒用户先发送或引用图片。
        generate_without_reference 是结构化兜底：仅当已确定这次不是改图、而是允许按同一画面要求生成新图时才设为 true；通常应直接调用 life_image_generate。

        Args:
            prompt(string): 想要的图片效果，例如“保留人物姿势，改成午后咖啡店随手拍，暖色自然光，生活抓拍感”。
            reference_image(string): 可选参考图片路径或 URL；留空时会尝试使用当前消息或引用消息里的图片。
            generate_without_reference(bool): 未找到真实参考图时，是否允许改走 life_image_generate 生成新图。
        """
        return await self.runtime.edit_life_image(
            event,
            str(prompt or "").strip(),
            str(reference_image or "").strip(),
            generate_without_reference=self._tool_bool(generate_without_reference),
        )

    @filter.llm_tool(name="life_image_reverse_prompt")
    async def tool_life_image_reverse_prompt(
        self,
        event: AstrMessageEvent,
        reference_image: str = "",
        source_prompt: str = "",
        profile: str = "",
    ):
        """
        根据用户当前发送、引用或指定的图片，反推出可复用的图片生成提示词。
        适合用户问“这张图提示词怎么写”“反推提示词”“按这张图写一段生图提示词”等场景。
        本工具只返回提示词文本，不生成图片，也不改图。

        Args:
            reference_image(string): 可选图片路径或 URL；留空时自动尝试当前消息或引用消息里的图片。
            source_prompt(string): 可选参考重点；当用户给了原提示词、想保留的风格或关注点时填写。
            profile(string): 可选反推方案，例如“通用”“通用超详细”“生活照”“人像”“CCD人像”“棚拍”“棚拍人像”“古风”“古风特调”“商品”“插画”。
        """
        return await self.runtime.life_image_reverse_prompt(
            event,
            str(reference_image or "").strip(),
            str(source_prompt or "").strip(),
            str(profile or "").strip(),
        )

    @filter.llm_tool(name="life_video_generate")
    async def tool_life_video_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
    ):
        """
        根据当前角色生活场景生成并发送一段短视频。
        视频生成较慢且成本更高，只适合用户明确要视频、引用图转视频，或动作/镜头变化非常强的场景。
        当前消息或引用消息里带图片时会自动作为视频首帧/参考图；普通看状态、看穿搭、发照片应优先使用图片。
        如果用户本轮已经给出完整视频提示词、分镜脚本或时间轴，prompt 必须原样保留用户全文，不要改写、摘要或翻译。

        Args:
            prompt(string): 视频画面要求；用户给出完整分镜或时间轴时必须填完整原文，例如“第1格【0-1.5秒】...第2格【1.5-3秒】...”。
        """
        return await self.runtime.life_video_generate(event, str(prompt or "").strip())

    @filter.llm_tool(name="life_video_understand")
    async def tool_life_video_understand(
        self,
        event: AstrMessageEvent,
        target: str = "",
    ):
        """
        理解用户当前发送、引用或指定的视频，并把可确认的画面信息用于后续对话。
        适合用户问“这个视频里是什么”“刚才视频怎么看”“帮我看看这段视频”等场景；不要凭生活背景猜视频内容。
        需要理解视频时直接调用本工具，不要先输出“我先看看/我去分析一下”这类中间回复；等工具结果返回后再自然回答用户。
        Args:
            target(string): 可选的视频文件路径或直链；留空时自动使用当前消息或引用消息里的视频。
        """
        return await self.runtime.life_video_understand(event, str(target or "").strip())

    @filter.llm_tool(name="life_video_note")
    async def tool_life_video_note(
        self,
        event: AstrMessageEvent,
        target: str = "",
        style: str = "professional",
    ):
        """
        把用户当前发送、引用、指定或最近已理解的视频整理成专业 Markdown 长文总结，并交给 AstrBot 文转图发送。
        只在用户明确需要“专业总结、长文总结、详细分析、总结成图、转图总结”时调用；普通询问视频内容时使用 life_video_understand。
        调用后不要再额外复述总结正文。

        Args:
            target(string): 可选的视频文件路径或直链；留空时自动使用当前消息、引用消息或最近视频。
            style(string): 可选总结风格，professional、detailed 或 concise。
        """
        return await self.runtime.life_video_note(
            event,
            str(target or "").strip(),
            str(style or "professional").strip(),
        )

    @filter.llm_tool(name="life_voice_generate")
    async def tool_life_voice_generate(
        self,
        event: AstrMessageEvent,
        text: str = "",
        emotion: str = "",
        emotion_category: str = "",
        user_requested: bool = False,
        decision_reason: str = "",
    ):
        """
        把一句角色回复合成为语音并发送，作为本轮最终回复。
        用户明确要求语音时可调用本工具并设置 user_requested=true；仅模型自主判断语音更自然时，只有在隐藏上下文允许智能切换时才调用。
        需要语音时不要先输出同句文字。
        调用时必须提交第一人称 decision_reason，说明我为什么觉得这句话适合直接说出来。
        调用成功后，不要再用文字重复同一句内容；如果工具返回失败，再改用自然文字回复。

        Args:
            text(string): 本轮最终要说出口的回复文本。
            emotion(string): 可选自然情绪描述，例如“困倦”“小声吐槽”“无奈中带点宠溺”。
            emotion_category(string): 可选情绪分类，只能是 neutral、happy、sad、angry 之一。
            user_requested(bool): 用户明确要求发送语音时填 true；只是模型自主判断语音更自然时保持 false。
            decision_reason(string): 为什么本轮更适合用语音表达，必须用第一人称内心判断；用于后台裁定记录，不会发给用户。
        """
        return await self.runtime.life_voice_generate(
            event,
            str(text or "").strip(),
            emotion=emotion,
            emotion_category=emotion_category,
            user_requested=self._tool_bool(user_requested),
            decision_reason=str(decision_reason or "").strip(),
        )

    @filter.llm_tool(name="life_emoji_send")
    async def tool_life_emoji_send(
        self,
        event: AstrMessageEvent,
        intent: str = "",
        emotion: str = "",
        emotion_category: str = "",
        decision_reason: str = "",
    ):
        """
        从本插件已收藏的表情素材池里选择一张合适表情并发送。
        用户明确要发表情，或当前文字需要配合一张已收藏表情完成语义时调用；不需要传图片路径或 URL。
        如果素材池里没有语义合适的表情，工具会直接不发送；不要为了发表情而硬调用。

        Args:
            intent(string): 本轮想发送表情的自然意图，例如“发送一张小丑自嘲表情”或“补一个调侃自嘲表情”。
            emotion(string): 可选自然情绪描述，例如“轻松调侃”“开心递上”“无奈自嘲”。
            emotion_category(string): 可选情绪分类，只能是 neutral、happy、sad、angry 之一。
            decision_reason(string): 为什么本轮适合直接发送表情，用第一人称短句说明；用于后台记录，不会发给用户。
        """
        return await self.runtime.life_emoji_send(
            event,
            intent=str(intent or "").strip(),
            emotion=str(emotion or "").strip(),
            emotion_category=str(emotion_category or "").strip(),
            decision_reason=str(decision_reason or "").strip(),
        )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        await self.runtime.inject_life_context(req, event)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response):
        stop_recalled = getattr(self.runtime, "stop_recalled_event_before_history", None)
        if callable(stop_recalled):
            stop_recalled(event)

    def _runtime_hook(self, name: str):
        hook = getattr(self.runtime, name, None)
        return hook if callable(hook) else None

    def _runtime_hook_bool(self, name: str, event: AstrMessageEvent) -> bool:
        hook = self._runtime_hook(name)
        return bool(hook(event)) if hook else False

    def _runtime_hook_call(self, name: str, event: AstrMessageEvent):
        hook = self._runtime_hook(name)
        return hook(event) if hook else None

    async def _runtime_hook_apply(self, name: str, event: AstrMessageEvent, *, is_async: bool) -> None:
        hook = self._runtime_hook(name)
        if not hook:
            return
        if is_async:
            await hook(event)
        else:
            hook(event)

    def _send_pipeline_should_stop(self, event: AstrMessageEvent) -> bool:
        return any(self._runtime_hook_bool(name, event) for name in self._SEND_PIPELINE_STOP_HOOKS)

    async def _send_pipeline_apply(self, event: AstrMessageEvent) -> None:
        for name, is_async in self._SEND_PIPELINE_APPLY_HOOKS:
            await self._runtime_hook_apply(name, event, is_async=is_async)

    @filter.on_decorating_result(priority=-900)
    async def on_decorating_result(self, event: AstrMessageEvent):
        if self._send_pipeline_should_stop(event):
            return
        await self._send_pipeline_apply(event)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        for name in self._AFTER_SENT_NOTE_HOOKS:
            self._runtime_hook_call(name, event)
        self.runtime.note_proactive_bot_reply(event)
        self.runtime.note_voice_switch_text_result(event)

    @filter.event_message_type(
        EventMessageType.GROUP_MESSAGE | EventMessageType.PRIVATE_MESSAGE,
        priority=-90,
    )
    async def on_message_for_proactive_reply(self, event: AstrMessageEvent):
        if self._runtime_hook_call("note_recalled_message", event):
            return
        for name in self._INCOMING_NOTE_HOOKS:
            self._runtime_hook_call(name, event)
        await self._runtime_hook_apply("capture_chat_memory_message", event, is_async=True)
        if self._runtime_hook_call("schedule_bili_summary_from_event", event):
            return
        self._runtime_hook_call("mark_alias_directed_event_as_wake", event)
        self.runtime.note_proactive_activity(event)
        await self.runtime.apply_response_gate_for_event(event)

    @filter.command("生活")
    async def life_command(self, event: AstrMessageEvent):
        async for result in self.commands.dispatch(event):
            yield result

    @filter.command("B站登录")
    async def bili_login_command(self, event: AstrMessageEvent):
        async for result in self.runtime.bili_login(event):
            yield result

    @filter.command("B站登出")
    async def bili_logout_command(self, event: AstrMessageEvent):
        yield await self.runtime.bili_logout(event)

    @filter.command("B站状态")
    async def bili_status_command(self, event: AstrMessageEvent):
        yield await self.runtime.bili_status(event)
