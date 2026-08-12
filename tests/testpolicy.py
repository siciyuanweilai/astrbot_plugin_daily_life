import unittest

from core.interface import (
    LifeAccessPolicy,
    LifeActionProposal,
    LifeActionScope,
)
from support import Event


class LifeAccessPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = LifeAccessPolicy()
        self.private_member = Event(
            role="member",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            sender_id="10001",
        )
        self.other_private_member = Event(
            role="member",
            unified_msg_origin="aiocqhttp:FriendMessage:20002",
            sender_id="20002",
        )
        self.group_member = Event(
            role="member",
            unified_msg_origin="aiocqhttp:GroupMessage:30003",
            group_id="30003",
        )
        self.group_admin = Event(
            role="admin",
            unified_msg_origin="aiocqhttp:GroupMessage:30003",
            group_id="30003",
        )

    def _allowed(
        self,
        event,
        scope: LifeActionScope,
        *,
        resource_owner: str = "",
    ) -> bool:
        proposal = LifeActionProposal.build(
            "test:action",
            scope,
            resource_owner=resource_owner,
        )
        return self.policy.decide(event, proposal).allowed

    def test_public_scope_allows_every_message_context(self):
        for event in (
            self.private_member,
            self.other_private_member,
            self.group_member,
            self.group_admin,
        ):
            with self.subTest(origin=event.unified_msg_origin, role=event.role):
                self.assertTrue(self._allowed(event, LifeActionScope.PUBLIC))

    def test_private_scope_allows_private_sessions_and_admins_only(self):
        self.assertTrue(self._allowed(self.private_member, LifeActionScope.PRIVATE))
        self.assertTrue(
            self._allowed(self.other_private_member, LifeActionScope.PRIVATE)
        )
        self.assertFalse(self._allowed(self.group_member, LifeActionScope.PRIVATE))
        self.assertTrue(self._allowed(self.group_admin, LifeActionScope.PRIVATE))

    def test_owned_scope_requires_exact_session_or_admin(self):
        owner = self.private_member.unified_msg_origin
        self.assertTrue(
            self._allowed(
                self.private_member,
                LifeActionScope.OWNED,
                resource_owner=owner,
            )
        )
        self.assertFalse(
            self._allowed(
                self.other_private_member,
                LifeActionScope.OWNED,
                resource_owner=owner,
            )
        )
        self.assertFalse(
            self._allowed(
                self.group_member,
                LifeActionScope.OWNED,
                resource_owner=owner,
            )
        )
        self.assertTrue(
            self._allowed(
                self.group_admin,
                LifeActionScope.OWNED,
                resource_owner=owner,
            )
        )

    def test_admin_scope_rejects_members_in_private_and_group_chat(self):
        self.assertFalse(self._allowed(self.private_member, LifeActionScope.ADMIN))
        self.assertFalse(self._allowed(self.group_member, LifeActionScope.ADMIN))
        self.assertTrue(self._allowed(self.group_admin, LifeActionScope.ADMIN))


if __name__ == "__main__":
    unittest.main()
