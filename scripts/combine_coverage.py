"""Combine successful Windows and Linux evidence at the configured coverage floor."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dev  # noqa: E402
from scripts.coverage_evidence import valid_matrix  # noqa: E402


def main() -> int:
    """Combine native data and enforce the unchanged repository aggregate policy.

    Returns:
        Zero when unchanged combined coverage reaches the configured floor, otherwise one.

    Raises:
        ValueError: The combining checkout is dirty or native provenance does not match.
    """
    # Parse the artifact location and destination for the standalone command.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    # Bind combination to the current clean checkout before loading evidence.
    os.chdir(ROOT)
    initial = dev.checkout_state()
    # Reject local edits because the native candidate must match source bytes.
    if initial["status"]:
        raise ValueError("Combining checkout must be clean")
    # Validate every native suite and its exact revision before combination.
    records = valid_matrix(
        args.artifacts.resolve(),
        sha=initial["sha"],
        tracked_digest=initial["tracked_digest"],
        config_digest=hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest(),
        tool_versions={
            name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
        },
        systems={"Windows", "Linux"},
        allow_full=True,
        source_root=ROOT,
    )
    # Combine the validated databases and retain machine-readable reports.
    inputs = [str(path) for path in records.values()]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    coverage = Coverage(
        data_file=str(output / ".coverage"), config_file=str(ROOT / "pyproject.toml")
    )
    coverage.combine(inputs, strict=True, keep=True)
    coverage.save()
    total = coverage.report()
    coverage.xml_report(outfile=str(output / "coverage.xml"))
    coverage.json_report(outfile=str(output / "coverage.json"), show_contexts=True)
    floor = coverage.get_option("report:fail_under")
    # Reject an invalid configured floor instead of silently weakening it.
    if not isinstance(floor, (int, float)) or isinstance(floor, bool):
        raise ValueError("Coverage floor must be numeric")
    # Recheck checkout identity and record whether the candidate stayed unchanged.
    final = dev.checkout_state()
    unchanged = initial == final
    result = {
        **initial,
        **{"end_" + key: value for key, value in final.items()},
        "candidate_unchanged": unchanged,
        "platforms": ["Windows", "Linux"],
        "inputs": inputs,
        "coverage_percent": total,
        "required_percent": floor,
        "passed": unchanged and total >= floor,
    }
    # Persist the decision and return a failing status when the floor is missed.
    (output / "combined.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
