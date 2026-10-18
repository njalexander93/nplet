"""Model bounded runner decisions while native process tests cover OS behavior."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nplet.adapters import hook_capture, runner

pytestmark = pytest.mark.unit


def handler(_event: dict[str, object]) -> dict[str, object]:
    """Provide a known adapter module name for worker selection.

    Args:
        _event: Ignored hook event accepted by this test callback.

    Returns:
        An empty native hook response for successful worker routing.
    """
    return {}


handler.__module__ = "nplet.adapters.codex"


def failure(name: str, code: str) -> dict[str, object]:
    """Expose the bounded event and error selected by the runner.

    Args:
        name: Native hook event receiving the failure response.
        code: Stable error code selected by the runner.

    Returns:
        A native response containing the event name and public error code.
    """
    return {"name": name, "code": code}


class Worker:
    """Record the modeled worker lifecycle and its bounded response."""

    def __init__(
        self, output: bytes = b'{"ok":true}', returncode: int = 0, timeout: bool = False
    ) -> None:
        """Set one response and optional timeout for a deterministic supervisor case.

        Args:
            output: Worker output selected to exercise the result boundary.
            returncode: Expected subprocess exit status.
            timeout: Whether the fake first communicate call exceeds its deadline.
        """
        self.output = output
        self.returncode = returncode
        self.timeout = timeout
        self.calls: list[bytes | None] = []
        self.killed = False

    def __enter__(self) -> Worker:
        """Expose the modeled process without spawning one.

        Returns:
            This fake worker handle.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """End the modeled process context.

        Args:
            _args: Context manager exception fields, unused by the fake worker.
        """

    def communicate(
        self, data: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        """Return one response or simulate a deadline followed by reap.

        Args:
            data: Native event bytes on the first call, or None when reaping.
            timeout: Supervisor deadline reported if the fake times out.

        Returns:
            Captured worker output and error bytes.
        """
        # Track the send/reap sequence before deciding whether the worker timed out.
        self.calls.append(data)
        # Time out only the first exchange; kill permits the second call to reap.
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout or 0)
        return self.output, b""

    def kill(self) -> None:
        """Record forced termination before the second communicate call."""
        self.killed = True


def input_bytes(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    """Supply a bounded stdin stream without a real pipe or thread delay.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        raw: Exact native hook payload returned from fake stdin.
    """
    # Return the payload once, then EOF, without a real host pipe.
    chunks = iter([raw, b""])
    monkeypatch.setattr(runner.os, "read", lambda _fd, _size: next(chunks))
    monkeypatch.setattr(runner.sys.stdin, "fileno", lambda: 0)


def install_worker(monkeypatch: pytest.MonkeyPatch, worker: Worker) -> list[list[str]]:
    """Capture worker arguments at the subprocess lookup point.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        worker: Fake process returned when the runner launches its worker.

    Returns:
        Argument vectors captured from worker launches.
    """
    # Capture the worker launch vector while returning a deterministic process.
    starts: list[list[str]] = []

    def popen(argv: list[str], **_options: object) -> Worker:
        """Return the deterministic worker with its command captured.

        Args:
            argv: Worker launch argument vector selected by the runner.
            _options: Subprocess launch options, ignored by the fake.

        Returns:
            The configured fake worker.
        """
        starts.append(argv)
        return worker

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    return starts


def test_runner_returns_worker_result_after_bounded_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Send exact input bytes to the selected worker and preserve its result.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # Supply one valid event and a worker response without launching a subprocess.
    raw = b'{"hook_event_name":"PreToolUse"}'
    input_bytes(monkeypatch, raw)
    worker = Worker(output=b'{"decision":"deny"}')
    starts = install_worker(monkeypatch, worker)
    # Verify byte forwarding, worker selection, and native JSON output together.
    assert runner.run(handler, failure) == 0
    assert worker.calls == [raw]
    assert starts[0][-2:] == ["--worker", "codex"]
    assert json.loads(capsys.readouterr().out) == {"decision": "deny"}


@pytest.mark.parametrize("worker_status", [0, 2])
def test_observer_failure_preserves_completed_worker_decision(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    worker_status: int,
) -> None:
    """Keep diagnostic failures separate from authoritative output and exit status.

    Args:
        monkeypatch: Fixture isolating stdin, worker and observer boundaries.
        capsys: Captured native response and diagnostic streams.
        worker_status: Successful or failed authoritative worker exit status.
    """
    # Finish one modeled worker before the optional diagnostic boundary is reached.
    raw = b'{"hook_event_name":"PreToolUse"}'
    input_bytes(monkeypatch, raw)
    worker = Worker(output=b'{"decision":"deny"}', returncode=worker_status)
    starts = install_worker(monkeypatch, worker)
    observations = []

    def unavailable(event: dict, result: dict, status: int) -> None:
        """Raise at observation time after checking the worker has already completed.

        Args:
            event: Parsed native input delivered to the observer.
            result: Authoritative worker result that must survive the failure.
            status: Authoritative exit status that must survive the failure.

        Raises:
            OSError: The optional diagnostic destination is unavailable.
        """
        # Confirm ordering and retain the exact observer inputs before failure.
        assert worker.calls == [raw]
        observations.append((event, result.copy(), status))
        raise OSError("diagnostic destination unavailable")

    # The observer exception must not escape or replace a denial with an allow.
    monkeypatch.setattr(hook_capture, "capture", unavailable)
    assert runner.run(handler, failure) == worker_status
    output = capsys.readouterr()
    assert json.loads(output.out) == {"decision": "deny"}
    assert output.err == ""
    assert observations == [
        ({"hook_event_name": "PreToolUse"}, {"decision": "deny"}, worker_status)
    ]
    assert len(starts) == 1
    assert not worker.killed


def test_runner_selects_script_entrypoint_by_source_stem(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A direct file hook entry uses its known adapter filename for worker selection.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        tmp_path: Source filename used to model a direct script hook.
    """
    # Model direct execution of an adapter file with a __main__ handler.
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    namespace: dict[str, object] = {
        "__name__": "__main__",
        "__file__": str(tmp_path / "checkout/src/nplet/adapters/codex.py"),
    }
    exec("def direct_handler(event):\n    return {}", namespace)
    direct_handler = namespace["direct_handler"]
    assert callable(direct_handler)
    # The filename, rather than the generated function name, selects the worker.
    starts = install_worker(monkeypatch, Worker())
    assert runner.run(direct_handler, failure) == 0  # type: ignore[arg-type]
    assert starts[0][-1] == "codex"


def test_runner_forwards_worker_stderr_without_changing_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Worker diagnostics reach stderr while the typed response stays intact.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # Keep one valid worker result beside a diagnostic stderr stream.
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')

    class NoisyWorker(Worker):
        """Return a valid result and one bounded diagnostic byte stream."""

        def communicate(
            self, data: bytes | None = None, timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            """Record the request and supply stderr beside the response.

            Args:
                data: Bytes forwarded to the fake worker.
                timeout: Whether the fake first communicate call exceeds its deadline.

            Returns:
                Captured worker output and error bytes.
            """
            self.calls.append(data)
            return self.output, b"worker note"

    # Preserve response JSON while forwarding the worker's note to host stderr.
    install_worker(monkeypatch, NoisyWorker())
    assert runner.run(handler, failure) == 0
    output = capsys.readouterr()
    assert "worker note" in output.err
    assert json.loads(output.out) == {"ok": True}


def test_runner_reaps_worker_before_timeout_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kill and reap a timed-out worker before returning BUSY.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # The first worker exchange times out and must trigger termination.
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    worker = Worker(timeout=True)
    install_worker(monkeypatch, worker)
    # The second communicate call proves reap occurred before the BUSY response.
    assert runner.run(handler, failure) == 2
    assert worker.killed is True
    assert worker.calls == [b'{"hook_event_name":"PreToolUse"}', None]
    assert json.loads(capsys.readouterr().out) == {"name": "PreToolUse", "code": "BUSY"}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"[]", "INVALID_REQUEST"),
        (b'{"hook_event_name":1}', "INVALID_REQUEST"),
        (b"{}", "INVALID_REQUEST"),
    ],
)
def test_runner_rejects_malformed_envelope_before_worker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: bytes,
    code: str,
) -> None:
    """Keep malformed input away from process creation.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
        raw: Malformed native hook payload.
        code: Stable error code selected by the runner.
    """
    # Invalid top-level input must stop before process creation.
    input_bytes(monkeypatch, raw)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == code


def test_runner_rejects_invalid_worker_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Turn an unknown worker exit status into a recovery diagnostic.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # A worker exit outside the protocol's statuses becomes recovery-required.
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    install_worker(monkeypatch, Worker(returncode=9))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out) == {
        "name": "PreToolUse",
        "code": "RECOVERY_REQUIRED",
    }


def test_worker_main_rejects_unknown_entry_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid importing adapters for malformed worker arguments.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
    """
    # Reject an unsupported worker name before importing adapter code.
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "unknown"])
    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _name: pytest.fail("unexpected import")
    )
    assert runner.main() == 2


def test_worker_main_returns_translated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit one JSON line from the selected adapter handler.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
    """
    # Bind in-memory stdin/stdout around a cursor handler selected by import.
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"a":1}')))
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda name: SimpleNamespace(handle=lambda event: {"a": event["a"] + 1}),
    )
    # The worker emits exactly one translated JSON line.
    assert runner.main() == 0
    assert output.getvalue() == b'{"a":2}\n'


def test_runner_rejects_oversized_input_before_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stop after one byte beyond the bounded hook input limit.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # One byte beyond the bound is rejected before spawning a worker.
    input_bytes(monkeypatch, b"x" * (runner.INPUT_LIMIT + 1))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "SIZE_LIMIT"


def test_runner_reports_stdin_read_failure_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a failed host pipe read within the bounded recovery response.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # A host pipe failure must return a bounded response without process creation.
    monkeypatch.setattr(runner.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(runner.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("pipe")))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_REQUIRED"


def test_runner_preserves_worker_failure_status_and_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a worker's documented failure result and host-specific exit code.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
        capsys: Captures the runner's JSON stdout and diagnostic stderr.
    """
    # A documented worker failure keeps its JSON while the host maps exit status.
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    install_worker(monkeypatch, Worker(output=b'{"decision":"deny"}', returncode=2))
    assert runner.run(handler, failure, error_status=0) == 0
    assert json.loads(capsys.readouterr().out) == {"decision": "deny"}


def test_worker_main_renders_bounded_handler_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a public diagnostic without serializing handler exception details.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
    """
    # Make the adapter handler raise private details inside the worker process.
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(
        runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"hook_event_name":"preToolUse"}'))
    )
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            handle=lambda _event: (_ for _ in ()).throw(OSError("private detail")),
            failure=lambda name, code: {"name": name, "code": code},
        ),
    )
    # Only the public recovery code may cross the worker output boundary.
    assert runner.main() == 2
    assert json.loads(output.getvalue()) == {"name": "preToolUse", "code": "RECOVERY_REQUIRED"}


def test_worker_main_bounds_oversized_handler_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace an oversized native result with a small host failure response.

    Args:
        monkeypatch: Replaces stdin, worker process, or adapter import boundaries.
    """
    # Force a handler response beyond the native output limit.
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(
        runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"hook_event_name":"preToolUse"}'))
    )
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            handle=lambda _event: {"text": "x" * (runner.INPUT_LIMIT + 1)},
            failure=lambda name, code: {"name": name, "code": code},
        ),
    )
    # Replace oversized content with the bounded host failure shape.
    assert runner.main() == 2
    assert json.loads(output.getvalue()) == {"name": "preToolUse", "code": "SIZE_LIMIT"}


def cursor_content_handler(_event: dict) -> dict:
    """Select the fixed Cursor worker in tests without invoking host behavior.

    Args:
        _event: Unused supervisor routing input.

    Returns:
        Empty synthetic result.
    """
    return {}


cursor_content_handler.__module__ = "nplet.adapters.cursor"


@pytest.mark.parametrize("observer", [False, True])
def test_content_input_budget_requires_fixed_entry_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], observer: bool
) -> None:
    """Only the fixed content observer accepts an input above the ordinary hook cap.

    Args:
        monkeypatch: Bounded input and worker process replacements.
        capsys: Public response capture.
        observer: Fixed entry point selection, never taken from JSON.
    """
    monkeypatch.setattr(runner, "INPUT_LIMIT", 128)
    monkeypatch.setattr(runner, "CONTENT_INPUT_LIMIT", 1024)
    raw = json.dumps(
        {
            "hook_event_name": "beforeReadFile",
            "content": "x" * 200,
            "_cursor_deadline": 999999999999,
        }
    ).encode()
    input_bytes(monkeypatch, raw)
    worker = Worker(output=b'{"permission":"deny"}', returncode=2)
    starts = install_worker(monkeypatch, worker)
    # Exact handler denial still wins; the observer never converts it into permission.
    assert runner.run(cursor_content_handler, failure, content_observer=observer) == 2
    result = json.loads(capsys.readouterr().out)
    if observer:
        assert result == {"permission": "deny"}
        assert starts[0][-3:] == ["--worker", "cursor", "--observe-read-content"]
        forwarded = json.loads(worker.calls[0])
        assert forwarded["content"] == "x" * 200
        assert forwarded["_cursor_deadline"] != 999999999999
    else:
        assert result["code"] == "SIZE_LIMIT"
        assert starts == []


def test_content_overflow_drains_without_starting_mutation_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discard an oversized observation and return no source verification metadata.

    Args:
        monkeypatch: Input cap, pipe and process boundary replacements.
        capsys: Public response capture.
    """
    monkeypatch.setattr(runner, "CONTENT_INPUT_LIMIT", 128)
    chunks = iter([b"x" * 129, b"rest", b""])
    reads = []

    def read(_fd: int, size: int) -> bytes:
        """Supply bounded pipe segments and record drain requests.

        Args:
            _fd: Ignored pipe descriptor.
            size: Maximum requested chunk size.

        Returns:
            Next segment or EOF.
        """
        reads.append(size)
        return next(chunks)

    monkeypatch.setattr(runner.os, "read", read)
    monkeypatch.setattr(runner.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(cursor_content_handler, failure, content_observer=True) == 0
    assert json.loads(capsys.readouterr().out) == {"permission": "allow"}
    assert reads == [129, 65536, 65536]


def test_content_worker_timeout_reaps_before_observational_allow(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeout alone grants no proof and leaves no mutation worker alive.

    Args:
        monkeypatch: Input and process boundaries.
        capsys: Public response capture.
    """
    input_bytes(monkeypatch, b'{"hook_event_name":"beforeReadFile"}')
    worker = Worker(timeout=True)
    install_worker(monkeypatch, worker)
    assert runner.run(cursor_content_handler, failure, content_observer=True) == 0
    assert worker.killed and worker.calls[-1] is None
    assert json.loads(capsys.readouterr().out) == {"permission": "allow"}


def test_fixed_content_mode_rejects_an_authorizing_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The observational invocation cannot be reused to allow generic tool admission.

    Args:
        monkeypatch: Input and process boundaries.
        capsys: Public response capture.
    """
    input_bytes(monkeypatch, b'{"hook_event_name":"preToolUse"}')
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(cursor_content_handler, failure, content_observer=True) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("observer", [False, True])
def test_worker_content_mode_uses_its_own_input_cap(
    monkeypatch: pytest.MonkeyPatch, observer: bool
) -> None:
    """The worker independently validates the selected mode and its input budget.

    Args:
        monkeypatch: Process streams, input caps and adapter import boundary.
        observer: Exact fixed worker mode versus ordinary worker.
    """
    monkeypatch.setattr(runner, "INPUT_LIMIT", 128)
    monkeypatch.setattr(runner, "CONTENT_INPUT_LIMIT", 1024)
    argv = ["runner", "--worker", "cursor"] + (["--observe-read-content"] if observer else [])
    monkeypatch.setattr(runner.sys, "argv", argv)
    raw = json.dumps({"hook_event_name": "beforeReadFile", "content": "x" * 400}).encode()
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    received = []

    def content(event: dict) -> dict:
        """Record the actual parsed input without creating lifecycle evidence.

        Args:
            event: Worker-decoded event.

        Returns:
            Non-authorizing observation result.
        """
        received.append(event)
        return {"permission": "allow"}

    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _name: SimpleNamespace(handle=content, failure=failure),
    )
    assert runner.main() == (0 if observer else 2)
    result = json.loads(output.getvalue())
    if observer:
        assert result == {"permission": "allow"}
        assert received[0]["content"] == "x" * 400
    else:
        assert result["code"] == "SIZE_LIMIT"
        assert received == []


@pytest.mark.parametrize("observer", [False, True])
def test_runner_does_not_spawn_after_serialization_deadline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], observer: bool
) -> None:
    """Do not grant a worker a new time budget after synchronous input processing.

    Args:
        monkeypatch: Input, clock, serialization and process boundaries.
        capsys: Public response capture.
        observer: Fixed observer fallback or ordinary authorizing hook.
    """
    # Simulate expensive canonicalization without a slow or large timing test.
    clock = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    original = runner.core.canonical

    def serialize(event: object) -> bytes:
        """Serialize exactly while advancing the modeled shared deadline.

        Args:
            event: Parsed event with supervisor-owned deadline.

        Returns:
            Canonical bytes after the simulated processing time.
        """
        result = original(event)
        clock[0] = runner.DEADLINE_SECONDS + 1
        return result

    monkeypatch.setattr(runner.core, "canonical", serialize)
    event = "beforeReadFile" if observer else "preToolUse"
    input_bytes(monkeypatch, json.dumps({"hook_event_name": event}).encode())
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("late worker")
    )
    assert runner.run(cursor_content_handler, failure, content_observer=observer) == (
        0 if observer else 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result == ({"permission": "allow"} if observer else {"name": event, "code": "BUSY"})
