"""Verify Cursor invocation capacity behavior."""

from __future__ import annotations

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.lifecycle.cursor_support import InvocationFixture, invocation_execute

pytestmark = pytest.mark.integration


class CursorCapacityTests(InvocationFixture):
    """Exercise capacity behavior over real disposable lifecycle stores."""

    def test_capacity_keeps_admitted_bind_and_retirement_room(self) -> None:
        """Reject unrelated metadata growth while preserving already admitted settlement."""
        invocation = self.start()
        before = self.fill_settlement_boundary()
        state = self.state()
        # An unrelated observation cannot steal an admitted child's settlement reserve.
        assert (
            invocation_execute(self.req("event", event_type="observation", event={"code": "OK"}))[
                "code"
            ]
            == "ARCHIVE_PENDING"
        )
        assert self.state() == state
        self.bind("first", invocation)
        before = self.assert_settlement_shrinks(before)
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        self.assert_settlement_shrinks(before)

    def test_capacity_keeps_source_content_completion_and_retirement_room(self) -> None:
        """Settle actual source proof and completion at the retained-state boundary."""
        _, data, path = self.source_child("source bytes \u2603")
        before = self.fill_settlement_boundary()
        length = len(data.decode("utf-8").encode("utf-16-le")) // 2
        content = self.source_event(
            "content", path, observed_sha256=core.sha(data), content_length=length
        )
        self.require_ok(invocation_execute(content))
        before = self.assert_settlement_shrinks(before)
        complete = self.source_event("success", path, content_length=length)
        result = self.require_ok(invocation_execute(complete))
        before = self.assert_settlement_shrinks(before)
        state = self.state()
        assert invocation_execute(complete) == result
        assert self.state() == state
        invocation = {"invocation_id": next(iter(state["cursor_invocations"]))}
        self.require_ok(invocation_execute(self.retirement("first", invocation)))
        self.assert_settlement_shrinks(before)
