import unittest

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.runtime.delivery import EventDeliveryRequest, ReplyDeliveryService


class _Result:
    def __init__(self):
        self.chain = ["一", "二", "三", "四"]


class _Event:
    def __init__(self):
        self.result = _Result()
        self.sent = []

    def get_result(self):
        return self.result

    def clear_result(self):
        self.result = None

    async def send(self, message):
        self.sent.append(message)


class ReplyDeliveryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_event_delivery_keeps_each_artificial_delay(self):
        event = _Event()
        sleeps = []

        async def record_sleep(delay):
            sleeps.append(delay)

        service = ReplyDeliveryService(object())
        outcome = await service.send_event(
            EventDeliveryRequest(
                event=event,
                texts=("一", "二", "三", "四"),
                scope="private:test",
                match="exact",
                text_from_item=str,
                build_message=lambda index, chain: chain[index],
                delay_seconds=lambda index: 3.5,
                sleep=record_sleep,
                is_current=lambda: True,
            )
        )

        self.assertEqual(outcome.status, "sent")
        self.assertEqual(event.sent, ["一", "二", "三", "四"])
        self.assertEqual(sleeps, [3.5, 3.5, 3.5])
        self.assertEqual(sum(sleeps), 10.5)


if __name__ == "__main__":
    unittest.main()
