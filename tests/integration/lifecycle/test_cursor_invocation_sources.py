"""Verify Cursor invocation sources behavior."""

from __future__ import annotations

import uuid

import pytest

from nplet.lifecycle import task_workspace as core
from tests.integration.lifecycle.cursor_support import InvocationFixture, invocation_execute

pytestmark = pytest.mark.integration


class CursorSourcesTests(InvocationFixture):
    """Exercise sources behavior over real disposable lifecycle stores."""

    def test_source_preparation_and_stale_owned_update_share_the_invocation_fence(self) -> None:
        """Retain pre-readiness source effects and deny writes from a stale child packet.

        Raises:
            AssertionError: Preparation is lost or stale sources permit child work.
        """
        # Plan the actual required source before binding; later scope cannot broaden it.
        self.create()
        self.ready()
        packet = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": self.state()["files"]["roadmap.md"],
                "required": True,
                "authority": "fixture",
                "reason": "source-test",
                "stage": "implementation",
                "reader": "cursor-child",
            }
        ]
        self.call(
            "cursor-plan",
            expected_revision=self.state()["revision"],
            plan_id="first",
            parent_conversation="parent-conversation",
            task_digest="a" * 64,
            packet=packet,
            owned_paths=["context/first.md"],
        )
        invocation = self.admit("first")
        self.bind("first", invocation)
        read = self.require_ok(invocation_execute(self.child_request("read", binding_generation=1)))
        # Preparation enters pending accounting without granting ordinary readiness.
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "source-read-start",
                    binding_generation=1,
                    tool_id="source",
                    source_locator="roadmap.md",
                    packet_digest=read["packet_digest"],
                )
            )
        )
        before = self.state()
        assert (
            invocation_execute(self.retirement("first", invocation))["code"] == "PENDING_OPERATION"
        )
        assert self.state() == before
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "cursor-source-callback",
                    binding_generation=1,
                    tool_id="source",
                    source_event="failure",
                    source_path=str(self.root / ".task" / self.base["issue_id"] / "roadmap.md"),
                )
            )
        )
        self.child_ready(1)
        # A supported parent update changes required bytes without an untracked filesystem edit.
        state = self.state()
        self.call(
            "update",
            expected_revision=state["revision"],
            path="roadmap.md",
            old_digest=state["files"]["roadmap.md"],
            content="revised required source\n",
            provenance={
                "sources": self.evidence(),
                "status": "draft",
                "applicability": "fixture",
            },
        )
        before = self.state()
        assert (
            invocation_execute(
                self.child_request(
                    "tool-start",
                    binding_generation=1,
                    tool_id="stale-effect",
                )
            )["code"]
            == "SOURCE_STALE"
        )
        assert (
            invocation_execute(
                self.child_request(
                    "update",
                    binding_generation=1,
                    expected_revision=before["revision"],
                    path="context/first.md",
                    old_digest=None,
                    content="forbidden stale output",
                    provenance={
                        "sources": self.evidence(),
                        "status": "draft",
                        "applicability": "fixture",
                    },
                )
            )["code"]
            == "SOURCE_STALE"
        )
        assert self.state() == before

    def test_source_receipt_needs_content_then_correlated_success(self) -> None:
        """Keep Read pending until completion and acknowledge only its finalized receipt."""
        read, data, path = self.source_child()
        ack = self.child_request(
            "acknowledge", binding_generation=1, packet_digest=read["packet_digest"]
        )
        assert invocation_execute(ack)["code"] == "SOURCE_DELIVERY_REQUIRED"
        content = self.source_event(
            "content",
            path,
            observed_sha256=core.sha(data),
            content_length=len(data.decode("utf-8").encode("utf-16-le")) // 2,
        )
        self.require_ok(invocation_execute(content))
        assert invocation_execute(ack)["code"] == "SOURCE_DELIVERY_REQUIRED"
        before = self.state()
        assert (
            invocation_execute(
                self.child_request(
                    "tool-complete", binding_generation=1, tool_id="source", completed=True
                )
            )["code"]
            == "REQUEST_CONFLICT"
        )
        assert self.state() == before
        success = self.source_event("success", path, content_length=content["content_length"])
        result = self.require_ok(invocation_execute(success))
        assert result["source_receipt"] is True
        self.require_ok(invocation_execute({**ack, "request_id": str(uuid.uuid4())}))
        key = core.participant_key(self.child_request("read"))
        assert self.state()["participants"][key]["pending"] == {}
        after = self.state()
        assert invocation_execute(success) == result
        assert self.state() == after

    def test_finished_unverified_and_failed_sources_have_no_receipt(self) -> None:
        """Success without content and failure after content settle without readiness credit."""
        read, data, path = self.source_child()
        assert (
            self.require_ok(
                invocation_execute(self.source_event("success", path, content_length=1))
            )["source_receipt"]
            is False
        )
        assert (
            invocation_execute(
                self.child_request(
                    "acknowledge", binding_generation=1, packet_digest=read["packet_digest"]
                )
            )["code"]
            == "SOURCE_DELIVERY_REQUIRED"
        )
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "source-read-start",
                    binding_generation=1,
                    tool_id="second-source",
                    source_locator=read["references"][0]["locator"],
                    packet_digest=read["packet_digest"],
                )
            )
        )
        self.require_ok(
            invocation_execute(
                self.source_event(
                    "content",
                    path,
                    tool_id="second-source",
                    observed_sha256=core.sha(data),
                    content_length=1,
                )
            )
        )
        assert (
            self.require_ok(
                invocation_execute(self.source_event("failure", path, tool_id="second-source"))
            )["source_receipt"]
            is False
        )
        key = core.participant_key(self.child_request("read"))
        assert self.state()["participants"][key]["pending"] == {}
        assert self.state()["participants"][key].get("cursor_sources", {}) == {}

    def test_foreign_unknown_or_wrong_source_callbacks_preserve_pending(self) -> None:
        """Unknown tools and wrong source bytes cannot credit or clear a different Read."""
        _, data, path = self.source_child()
        before = self.state()
        for request, code in (
            (
                self.source_event(
                    "content", path, observed_sha256=core.sha(data + b"altered"), content_length=1
                ),
                "SOURCE_DELIVERY_REQUIRED",
            ),
            (
                self.source_event("success", path, tool_id="foreign", content_length=1),
                "UNKNOWN_OPERATION",
            ),
            (
                self.source_event(
                    "content", path + ".foreign", observed_sha256=core.sha(data), content_length=1
                ),
                "REQUEST_CONFLICT",
            ),
            (self.source_event("unknown", path), "INVALID_REQUEST"),
        ):
            assert invocation_execute(request)["code"] == code
            assert self.state() == before

    def test_source_refresh_settles_old_finished_read_without_credit(self) -> None:
        """Old provisional content cannot authorize acknowledgment of a refreshed packet."""
        read, data, path = self.source_child()
        self.require_ok(
            invocation_execute(
                self.source_event("content", path, observed_sha256=core.sha(data), content_length=1)
            )
        )
        packet = [
            {key: value for key, value in ref.items() if key != "available"}
            for ref in read["references"]
        ]
        packet[0]["sha256"] = "0" * 64
        key = core.participant_key(self.child_request("read"))
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=key,
            packet=packet,
        )
        assert (
            self.require_ok(
                invocation_execute(self.source_event("success", path, content_length=1))
            )["source_receipt"]
            is False
        )
        assert self.state()["participants"][key]["pending"] == {}
        assert self.state()["participants"][key].get("cursor_sources", {}) == {}

    def test_unicode_source_receipt_persists_metadata_only(self) -> None:
        """Use UTF16units for native length and persist no source prose."""
        text = "unique source café\n😀\n"
        read, data, path = self.source_child(text)
        length = len(text.encode("utf-16-le")) // 2
        assert length != len(data)
        self.require_ok(
            invocation_execute(
                self.source_event(
                    "content", path, observed_sha256=core.sha(data), content_length=length
                )
            )
        )
        assert (
            self.require_ok(
                invocation_execute(self.source_event("success", path, content_length=length))
            )["source_receipt"]
            is True
        )
        self.require_ok(
            invocation_execute(
                self.child_request(
                    "acknowledge", binding_generation=1, packet_digest=read["packet_digest"]
                )
            )
        )
        assert "unique source café" not in core.canonical(self.state()).decode()
