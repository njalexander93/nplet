"""Verify synchronized completion admission and recovery over real lifecycle stores."""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from nplet.adapters import claude, codex, common, cursor
from nplet.adapters import session_repository as carrier
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.native_events import event
from tests.support import Fixture

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_completion_waits_for_overlapping_core_commit(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """Settle the finished call after a concurrent durable transaction publishes safely.

    Args:
        monkeypatch: Pause only the writer at its existing durable-intent failpoint.
        host: Native completion shape sharing the synchronized core lookup.
    """
    # A genuine admitted call must survive another operation's in-progress transaction.
    case = Fixture()
    case.setUp()
    case.base["host"] = {"codex": "codex", "claude": "claude-code", "cursor": "cursor"}[host]
    case.base["coordinator"] = core.participant_key(case.base)
    intent, release, observer = threading.Event(), threading.Event(), threading.Event()
    try:
        case.create()
        case.ready()
        finished = cursor.cursor_children.call_token("finished") if host == "cursor" else "finished"
        case.call("tool-start", tool_id=finished)
        request = case.req("tool-start", tool_id="other")

        def pause(stage: str) -> None:
            """Hold the writer after durable intent until the completion overlaps it.

            Args:
                stage: Existing core failpoint reached by a transaction.
            """
            if stage == "intent" and not intent.is_set():
                intent.set()
                assert release.wait(2)

        def complete() -> dict:
            """Deliver the actual terminal callback while the writer owns core locks.

            Returns:
                The adapter completion response.
            """
            observer.set()
            observed = event(
                case.root,
                host,
                "Write",
                {"file_path": "src/app.py", "content": "done"},
                name="PostToolUse",
                call="finished",
            )
            observed.update(
                session_id="coordinator",
                conversation_id="coordinator",
                tool_response={"isError": False},
                tool_output="{}",
            )
            return {"codex": codex, "claude": claude, "cursor": cursor}[host].handle(observed)

        # The reader must use existing locks rather than interpret live intent as corruption.
        monkeypatch.setattr(core, "FAILPOINT", pause)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(core.execute, request)
            assert intent.wait(2)
            completion = pool.submit(complete)
            assert observer.wait(2)
            try:
                with pytest.raises(TimeoutError):
                    completion.result(timeout=0.2)
            finally:
                release.set()
            response = completion.result(timeout=3)
            assert writer.result(timeout=3)["ok"]
        assert response == {}
        pending = case.state()["participants"][case.base["coordinator"]]["pending"]
        assert set(pending) == {"other"}
    # Release any blocked writer and both disposable worktrees on failure.
    finally:
        release.set()
        case.doCleanups()


def test_completion_recovers_interrupted_existing_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roll existing durable intent forward before settling only the finished call.

    Args:
        monkeypatch: Interrupt one genuine core transaction after its durable intent.
    """
    # Retain the original pending call while another operation is interrupted durably.
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        case.call("tool-start", tool_id="finished")

        def interrupt(stage: str) -> None:
            """Stop publication after intent without editing its staged bytes.

            Args:
                stage: Existing lifecycle failpoint.

            Raises:
                RuntimeError: Simulated process interruption after intent is durable.
            """
            if stage == "intent":
                raise RuntimeError("interrupted writer")

        monkeypatch.setattr(core, "FAILPOINT", interrupt)
        with pytest.raises(RuntimeError, match="interrupted writer"):
            core.execute(case.req("tool-start", tool_id="other"))
        monkeypatch.setattr(core, "FAILPOINT", None)
        # The actual completion uses normal locked recovery, never a blanket transaction retry.
        observed = event(case.root, "codex", "Write", {}, name="PostToolUse", call="finished")
        observed.update(session_id="coordinator", tool_response={"isError": False})
        assert codex.handle(observed) == {}
        pending = case.state()["participants"][case.base["coordinator"]]["pending"]
        assert set(pending) == {"other"}
        assert not (case.root / ".task/.control/issues/TEST-1/transaction.json").exists()
    # Always release the disposable repository; the process-failure fixture remains isolated.
    finally:
        case.doCleanups()


@pytest.mark.parametrize("lock", ["binding", "issue"])
def test_completion_lookup_does_not_recreate_missing_locks(lock: str) -> None:
    """Reject corrupt registered storage without creating an absent persistent lock.

    Args:
        lock: Existing binding or issue lock removed from this disposable store.
    """
    case = Fixture()
    case.setUp()
    try:
        case.create()
        case.ready()
        case.call("tool-start", tool_id="finished")
        path = (
            case.root / ".task/.bindings" / (case.base["coordinator"] + ".lock")
            if lock == "binding"
            else case.root / ".task/.control/issues/TEST-1/lock"
        )
        path.unlink()
        before = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        # A bound request requires the actual existing lock; no creating lock call is allowed.
        observed = event(case.root, "codex", "Write", {}, call="finished")
        observed["session_id"] = "coordinator"
        with pytest.raises(core.WorkspaceError) as failure:
            common.existing_admission(carrier.carry_repository(observed), "codex")
        assert failure.value.code == "RECOVERY_REQUIRED"
        assert not path.exists()
        after = {
            str(p.relative_to(case.root)): p.read_bytes()
            for p in (case.root / ".task").rglob("*")
            if p.is_file()
        }
        assert after == before
    # Deletion was confined to the disposable fixture and cannot affect real repository locks.
    finally:
        case.doCleanups()


@pytest.mark.parametrize("held_lock", ["binding", "issue"])
def test_existing_tool_request_waits_for_each_existing_lock(held_lock: str) -> None:
    """Block the lookup itself on either lock, independently of later settlement.

    Args:
        held_lock: The only persistent lock held by the controlling thread.
    """
    # Establish a genuine pending admission whose identity must survive this read.
    case = Fixture()
    case.setUp()
    started = threading.Event()
    try:
        case.create()
        case.ready()
        case.call("tool-start", tool_id="finished")
        request = case.req("tool-complete", tool_id="finished")
        before = case.state()

        def lookup() -> dict | None:
            """Select the existing admission without invoking tool-complete settlement.

            Returns:
                The exact recorded completion request after the held lock releases.
            """
            started.set()
            return core.existing_tool_request(request.copy(), "finished")

        # Hold just one lock so removing that lookup acquisition makes this test fail.
        with ThreadPoolExecutor(max_workers=1) as pool:
            with core.Store(request, create=False) as store:
                directory = (
                    store.bindings if held_lock == "binding" else store.issues.child("TEST-1")
                )
                name = case.base["coordinator"] + ".lock" if held_lock == "binding" else "lock"
                try:
                    with directory.lock(name, create=False):
                        completion = pool.submit(lookup)
                        assert started.wait(2)
                        with pytest.raises(TimeoutError):
                            completion.result(timeout=0.2)
                    result = completion.result(timeout=3)
                # The issue child is owned here; binding handles remain Store-owned.
                finally:
                    if held_lock == "issue":
                        directory.close()
            assert result is not None
            assert result["issue_id"] == "TEST-1"
            assert result["binding_generation"] == request["binding_generation"]
        assert case.state() == before
    # Release the disposable repository after success or assertion failure.
    finally:
        case.doCleanups()
