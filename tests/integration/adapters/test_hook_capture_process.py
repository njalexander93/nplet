"""Exercise diagnostic capture through real supervised adapter subprocesses on POSIX."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nplet.adapters import hook_capture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="POSIX observer"),
]
ROOT = Path(__file__).resolve().parents[3]


def invoke(event: dict[str, object], destination: Path | None) -> subprocess.CompletedProcess[str]:
    """Run a synthetic protocol event through the actual supervised Codex adapter.

    Args:
        event: Explicit synthetic fixture; never claimed as installed-host capture.
        destination: Optional private observation directory.

    Returns:
        The completed native adapter command result.
    """
    # Preserve the default interpreter and guard; vary only the diagnostic environment.
    env = os.environ.copy()
    env.pop(hook_capture.ENVIRONMENT, None)
    if destination is not None:
        env[hook_capture.ENVIRONMENT] = str(destination)
    return subprocess.run(
        [sys.executable, "-m", "nplet.adapters.codex"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
        check=False,
    )


def test_diagnostic_preserves_real_guard_denial_and_result(tmp_path: Path) -> None:
    """Keep the existing child denial unchanged with capture enabled or unavailable.

    Args:
        tmp_path: Disposable private evidence directories.
    """
    # A native object input reaches the launch-shape denial before any issue write.
    event = {
        "cwd": str(ROOT),
        "session_id": "capture-fixture",
        "hook_event_name": "PreToolUse",
        "tool_name": "spawn_agent",
        "tool_use_id": "denied-tool",
        "tool_input": {},
    }
    destination = tmp_path / "capture"
    destination.mkdir(mode=0o700)
    baseline = invoke(event, None)
    captured = invoke(event, destination)
    unavailable = invoke(event, tmp_path / "absent")
    assert (captured.returncode, captured.stdout, captured.stderr) == (
        baseline.returncode,
        baseline.stdout,
        baseline.stderr,
    )
    assert (unavailable.returncode, unavailable.stdout, unavailable.stderr) == (
        baseline.returncode,
        baseline.stdout,
        baseline.stderr,
    )
    assert "HOST_UNSUPPORTED_CHILD_IDENTITY" in captured.stdout
    records = [json.loads(p.read_text()) for p in destination.glob("event-*.json")]
    assert len(records) == 1
    assert records[0]["adapter_decisions"] == {"permissionDecision": "deny"}
    assert records[0]["native"]["tool_use_id"] == "denied-tool"


def test_writer_bounds_slots_and_private_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use atomic fixed slots so concurrent callers cannot exceed the configured log cap.

    Args:
        tmp_path: Disposable evidence directory.
        monkeypatch: Fixture shrinking the slot limit for the exhaustion test.
    """
    # Exhaust a small bounded directory and verify prior observations remain intact.
    tmp_path.chmod(0o700)
    monkeypatch.setattr(hook_capture, "FILE_LIMIT", 2)
    hook_capture.write_record(tmp_path, b"one")
    hook_capture.write_record(tmp_path, b"two")
    with pytest.raises(OSError, match="exhausted"):
        hook_capture.write_record(tmp_path, b"three")
    files = sorted(tmp_path.iterdir())
    assert [p.read_bytes() for p in files] == [b"one", b"two"]
    assert all(p.stat().st_mode & 0o077 == 0 for p in files)


def test_overlapping_synthetic_events_remain_distinct(tmp_path: Path) -> None:
    """Start three real adapters together and retain distinct correlation metadata.

    Args:
        tmp_path: Shared private evidence destination.
    """
    # A barrier overlaps the starts; these fixtures are not native sibling acceptance.
    tmp_path.chmod(0o700)
    barrier = threading.Barrier(3)

    def run(index: int) -> subprocess.CompletedProcess[str]:
        """Release one synthetic adapter invocation with its siblings.

        Args:
            index: Unique synthetic event identity.

        Returns:
            Original completed adapter result.
        """
        barrier.wait(timeout=3)
        return invoke(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "parent-fixture",
                "agent_id": f"00000000-0000-4000-8000-{index:012d}",
                "cwd": str(tmp_path),
                "tool_use_id": f"tool-{index}",
            },
            tmp_path,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    records = [json.loads(p.read_text()) for p in tmp_path.glob("event-*.json")]
    assert {r["native"]["agent_id"] for r in records} == {
        f"00000000-0000-4000-8000-{i:012d}" for i in range(3)
    }
    assert {r["native"]["tool_use_id"] for r in records} == {f"tool-{i}" for i in range(3)}
    assert all(r.returncode == 0 and json.loads(r.stdout) == {} for r in results)
