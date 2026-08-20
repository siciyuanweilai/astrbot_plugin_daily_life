import asyncio
import functools
import types
import unittest
from unittest.mock import patch

import support  # noqa: F401
from core.runtime.outbound import OutboundLogMixin, message_outline
from support import Event


class _Runtime(OutboundLogMixin):
    pass


class OutboundLogTest(unittest.TestCase):
    def test_message_outline_keeps_text_and_labels_components(self):
        chain = types.SimpleNamespace(
            chain=[
                "先看这张图",
                {"type": "image", "file": "/tmp/private.png"},
                types.SimpleNamespace(type="record", file="/tmp/private.wav"),
            ]
        )

        self.assertEqual(message_outline(chain), "先看这张图[图片][语音]")

    def test_log_outbound_message_uses_unified_bot_format(self):
        runtime = _Runtime()
        event = Event(sender_name="测试用户", sender_id="test-user")
        chain = types.SimpleNamespace(chain=[types.SimpleNamespace(text="完整回复")])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                runtime.log_outbound_message(
                    chain,
                    scope=event.unified_msg_origin,
                    source_event=event,
                    source="respond",
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 机器人: 完整回复")
        self.assertNotIn("测试用户", info.call_args.args[0])
        self.assertNotIn("test-user", info.call_args.args[0])
        self.assertEqual(info.call_args.kwargs["extra"]["category"], "user_chat")

    def test_async_outbound_log_uses_bot_instance_name_without_user_identity(self):
        runtime = _Runtime()
        event = Event(sender_name="测试用户", sender_id="test-user", self_id="test-bot")
        event.get_self_name = lambda: "测试机器人"
        chain = types.SimpleNamespace(chain=["完整回复"])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                asyncio.run(
                    runtime.log_outbound_message_async(
                        chain,
                        scope=event.unified_msg_origin,
                        source_event=event,
                        source="respond",
                    )
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 测试机器人/test-bot: 完整回复")
        self.assertNotIn("测试用户", info.call_args.args[0])
        self.assertNotIn("test-user", info.call_args.args[0])

    def test_async_outbound_log_ignores_dynamic_api_callable_as_nickname(self):
        runtime = _Runtime()

        class Bot:
            nickname = functools.partial(lambda: "", "nickname")

            async def call_action(self, action, **params):
                self.last_call = (action, params)
                return {"data": {"nickname": "测试机器人", "user_id": 188852752}}

        event = Event(
            bot=Bot(),
            sender_name="测试用户",
            sender_id="test-user",
            self_id="",
        )
        chain = types.SimpleNamespace(chain=["完整回复"])

        with patch("core.runtime.outbound.logger.info") as info:
            self.assertTrue(
                asyncio.run(
                    runtime.log_outbound_message_async(
                        chain,
                        scope=event.unified_msg_origin,
                        source_event=event,
                        source="respond",
                    )
                )
            )

        self.assertEqual(info.call_args.args[0], "[日常生活] 测试机器人/188852752: 完整回复")

    def test_direct_body_is_deduplicated_from_final_result(self):
        runtime = _Runtime()
        event = Event(sender_name="测试用户", sender_id="test-user")
        event.set_result(event.chain_result(["已经发给你啦。"]))

        self.assertTrue(
            runtime.log_outbound_message(
                event.get_result().chain,
                scope=event.unified_msg_origin,
                source_event=event,
                source="preface",
            )
        )
        event.set_result(event.chain_result(["已经发给你啦。"]))
        self.assertFalse(runtime.log_outbound_result(event))
if __name__ == "__main__":
    unittest.main()
