"""Verify temporary fixture diagnostics against real monitoring and coverage machinery."""

import errno
import inspect
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import coverage
import pytest

from nplet.lifecycle import task_workspace as w
from tests.support import ROOT, Fixture, fixture_diagnostics
from tests.types import JsonObject

pytestmark = pytest.mark.integration


@pytest.fixture
def envelope() -> JsonObject:
    """Provide a valid registration envelope without creating lifecycle state.

    Returns:
        A request whose register boundary is replaced to inject rare failures.
    """
    return {"schema_version": 1, "request_id": "diagnostic-request", "operation": "register"}


@pytest.fixture
def fixture(monkeypatch: pytest.MonkeyPatch, envelope: JsonObject) -> Fixture:
    """Provide only the call/assertion surface, without Git setup.

    Args:
        monkeypatch: Scoped seam replacement for request construction.
        envelope: Fixed valid envelope used by the real dispatcher.

    Returns:
        A fixture with its request builder bound to the supplied envelope.
    """
    instance = Fixture()
    monkeypatch.setattr(instance, "req", lambda *args, **kwargs: envelope)
    return instance


def fail_register(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Inject a specific error at the real dispatcher's registration boundary.

    Args:
        monkeypatch: Scoped boundary replacement.
        error: Exception to raise within the real execute frame.
    """

    def register(request: JsonObject) -> JsonObject:
        """Raise the injected fault without touching persistent state.

        Args:
            request: Unused synthetic registration envelope.

        Raises:
            Exception: The exact injected exception instance.
        """
        raise error

    monkeypatch.setattr(w, "register", register)


@pytest.mark.parametrize(
    "error, expected",
    [
        (OSError(errno.EIO, "synthetic disk failure", "fixture.json"), "errno"),
        (subprocess.TimeoutExpired(["git", "status"], 10), "timeout"),
        (subprocess.SubprocessError("synthetic process failure"), "SubprocessError"),
    ],
)
def test_failed_call_reports_handled_error(
    monkeypatch: pytest.MonkeyPatch, fixture: Fixture, error: Exception, expected: str
) -> None:
    """Expose actionable error metadata while retaining the original failure code.

    Args:
        monkeypatch: Scoped fault injection.
        fixture: Minimal assertion fixture.
        error: Selected filesystem or process error.
        expected: Metadata unique to the injected error family.
    """
    # Inject the rare failure behind the real execute exception translation.
    if isinstance(error, OSError):
        error.winerror = 32
    fail_register(monkeypatch, error)
    with fixture_diagnostics() as diagnostics:
        with pytest.raises(AssertionError) as caught:
            fixture.call("register")
    # Assert both the original contract and the diagnostic location and attributes.
    assert "RECOVERY_REQUIRED" in str(caught.value)
    message = str(list(diagnostics))
    assert type(error).__name__ in message
    assert expected in message
    assert "register" in message
    assert "test_fixture_diagnostics.py" in message
    if isinstance(error, OSError):
        assert "'winerror': 32" in message
        assert "fixture.json" in message
    if isinstance(error, subprocess.TimeoutExpired):
        assert "git" in message
        assert "10" in message


def test_success_identity_and_unmodified_failure(
    monkeypatch: pytest.MonkeyPatch, fixture: Fixture
) -> None:
    """Return successful responses unchanged and never add metadata to core responses.

    Args:
        monkeypatch: Scoped dispatcher boundary replacements.
        fixture: Minimal assertion fixture.
    """
    # Preserve identity even when a successful dispatcher has no diagnostics.
    success = {"ok": True, "sentinel": "unchanged"}
    monkeypatch.setattr(w, "register", lambda request: success)
    assert fixture.call("register") is success
    # A failure response also remains the exact unmodified original dictionary.
    failure = {"ok": False, "code": "SYNTHETIC"}
    monkeypatch.setattr(w, "register", lambda request: failure)
    with pytest.raises(AssertionError) as caught:
        fixture.call("register")
    assert str(failure) in str(caught.value)
    assert failure == {"ok": False, "code": "SYNTHETIC"}
    assert "Fixture diagnostics" not in str(caught.value)


def test_foreign_frames_threads_and_exception_families(
    monkeypatch: pytest.MonkeyPatch, envelope: JsonObject
) -> None:
    """Exclude handled errors outside this thread's dispatcher and unrelated families.

    Args:
        monkeypatch: Scoped injected registration failure.
        envelope: Valid registration envelope.
    """
    # Exercise the same execute code in another real thread while observing this one.
    fail_register(monkeypatch, OSError("foreign thread"))
    with fixture_diagnostics() as diagnostics:
        worker = threading.Thread(target=w.execute, args=(envelope,))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        # Handle an eligible family in a foreign frame on the observing thread.
        try:
            raise OSError("foreign frame")
        except OSError:
            pass
        # Translate a non-filesystem/process error in the actual dispatcher.
        fail_register(monkeypatch, ValueError("unrelated family"))
        assert w.execute(envelope)["code"] == "INVALID_REQUEST"
    assert list(diagnostics) == []


def test_text_count_and_traceback_are_bounded(
    monkeypatch: pytest.MonkeyPatch, envelope: JsonObject
) -> None:
    """Bound repeated exception observations without retaining output or local objects.

    Args:
        monkeypatch: Scoped registration failure.
        envelope: Valid registration envelope.
    """
    # Include output that must never become part of retained diagnostic fields.
    error = subprocess.TimeoutExpired(["git", "x" * 1000], 10, output="private-output")
    fail_register(monkeypatch, error)
    with fixture_diagnostics() as diagnostics:
        for index in range(23):
            error.cmd = ["git", str(index) + "x" * 1000]
            assert not w.execute(envelope)["ok"]
    # Only bounded scalar fields and code locations survive capture.
    assert len(diagnostics) == 16
    assert "7xxxx" in diagnostics[0]["command"]
    assert "22xxxx" in diagnostics[-1]["command"]
    for record in diagnostics:
        assert len(record["message"]) <= 512
        assert len(record["command"]) <= 512
        assert len(record["traceback"]) <= 8
        assert "private-output" not in str(record)
        assert "request" not in record
        assert "locals" not in record


@pytest.mark.parametrize("raises", [False, True])
def test_owned_slot_cleanup(raises: bool) -> None:
    """Release events and the callback on both ordinary and exceptional body exits.

    Args:
        raises: Whether the observed body propagates an unexpected exception.
    """
    # Snapshot ownership and find the actual newly allocated slot inside the context.
    before = [sys.monitoring.get_tool(i) for i in range(6)]
    owned = None
    try:
        with fixture_diagnostics():
            owned = next(i for i in range(6) if sys.monitoring.get_tool(i) != before[i])
            assert sys.monitoring.get_events(owned) == sys.monitoring.events.EXCEPTION_HANDLED
            if raises:
                raise RuntimeError("body failed")
    except RuntimeError as error:
        assert raises and str(error) == "body failed"
    # Reclaim the now-free slot to verify the callback and events were explicitly cleared.
    assert [sys.monitoring.get_tool(i) for i in range(6)] == before
    assert owned is not None
    sys.monitoring.use_tool_id(owned, "cleanup-probe")
    try:
        assert sys.monitoring.get_events(owned) == 0
        assert (
            sys.monitoring.register_callback(owned, sys.monitoring.events.EXCEPTION_HANDLED, None)
            is None
        )
    finally:
        sys.monitoring.free_tool_id(owned)


def test_occupied_slots_preserved(monkeypatch: pytest.MonkeyPatch, fixture: Fixture) -> None:
    """Leave other tools intact and retain original failure semantics without a free slot.

    Args:
        monkeypatch: Scoped registration failure.
        fixture: Minimal assertion fixture.
    """

    def callback(*args: object) -> None:
        """Keep a harmless foreign callback installed during slot exhaustion.

        Args:
            *args: Monitoring event arguments ignored by this probe.
        """

    # Occupy every currently free slot, preserving any tool already present.
    slots = []
    event = sys.monitoring.events.EXCEPTION_HANDLED
    before = [sys.monitoring.get_tool(i) for i in range(6)]
    try:
        for i in range(6):
            if before[i] is None:
                sys.monitoring.use_tool_id(i, "occupied-probe")
                slots.append(i)
                sys.monitoring.register_callback(i, event, callback)
                sys.monitoring.set_events(i, event)
        snapshot = [sys.monitoring.get_tool(i) for i in range(6)]
        fail_register(monkeypatch, OSError("unobserved"))
        with fixture_diagnostics() as diagnostics:
            with pytest.raises(AssertionError) as caught:
                fixture.call("register")
        assert "RECOVERY_REQUIRED" in str(caught.value)
        assert "All sys.monitoring tool slots are occupied" in str(diagnostics)
        assert [sys.monitoring.get_tool(i) for i in range(6)] == snapshot
        for i in slots:
            assert sys.monitoring.get_events(i) == event
            assert sys.monitoring.register_callback(i, event, callback) is callback
    # Release only the probe-owned slots even if a diagnostic assertion fails.
    finally:
        for i in slots:
            sys.monitoring.set_events(i, 0)
            sys.monitoring.register_callback(i, event, None)
            sys.monitoring.free_tool_id(i)
    assert [sys.monitoring.get_tool(i) for i in range(6)] == before


def test_ctrace_stays_active(monkeypatch: pytest.MonkeyPatch, envelope: JsonObject) -> None:
    """Keep real C-tracer coverage active during and after monitoring observation.

    Args:
        monkeypatch: Scoped injected registration failure.
        envelope: Valid registration envelope.
    """
    # Start a separate in-memory collector using the repository's required tracing core.
    collector = coverage.Coverage(data_file=None, config_file=False, branch=True)
    collector.set_option("run:core", "ctrace")
    fail_register(monkeypatch, OSError("covered error"))
    collector.start()
    try:
        tracer = sys.gettrace()
        assert tracer is not None and type(tracer).__name__ == "CTracer"
        with fixture_diagnostics() as diagnostics:
            assert not w.execute(envelope)["ok"]
            assert sys.gettrace() is tracer
        assert sys.gettrace() is tracer
        assert not w.execute(envelope)["ok"]
    finally:
        collector.stop()
    # Verify actual measured dispatcher lines and arcs, rather than just a tracer identity.
    filename = str(Path(w.__file__).resolve())
    data = collector.get_data()
    assert diagnostics[0]["type"] == "OSError"
    source, start = inspect.getsourcelines(w.execute)
    recovery_line = start + next(
        i for i, line in enumerate(source) if '"RECOVERY_REQUIRED"' in line
    )
    assert recovery_line in data.lines(filename)
    assert data.arcs(filename)


def test_child_pytest_reports_direct_adapter_and_unittest_subtest_failures(tmp_path: Path) -> None:
    """Prove real pytest phase/subtest failures retain outcomes and gain diagnostics.

    Args:
        tmp_path: Disposable child suite, plugin reports and coverage data directory.
    """
    # Run the installed report wrappers in a fresh interpreter with ordinary and unittest tests.
    suite = tmp_path / "test_report_probe.py"
    suite.write_text(
        textwrap.dedent('''\
            import unittest
            from unittest.mock import patch
            import pytest
            from nplet.adapters import cursor

            def handled(message):
                """Exercise a handled error through a direct adapter call."""
                with patch.object(cursor.common, "native_identity", side_effect=OSError(message)):
                    return cursor.handle({"hook_event_name": "preToolUse"})

            @pytest.fixture(autouse=True)
            def phases(request):
                """Exercise setup and teardown while preserving their original failures."""
                handled("setup-marker")
                if request.node.name == "test_setup_failure":
                    raise AssertionError("ORIGINAL_SETUP")
                yield
                handled("teardown-marker")
                if request.node.name == "test_teardown_failure":
                    raise AssertionError("ORIGINAL_TEARDOWN")

            def test_ordinary_failure():
                """Keep a direct handle assertion visible in the failed report."""
                assert handled("direct-marker") == {"permission": "allow"}, "ORIGINAL_DIRECT"

            class TestSubtest(unittest.TestCase):
                """Exercise pytest's real unittest subtest conversion."""
                def test_subtest(self):
                    """Keep one failed and one passing subtest independent."""
                    with self.subTest(case="failure"):
                        self.assertEqual(handled("subtest-marker"), {"permission": "allow"},
                                         "ORIGINAL_SUBTEST")
                    with self.subTest(case="success"):
                        self.assertTrue(True)

            def test_success():
                """Keep passing reports quiet despite observed handled errors."""
                assert handled("passing-marker")["permission"] == "deny"

            def test_teardown_failure():
                """Reach a failing teardown without failing the call."""

            def test_setup_failure():
                """Remain uncalled after the original setup failure."""
        '''),
        encoding="utf-8",
    )
    # Record the exact reports emitted after unittest has converted its subtest report.
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent('''\
            import json
            import os
            from pathlib import Path

            def pytest_runtest_logreport(report):
                """Retain emitted report contracts for the parent assertions."""
                with Path(os.environ["DIAGNOSTIC_REPORTS"]).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"nodeid": report.nodeid, "when": report.when,
                        "outcome": report.outcome, "type": type(report).__name__,
                        "sections": report.sections, "longrepr": str(report.longrepr)}) + "\\n")
        '''),
        encoding="utf-8",
    )
    reports_file = tmp_path / "reports.jsonl"
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src"))),
        "DIAGNOSTIC_REPORTS": str(reports_file),
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.conftest",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(suite),
            "--cov=nplet",
            "--cov-report=",
            "--cov-fail-under=0",
            "-q",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Every intended failure survives, including the emitted SubtestReport and phase errors.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Handled package exceptions" in result.stdout
    assert '"message": "direct-marker"' in result.stdout
    assert '"message": "subtest-marker"' in result.stdout
    reports = [json.loads(line) for line in reports_file.read_text(encoding="utf-8").splitlines()]
    failed = [report for report in reports if report["outcome"] == "failed"]
    assert len(failed) == 4, reports
    expected = {"ORIGINAL_DIRECT", "ORIGINAL_SUBTEST", "ORIGINAL_SETUP", "ORIGINAL_TEARDOWN"}
    assert all(any(marker in report["longrepr"] for report in failed) for marker in expected)
    subtest = next(report for report in failed if "ORIGINAL_SUBTEST" in report["longrepr"])
    assert subtest["type"] == "SubtestReport"
    assert {report["when"] for report in failed} == {"setup", "call", "teardown"}
    for report in failed:
        sections = [
            body for title, body in report["sections"] if title == "Handled package exceptions"
        ]
        assert len(sections) == 1
        assert "OSError" in sections[0]
        assert "setup-marker" in sections[0]
    assert "subtest-marker" in str(subtest["sections"])
    assert "direct-marker" in str(
        next(r for r in failed if "ORIGINAL_DIRECT" in r["longrepr"])["sections"]
    )
    assert "teardown-marker" in str(next(r for r in failed if r["when"] == "teardown")["sections"])
    # A handled error alone never changes success or adds a report section.
    success = [report for report in reports if report["nodeid"].endswith("::test_success")]
    assert len(success) == 3
    assert all(report["outcome"] == "passed" for report in success)
    assert all(not report["sections"] for report in success)
