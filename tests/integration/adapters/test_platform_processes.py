"""Exercise real native-shell transports and bounded hook workers in disposable copies."""

import inspect
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from coverage import Coverage

from nplet.adapters import common, runner
from tests.platform_support import environment_python, link_directory, shell_command
from tests.support import ROOT

pytestmark = pytest.mark.integration

HOSTS = [("codex", "PreToolUse", 0), ("claude", "PreToolUse", 2), ("cursor", "preToolUse", 2)]


def mutated_source_environment(source: Path) -> dict[str, str]:
    """Isolate mutated fixture code from the production coverage collector.

    Args:
        source: Copied source tree containing an injected fault handler.

    Returns:
        Inherited process environment without coverage's two child-startup requests.
    """
    # Mutated copies are behavioral fixtures, not evidence for the submitted source bytes.
    environment = {**os.environ, "PYTHONPATH": str(source)}
    environment.pop("COVERAGE_PROCESS_CONFIG", None)
    environment.pop("COVERAGE_PROCESS_START", None)
    return environment


@pytest.mark.parametrize("fault", ["oversized", "exception"])
def test_mutated_fixture_does_not_pollute_production_coverage(tmp_path: Path, fault: str) -> None:
    """Keep copied fault-injection code out of real subprocess coverage evidence.

    Args:
        tmp_path: Disposable coverage configuration, database and copied fixture.
        fault: Existing process scenario whose mutated source must remain unmeasured.

    Raises:
        AssertionError: The fault scenario fails or copied source enters production evidence.
    """
    # Start a separate collector so this test cannot alter the suite's coverage session.
    config = tmp_path / "coverage.toml"
    database = tmp_path / ".coverage"
    config.write_text(
        "[tool.coverage.run]\nbranch = true\nparallel = true\n"
        'relative_files = true\nsource = ["nplet"]\npatch = ["subprocess"]\n',
        encoding="utf-8",
    )
    fixture = tmp_path / "mutated"
    fixture.mkdir()
    environment = {**os.environ, "COVERAGE_FILE": str(database)}
    environment.pop("COVERAGE_PROCESS_CONFIG", None)
    environment["COVERAGE_PROCESS_START"] = str(config)
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(ROOT / "src")))
    # Run the actual fault fixture, then a genuine production child as a positive control.
    program = (
        "import subprocess, sys\nfrom pathlib import Path\n"
        "from tests.integration.adapters import test_platform_processes as cases\n"
        "fixture = Path(sys.argv[1])\n"
        "if sys.argv[2] == 'oversized':\n"
        "    cases.test_supervisor_rejects_oversized_worker_output(fixture)\n"
        "else:\n"
        "    cases.test_supervisor_reaps_worker_and_preserves_failure_contract(\n"
        "        'codex', 'PreToolUse', 0, 'exception', fixture)\n"
        "result = subprocess.run([sys.executable, '-m', 'nplet.adapters.runner'])\n"
        "assert result.returncode == 2\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(fixture), fault],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Inspect the raw combined database, without deleting or remapping foreign sources.
    coverage = Coverage(data_file=str(database), config_file=False)
    coverage.combine(strict=True)
    data = coverage.get_data()
    try:
        measured = {name.replace("\\", "/"): name for name in data.measured_files()}
        assert measured
        assert all(name.startswith("src/nplet/") for name in measured), sorted(measured)
        # Import-only arcs cannot satisfy the positive control: main runs only in the child.
        lines, start = inspect.getsourcelines(runner.main)
        rejection = start + next(i for i, line in enumerate(lines) if line.strip() == "return 2")
        assert rejection in (data.lines(measured["src/nplet/adapters/runner.py"]) or [])
    finally:
        # Release SQLite handles before Windows removes the disposable directory.
        data.close()


@pytest.mark.parametrize(("host", "event", "status"), HOSTS)
def test_hook_bounds_malformed_and_open_stdin(
    host: str, event: str, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject malformed bytes and an unclosed input pipe using the native process deadline.

    Args:
        host: Actual adapter entry point.
        event: Host event name retained in the parameter set for worker tests.
        status: Host's documented failure exit convention.
        monkeypatch: Scoped removal of imported-Claude skip state.

    Raises:
        AssertionError: Input handling hangs, crashes or loses the host failure convention.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    argv = [sys.executable, "-m", "nplet.adapters." + host]
    malformed = subprocess.run(argv, input="{", capture_output=True, text=True, timeout=6)
    assert malformed.returncode == status, malformed.stderr
    assert isinstance(json.loads(malformed.stdout), dict)
    assert "Traceback" not in malformed.stderr
    # Invalid event-name types must never escape failure rendering as unhashable keys.
    for event_name in ([], {}, None, 1):
        invalid = subprocess.run(
            argv,
            input=json.dumps({"hook_event_name": event_name}),
            capture_output=True,
            text=True,
            timeout=6,
        )
        assert invalid.returncode == status, invalid.stdout + invalid.stderr
        assert isinstance(json.loads(invalid.stdout), dict)
        assert "INVALID_REQUEST" in invalid.stdout + invalid.stderr
        assert "Traceback" not in invalid.stderr
    # Keep stdin open after a partial envelope. The supervisor must exit without waiting for EOF.
    with subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        assert process.stdin is not None
        process.stdin.write(json.dumps({"hook_event_name": event}).encode()[:-1])
        process.stdin.flush()
        # An unterminated input pipe must still produce a bounded exit.
        try:
            process.wait(timeout=6)
        # Kill a hung hook so the regression fails without blocking the suite.
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail("Hook did not bound an open stdin pipe")
        finally:
            # Close the input pipe after the bounded process check.
            process.stdin.close()
        assert process.stdout is not None and process.stderr is not None
        output, errors = process.stdout.read(), process.stderr.read()
    assert process.returncode == status, errors
    assert isinstance(json.loads(output), dict)
    assert b"BUSY" in errors


@pytest.mark.parametrize(("host", "event", "status"), HOSTS)
@pytest.mark.parametrize("fault", ["late_write", "exception"])
def test_supervisor_reaps_worker_and_preserves_failure_contract(
    host: str, event: str, status: int, fault: str, tmp_path: Path
) -> None:
    """Inject worker behavior in copied source while retaining the real process supervisor.

    Args:
        host: Adapter copied into an isolated source tree.
        event: Host-specific event name.
        status: Expected native failure exit convention.
        fault: Controlled worker timeout or lifecycle exception.
        tmp_path: Disposable source and sentinel location.

    Raises:
        AssertionError: A timed-out worker survives or an exception loses its diagnostic.
    """
    source = tmp_path / "src"
    shutil.copytree(ROOT / "src", source, ignore=shutil.ignore_patterns("__pycache__"))
    path = source / "nplet/adapters" / (host + ".py")
    sentinel = tmp_path / "late-write"
    started = tmp_path / "worker-started"
    # Choose a worker that writes late or one that loops forever.
    if fault == "late_write":
        body = (
            "    import time\n    from pathlib import Path\n"
            f"    Path({str(started)!r}).write_bytes(b'started')\n"
            "    time.sleep(3)\n"
            f"    Path({str(sentinel)!r}).write_bytes(b'escaped timeout')\n"
            "    return {}\n"
        )
    else:
        # The other fault raises a synchronous lifecycle conflict.
        body = '    raise core.WorkspaceError("BINDING_CONFLICT")\n'
    original = path.read_text(encoding="utf-8")
    marker = '\nif __name__ == "__main__":'
    assert marker in original
    path.write_text(
        original.replace(marker, "\ndef handle(event, *, deadline=None):\n" + body + marker),
        encoding="utf-8",
    )
    environment = mutated_source_environment(source)
    environment.pop("CURSOR_VERSION", None)
    # Inject only the copied handler. Processes, deadlines and termination remain production code.
    result = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps({"hook_event_name": event}),
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == status, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert isinstance(response, dict)
    # A valid pre-tool envelope needs an explicit denial, even when Codex exits successfully.
    if host == "cursor":
        assert response["permission"] == "deny"
    else:
        # Claude reports denial in its hook-specific response.
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    # The late-write case must start its worker before timeout enforcement.
    if fault == "late_write":
        assert started.exists(), "The mutation worker must actually start before the deadline"
        assert "BUSY" in result.stderr
        # An escaped worker would write within three seconds of its recorded start.
        time.sleep(3.2)
        assert not sentinel.exists()
    else:
        # Synchronous failure must surface its lifecycle conflict.
        assert "BINDING_CONFLICT" in result.stdout


def test_supervisor_rejects_oversized_worker_output(tmp_path: Path) -> None:
    """The real worker transport bounds a handler response before host publication.

    Args:
        tmp_path: Disposable directory for repository or file fixtures.
    """
    source = tmp_path / "src"
    shutil.copytree(ROOT / "src", source, ignore=shutil.ignore_patterns("__pycache__"))
    adapter = source / "nplet/adapters/codex.py"
    original = adapter.read_text(encoding="utf-8")
    marker = '\nif __name__ == "__main__":'
    assert marker in original
    adapter.write_text(
        original.replace(
            marker,
            '\ndef handle(event):\n    return {"oversized": "x" * (1024 * 1024)}\n' + marker,
        ),
        encoding="utf-8",
    )
    environment = mutated_source_environment(source)
    result = subprocess.run(
        [sys.executable, str(adapter)],
        input=json.dumps({"hook_event_name": "PreToolUse"}),
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "SIZE_LIMIT" in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(result.stdout) < 2048


@pytest.mark.skipif(
    os.name != "nt", reason="Native Windows PowerShell transport; POSIX has shell tests"
)
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_windows_bootstrap_survives_literal_paths_and_json(
    shell: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute encoded bootstrap through both native PowerShell versions with quoted paths.

    Args:
        shell: Required PowerShell 5.1 or 7 executable on the Windows validation runner.
        tmp_path: Disposable Git checkout with an apostrophe and spaces.
        monkeypatch: Scoped encoder path replacement pointing only into the fixture.

    Raises:
        AssertionError: A native shell corrupts the request or interprets literal metacharacters.
    """
    assert shutil.which(shell), f"Required native shell missing: {shell}"
    root = tmp_path / "checkout O'Brien with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    link_directory(root / ".venv", Path(sys.prefix))
    script = root / "bootstrap helper.py"
    shutil.copy2(ROOT / "src/nplet/adapters/bootstrap.py", script)
    monkeypatch.setattr(common, "PYTHON", str(environment_python(root / ".venv")))
    monkeypatch.setattr(common, "WINDOWS_BOOTSTRAP", script)
    request = {
        "schema_version": 1,
        "operation": "diagnose",
        "request_id": "native-shell-fixture",
        "worktree": str(root),
        "host": "codex",
        "session_id": "O'Brien-$HOME-`literal`",
    }
    command = common.bootstrap_command(request, "codex")
    assert common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE) == request
    result = subprocess.run(
        shell_command(shell, command), capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["ok"] is True and response["code"] == "REGISTRATION_REQUIRED"
    assert not (root / ".task").exists()


@pytest.mark.parametrize("open_pipe", [False, True])
def test_content_observer_bounds_oversize_and_open_pipe(tmp_path: Path, open_pipe: bool) -> None:
    """Exercise actual subprocess input fallback without claiming Cursor honors its output.

    Args:
        tmp_path: Disposable working directory, unrelated to any registered task.
        open_pipe: Keep stdin open past the deadline instead of ending oversized input.
    """
    # Lower only the fixture's byte threshold; retain the production deadline and process code.
    program = (
        "from functools import partial\n"
        "from nplet.adapters import cursor, runner\n"
        "runner.CONTENT_INPUT_LIMIT = 2048\n"
        "raise SystemExit(runner.run(cursor.handle, "
        "partial(cursor.failure, content_observer=True), content_observer=True))\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        assert process.stdin is not None
        # A still-open native transport must not create a lifecycle worker or hang forever.
        if open_pipe:
            process.stdin.write(b'{"hook_event_name":"beforeReadFile"}')
            process.stdin.flush()
            process.wait(timeout=6)
            process.stdin.close()
            process.stdin = None
            output, errors = process.communicate(timeout=1)
        else:
            output, errors = process.communicate(b"x" * 4096, timeout=6)
        assert process.returncode == 0, errors.decode()
        assert json.loads(output) == {"permission": "allow"}
    assert not (tmp_path / ".task").exists()
