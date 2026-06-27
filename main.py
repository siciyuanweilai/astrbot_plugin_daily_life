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

    @filter.llm_tool(name="life_image_generate")
    async def tool_life_image_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
    ):
        """
        根据当前角色生活场景生成并发送一张图片。
        适合用户想看当前状态、穿搭、环境、自拍/生活照，或普通聊天里用画面展示此刻更自然的时候。

        Args:
            prompt(string): 图片画面要求，例如“雨夜沙发上随手拍的一张生活照，暖色台灯，慵懒居家感，半身生活照”。
        """
        return await self.runtime.life_image_generate(event, str(prompt or "").strip())

    @filter.llm_tool(name="edit_life_image")
    async def tool_edit_life_image(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        reference_image: str = "",
    ):
        """
        根据参考图生成并发送一张新的生活图片；适合用户发图、引用图或明确给出图片链接/路径后再改图。
        reference_image 留空时会自动尝试当前消息或引用消息里的图片。

        Args:
            prompt(string): 想要的图片效果，例如“保留人物姿势，改成午后咖啡店随手拍，暖色自然光，生活抓拍感”。
            reference_image(string): 可选参考图片路径或 URL；留空时会尝试使用当前消息或引用消息里的图片。
        """
        return await self.runtime.edit_life_image(
            event,
            str(prompt or "").strip(),
            str(reference_image or "").strip(),
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

        Args:
            prompt(string): 视频画面要求，例如“傍晚从书店门口走出来，手里抱着纸袋，镜头轻轻晃动，有街边环境声”。
        """
        return await self.runtime.life_video_generate(event, str(prompt or "").strip())

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

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        await self.runtime.inject_life_context(req, event)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response):
        stop_recalled = getattr(self.runtime, "stop_recalled_event_before_history", None)
        if callable(stop_recalled):
            stop_recalled(event)

    @filter.on_decorating_result(priority=-900)
    async def on_decorating_result(self, event: AstrMessageEvent):
        suppress_recalled = getattr(self.runtime, "suppress_recalled_event_result", None)
        if callable(suppress_recalled) and suppress_recalled(event):
            return
        if self.runtime.suppress_intermediate_tool_result(event):
            return
        if self.runtime.hold_life_video_final_text(event):
            return
        await self.runtime.apply_voice_switch_before_send(event)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        structured_note = getattr(self.runtime, "note_structured_sent_result", None)
        if callable(structured_note):
            structured_note(event)
        self.runtime.note_proactive_bot_reply(event)
        self.runtime.note_voice_switch_text_result(event)

    @filter.event_message_type(
        EventMessageType.GROUP_MESSAGE | EventMessageType.PRIVATE_MESSAGE,
        priority=-90,
    )
    async def on_message_for_proactive_reply(self, event: AstrMessageEvent):
        note_recalled = getattr(self.runtime, "note_recalled_message", None)
        if callable(note_recalled) and note_recalled(event):
            return
        structured_note = getattr(self.runtime, "note_structured_incoming_message", None)
        if callable(structured_note):
            structured_note(event)
        visual_note = getattr(self.runtime, "schedule_visual_context_from_event", None)
        if callable(visual_note):
            visual_note(event)
        self.runtime.note_proactive_activity(event)
        await self.runtime.apply_response_gate_for_event(event)

    @filter.command("生活")
    async def life_command(self, event: AstrMessageEvent):
        async for result in self.commands.dispatch(event):
            yield result
