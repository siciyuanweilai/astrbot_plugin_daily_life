"""隔离既有节奏、语音、投递测试的语义依赖；真实进度链路见 testproactivegrounding。"""
from core.runtime.proactive.grounding import DeliveryApproval, conversation_snapshot, digest


class GroundingStub:
    async def context(self, scope, messages, *args):
        return {**conversation_snapshot(scope, messages), "work_items": [], "execution_tasks": []}

    def approve(self, audit, context, text, **kwargs):
        if not audit.get("valid"):
            return None, audit.get("reason", "invalid")
        return DeliveryApproval(context["scope"], context["revision"], digest(text), "social", "", ()), ""

    async def prepare_send(self, scope, text, payload):
        return DeliveryApproval(scope, "fixture", digest(text), "social", "", ()), ""

    async def current(self, *args):
        return True

    async def begin_delivery(self, approval):
        return True

    async def record_delivery(self, *args):
        pass
