"""Validate one exact native suite record before combining or quality analysis."""

import hashlib
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, cast

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

ROOT = Path(__file__).resolve().parents[1]

SYSTEMS = {"Linux", "Windows", "Darwin"}
SUITES = {
    "test": "test",
    "test-unit": "unit",
    "test-integration": "integration",
    "test-tooling-unit": "tooling-unit",
}
ARTIFACTS = {
    ".coverage",
    "coverage.xml",
    "coverage.json",
    "tests.xml",
    "pytest-evidence.json",
    "instrumentation.json",
}
PROBE_LINES = {1, 2, 3, 4}
PROBE_ARCS = {(2, 3), (2, 4)}
WINDOWS_FILESYSTEM = "tests/integration/lifecycle/test_filesystem.py::"
WINDOWS_NONCANONICAL_PARAMS = (
    "C:relative",
    r"\\server\share\store",
    r"\\?\C:\store",
    r"\\.\pipe\store",
    r"C:\store:stream",
    r"C:\..\store",
)
WINDOWS_UNIT_NODES = (
    "tests/unit/lifecycle/test_windows_operations.py::test_child_create_success_opens_and_checks_new_directory",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_closes_native_handle_when_crt_adoption_fails",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_leaves_no_cleanup_after_successful_publication",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_rejects_unexpected_publish_failure_and_removes_temp",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_after_publish_checks_new_view_without_unlinking_target",
    "tests/unit/lifecycle/test_windows_contracts.py::test_native_layout_and_declarations",
    "tests/unit/lifecycle/test_windows_contracts.py::test_api_sets_native_signature",
    "tests/unit/lifecycle/test_windows_contracts.py::test_checked_preserves_native_error",
    "tests/unit/lifecycle/test_windows_contracts.py::test_open_handle_uses_non_delete_sharing_and_security",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[wrong-type]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[reparse]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[hard-link]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[zero-index]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_allows_explicit_reparse_inspection",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_surfaces_native_query_failure",
    "tests/unit/lifecycle/test_windows_contracts.py::test_exact_handle_path_rejects_alias_and_truncation",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_init_releases_pins_on_validation_failure",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_context_and_repeated_close",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_entry_rejects_closed_and_case_alias",
    "tests/unit/lifecycle/test_windows_contracts.py::test_names_exists_and_flush_use_pinned_directory",
    "tests/unit/lifecycle/test_windows_contracts.py::test_child_closes_result_when_private_acl_fails",
    "tests/unit/lifecycle/test_windows_contracts.py::test_file_releases_handle_after_identity_rejection",
    "tests/unit/lifecycle/test_windows_contracts.py::test_read_rejects_declared_oversize_before_handle_transfer",
    "tests/unit/lifecycle/test_windows_contracts.py::test_unlink_and_rmdir_validate_before_deletion",
    "tests/unit/lifecycle/test_windows_contracts.py::test_rename_rejects_existing_target_without_move",
    "tests/unit/lifecycle/test_windows_contracts.py::test_lock_releases_handle_on_body_error",
    "tests/unit/lifecycle/test_windows_contracts.py::test_lock_timeout_keeps_persistent_inode",
    "tests/unit/lifecycle/test_windows_failures.py::test_extended_path_uses_local_namespace",
    "tests/unit/lifecycle/test_windows_failures.py::test_open_handle_surfaces_native_failure_without_accepting_invalid_handle",
    "tests/unit/lifecycle/test_windows_failures.py::test_information_rejects_non_disk_handle",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_rejects_nonjunction_and_closes_handle",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_rejects_foreign_reparse_payload",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_accepts_exact_mount_point_data",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_creation_race_removes_only_temporary_junction",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_rejects_remote_drive_before_query",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_rejects_unc_namespace_before_kernel_query",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[NTFS-136-True]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[FAT32-136-False]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[NTFS-128-False]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_surfaces_native_information_failure",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_pins_each_ancestor_and_releases_after_failure",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_retains_chain_and_init_records_identity",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_rejects_device_component_before_opening",
    "tests/unit/lifecycle/test_windows_operations.py::test_child_create_preserves_existing_and_checks_private_acl",
    "tests/unit/lifecycle/test_windows_operations.py::test_file_opens_exclusive_private_handle",
    "tests/unit/lifecycle/test_windows_operations.py::test_read_validated_bytes_and_rejects_stream_growth",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_flushes_before_publish_and_cleans_temp",
    "tests/unit/lifecycle/test_windows_operations.py::test_rename_rejects_cross_volume_and_moves_only_after_check",
    "tests/unit/lifecycle/test_windows_operations.py::test_lock_reopens_persistent_inode_and_retries_contention[True]",
    "tests/unit/lifecycle/test_windows_operations.py::test_lock_reopens_persistent_inode_and_retries_contention[False]",
    "tests/unit/lifecycle/test_windows_operations.py::test_existing_only_lock_cannot_create_an_absent_file",
    "tests/unit/lifecycle/test_windows_operations.py::test_lock_surfaces_noncontention_error_and_releases_handle",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_releases_temp_after_failed_publication",
    "tests/unit/lifecycle/test_windows_operations.py::test_child_create_surfaces_native_denial_without_opening",
    "tests/unit/lifecycle/test_windows_operations.py::test_read_closes_handle_when_descriptor_transfer_fails",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_validates_existing_destination_before_temporary_creation",
    "tests/unit/lifecycle/test_windows_operations.py::test_unlink_surfaces_delete_denial_after_verified_handle_close",
    "tests/unit/lifecycle/test_windows_operations.py::test_rmdir_surfaces_native_denial_after_child_check",
    "tests/unit/lifecycle/test_windows_security.py::test_sid_text_frees_native_string",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_uses_requested_class_and_closes_token",
    "tests/unit/lifecycle/test_windows_security.py::test_private_security_protects_descriptor_and_frees_it",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-False-0-0-0-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[owner-True-0-0-1073741824-user-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-0-1073741824-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-8-1073741824-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-0-1-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-1-0-1073741824-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-9-0-0-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[foreign-False-0-0-0-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_rejects_null_dacl",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_reports_native_query_code",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_releases_descriptor_when_ace_read_fails",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_closes_token_after_information_failure",
    "tests/unit/lifecycle/test_windows_security.py::test_private_security_rejects_descriptor_conversion_failure",
    "tests/unit/lifecycle/test_windows_security.py::test_sid_text_rejects_conversion_failure_without_free",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_rejects_token_open_failure_without_close",
)
POSIX_UNIT_NODES = (
    "tests/unit/lifecycle/test_posix_contracts.py::test_absolute_walks_components_without_following_and_closes_previous",
    "tests/unit/lifecycle/test_posix_contracts.py::test_absolute_closes_active_descriptor_after_component_failure",
    "tests/unit/lifecycle/test_posix_contracts.py::test_child_creation_flushes_parent_and_uses_nofollow_open",
    "tests/unit/lifecycle/test_posix_contracts.py::test_read_rejects_oversized_file_before_byte_read_and_closes",
    "tests/unit/lifecycle/test_posix_contracts.py::test_unlink_flushes_parent_after_deletion",
    "tests/unit/lifecycle/test_posix_contracts.py::test_rename_refuses_occupied_quarantine_before_open_or_move",
    "tests/unit/lifecycle/test_posix_contracts.py::test_issue_view_creates_exact_link_and_flushes_before_validation",
    "tests/unit/lifecycle/test_posix_contracts.py::test_write_cleans_unpublished_temp_after_replace_failure",
    "tests/unit/lifecycle/test_posix_contracts.py::test_lock_rejects_unsafe_existing_lock_and_releases_descriptor[True]",
    "tests/unit/lifecycle/test_posix_contracts.py::test_lock_rejects_unsafe_existing_lock_and_releases_descriptor[False]",
    "tests/unit/lifecycle/test_posix_contracts.py::test_existing_only_lock_cannot_create_an_absent_file",
    "tests/unit/lifecycle/test_posix_contracts.py::test_directory_context_and_names_use_owned_descriptor",
    "tests/unit/lifecycle/test_posix_contracts.py::test_read_returns_exact_bounded_owned_regular_file",
    "tests/unit/lifecycle/test_posix_contracts.py::test_rmdir_validates_direct_child_then_flushes_parent",
    "tests/unit/lifecycle/test_posix_contracts.py::test_lock_creates_private_persistent_file_and_releases_after_body",
    "tests/unit/lifecycle/test_posix_contracts.py::test_rename_directory_validates_source_then_flushes_both_parents",
    "tests/unit/lifecycle/test_posix_contracts.py::test_exists_reports_present_direct_entry_without_following_symlink",
    "tests/unit/lifecycle/test_posix_contracts.py::test_child_reuses_existing_private_directory_after_create_race",
    "tests/unit/lifecycle/test_posix_contracts.py::test_write_publishes_staged_bytes_then_flushes_directory",
    "tests/unit/lifecycle/test_posix_contracts.py::test_lock_retries_blocked_flock_then_enters_with_same_descriptor",
    "tests/unit/lifecycle/test_posix_contracts.py::test_issue_view_validates_racing_existing_exact_link",
    "tests/unit/lifecycle/test_posix_contracts.py::test_child_without_create_never_attempts_mkdir_or_parent_flush",
    "tests/unit/lifecycle/test_posix_contracts.py::test_lock_reports_busy_when_existing_file_disappears_until_deadline",
    "tests/unit/lifecycle/test_posix_failures.py::test_directory_constructor_closes_unsafe_fd",
    "tests/unit/lifecycle/test_posix_failures.py::test_absolute_rejects_relative_or_parent_path_before_open",
    "tests/unit/lifecycle/test_posix_failures.py::test_exists_reports_absent_entry_without_following_link",
    "tests/unit/lifecycle/test_posix_failures.py::test_read_rejects_unsafe_file_and_closes_handle",
    "tests/unit/lifecycle/test_posix_failures.py::test_write_refuses_unsafe_existing_target_before_temp_creation",
    "tests/unit/lifecycle/test_posix_failures.py::test_issue_view_rejects_existing_link_to_different_target",
)
POSIX_UNIT_REASON = "POSIX fcntl and descriptor APIs are unavailable on Windows"
WINDOWS_UNIT_REASON = "Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX"
APPROVED_NATIVE_SKIPS: dict[str, tuple[set[str], str]] = {
    **{node: ({"Windows"}, POSIX_UNIT_REASON) for node in POSIX_UNIT_NODES},
    **{node: ({"Linux", "Darwin"}, WINDOWS_UNIT_REASON) for node in WINDOWS_UNIT_NODES},
    # These real observer processes require POSIX directory descriptors and private modes.
    **{
        "tests/integration/adapters/test_hook_capture_process.py::" + name: (
            {"Windows"},
            "POSIX observer",
        )
        for name in (
            "test_diagnostic_preserves_real_guard_denial_and_result",
            "test_writer_bounds_slots_and_private_permissions",
            "test_overlapping_synthetic_events_remain_distinct",
        )
    },
    **{
        "tests/integration/adapters/test_platform_processes.py::"
        f"test_windows_bootstrap_survives_literal_paths_and_json[{shell}]": (
            {"Linux", "Darwin"},
            "Native Windows PowerShell transport; POSIX has shell tests",
        )
        for shell in ("powershell.exe", "pwsh.exe")
    },
    WINDOWS_FILESYSTEM + "test_windows_case_alias_and_pinned_ancestor": (
        {"Linux", "Darwin"},
        "Requires native Windows NTFS handles and ACLs",
    ),
    WINDOWS_FILESYSTEM + "test_windows_foreign_write_acl_is_rejected": (
        {"Linux", "Darwin"},
        "Requires native Windows security descriptors",
    ),
    **{
        WINDOWS_FILESYSTEM + f"test_windows_noncanonical_roots_are_rejected[{path}]": (
            {"Linux", "Darwin"},
            "Requires native Windows path namespaces",
        )
        for path in (value.replace("\\", "\\\\") for value in WINDOWS_NONCANONICAL_PARAMS)
    },
    WINDOWS_FILESYSTEM + "test_windows_competing_process_cannot_redirect_held_ancestor": (
        {"Linux", "Darwin"},
        "Requires native Windows cross-process sharing",
    ),
    # Link points need symbolic links; Windows junctions are not reported as links.
    **{
        "tests/integration/adapters/test_repository_location.py::" + name: (
            {"Windows"},
            "POSIX symbolic link points",
        )
        for name in (
            "test_c3_symlink_points_carry_their_own_repository",
            "test_c4_links_keep_link_and_destination_repositories",
            "test_c5_failures_are_unresolved_and_linked_ancestors_resolve[symlinked_ancestor]",
            "test_c5_failures_are_unresolved_and_linked_ancestors_resolve[dangling_link]",
            "test_c6_repointed_link_is_followed_on_next_call",
        )
    },
    "tests/integration/lifecycle/test_posix_boundaries.py::"
    "test_held_directory_stays_on_original_inode_after_path_replacement": (
        {"Windows"},
        "Requires native POSIX directory descriptors",
    ),
    "tests/integration/lifecycle/test_posix_boundaries.py::"
    "test_group_writable_file_is_rejected_without_replacement": (
        {"Windows"},
        "Requires native POSIX directory descriptors",
    ),
}


def digest(path: Path) -> str:
    """Return the SHA-256 digest of retained bytes.

    Args:
        path: Retained artifact whose bytes are hashed.

    Returns:
        Hexadecimal SHA-256 digest of the file bytes.

    Raises:
        OSError: The retained artifact cannot be read.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_report_files(report: dict[str, Any]) -> dict[str, Any]:
    """Index hash-verified JSON rows across native separators without changing bytes.

    Args:
        report: Native coverage JSON with measured file rows.

    Returns:
        Report rows keyed by portable source paths.

    Raises:
        ValueError: Coverage rows contain invalid or colliding paths.
    """
    # Require a file-row mapping before normalizing native path separators.
    files = report["files"]
    # Reject a file-row container that cannot be indexed by source path.
    if not isinstance(files, dict):
        raise ValueError("malformed coverage file rows")
    normalized: dict[str, Any] = {}
    # Normalize each measured path while keeping its report data intact.
    for source, row in files.items():
        # Reject empty or non-string source names before indexing them.
        if not isinstance(source, str) or not source:
            raise ValueError("malformed coverage source path")
        path = source.replace("\\", "/")
        # Reject aliases that collapse onto one portable source path.
        if path in normalized:
            raise ValueError(f"duplicate normalized coverage source: {path}")
        normalized[path] = row
    return normalized


def regenerated_report(data: CoverageData, source_root: Path, tooling: bool) -> dict[str, Any]:
    """Analyze actual source from a disposable normalized copy of native arcs.

    Args:
        data: Coverage database containing native measured arcs.
        source_root: Checkout root used to resolve measured source files.
        tooling: Select scripts rather than application source for this receipt.

    Returns:
        Fresh coverage JSON derived from native arcs and checkout source.

    Raises:
        ValueError: Native measured paths escape the checkout or repeat.
    """
    # Resolve only the expected source tree from this checkout.
    root = source_root.resolve()
    prefix = "scripts/" if tooling else "src/nplet/"
    measured: dict[str, str] = {}
    # Regenerate a report in a disposable database without altering native data.
    with tempfile.TemporaryDirectory(prefix="nplet-coverage-") as temporary:
        database = Path(temporary) / ".coverage"
        copy = CoverageData(basename=str(database))
        # Populate the temporary database and always release its handle.
        try:
            copy.add_arcs({})
            # Map each measured native path back to a safe checkout source file.
            for source in data.measured_files():
                normalized = source.replace("\\", "/")
                relative_path = PurePosixPath(normalized)
                # Reject traversals, duplicates, and files outside the selected source tree.
                if (
                    not normalized.startswith(prefix)
                    or relative_path.is_absolute()
                    or ".." in relative_path.parts
                    or normalized in measured
                ):
                    raise ValueError(f"unsafe or duplicate measured source: {source}")
                path = (root / normalized).resolve()
                # Require the normalized source file to exist beneath the checkout.
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError(f"missing or unsafe source file: {source}")
                measured[normalized] = str(path)
                arcs = data.arcs(source) or []
                # Preserve measured branches; mark an unvisited file explicitly.
                if arcs:
                    copy.add_arcs({str(path): arcs})
                else:
                    # Preserve an unvisited source file even when its database has no arcs.
                    copy.touch_file(str(path))
            copy.write()
        finally:
            # Release the disposable writer even when source normalization fails.
            copy.close()
        coverage = Coverage(data_file=str(database), config_file=str(root / "pyproject.toml"))
        # Absolute copy names need matching analysis lookups even when the
        # native collector stored relative names.
        coverage.set_option("run:relative_files", False)
        # Use the tooling source configuration for tooling-only receipts.
        if tooling:
            coverage.set_option("run:source", ["scripts"])
        # Render coverage JSON from the temporary arcs and close the reader.
        try:
            coverage.load()
            output = Path(temporary) / "coverage.json"
            coverage.json_report(outfile=str(output), ignore_errors=False)
            report: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
        finally:
            # Release the report reader before removing its temporary database.
            coverage.get_data().close()
    files: dict[str, Any] = {}
    # Return regenerated rows to portable paths for exact comparison.
    for source, row in normalized_report_files(report).items():
        path = Path(source)
        relative_name = (
            path.resolve().relative_to(root).as_posix() if path.is_absolute() else source
        )
        # Reject duplicate rows after absolute paths become checkout-relative.
        if relative_name in files:
            raise ValueError(f"duplicate regenerated source: {relative_name}")
        files[relative_name] = row
    report["files"] = files
    return report


def valid_suite(
    directory: Path,
    *,
    sha: str,
    tracked_digest: str,
    config_digest: str,
    tool_versions: dict[str, str],
    require_clean: bool = True,
    source_root: Path = ROOT,
) -> tuple[str, str, Path]:
    """Reject stale, partial, failed, uninstrumented or malformed native evidence.

    Args:
        directory: Directory containing the candidate evidence or test output.
        sha: Exact candidate commit expected in every native record.
        tracked_digest: Digest of tracked checkout bytes expected in the receipt.
        config_digest: Digest of the coverage configuration expected in the receipt.
        tool_versions: Installed test-tool versions expected in the receipt.
        require_clean: Whether both recorded checkout states must be clean.
        source_root: Checkout root used to resolve measured source files.

    Returns:
        Native system, suite, and validated coverage database path.

    Raises:
        ValueError: Native evidence is stale, incomplete, malformed, or inconsistent.
    """
    # Load the receipt and translate malformed native artifacts at this boundary.
    manifest_path = directory / "manifest.json"
    # Validate the complete receipt inside one bounded failure-reporting boundary.
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Require a structured manifest before inspecting its command records.
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        commands = manifest["commands"]
        # Reject an empty or malformed command history.
        if (
            not isinstance(commands, list)
            or not commands
            or any(not isinstance(row, dict) for row in commands)
        ):
            raise ValueError("missing or malformed suite commands")
        test_commands = [row for row in commands if row.get("task") in SUITES]
        # Require one and only one selected test-suite command.
        if len(test_commands) != 1:
            raise ValueError("exactly one suite command is required")
        command = test_commands[0]
        suite = SUITES[command["task"]]
        tasks = [row.get("task") for row in commands]
        full_checks = [
            "validate-config",
            "validate-config",
            "format-check",
            "lint",
            "type-check",
            "test",
        ]
        # Distinguish split suite receipts from the legacy full-check sequence.
        if (
            suite != "test"
            and len(commands) != 1
            or suite == "test"
            and tasks not in (["test"], full_checks)
        ):
            raise ValueError("unexpected suite command sequence")
        logs: set[str] = set()
        counts: dict[str, int] = {}
        # Verify every command completed and its expected log name is unique.
        for row in commands:
            task = row["task"]
            log = f"{task}-{counts.get(task, 0)}.log"
            counts[task] = counts.get(task, 0) + 1
            # Reject failed commands, unstable timing, and mismatched log names.
            if (
                row.get("log") != log
                or type(row.get("exit_code")) is not int
                or row["exit_code"] != 0
                or type(row.get("seconds")) not in {int, float}
                or not math.isfinite(row["seconds"])
                or row["seconds"] < 0
            ):
                raise ValueError("failed or malformed suite command")
            logs.add(log)
        system = manifest["system"]
        # Require a supported native platform name.
        if system not in SYSTEMS:
            raise ValueError("unknown native system")
        # Check that the directory agrees with the recorded split suite.
        if suite != "test" and directory.name != suite:
            raise ValueError("suite directory disagrees with command")
        # Bind both recorded commit points to the requested candidate.
        if manifest.get("sha") != sha or manifest.get("end_sha") != sha:
            raise ValueError("stale candidate SHA")
        # Require unchanged tracked bytes across the native run.
        if (
            not tracked_digest
            or manifest.get("tracked_digest") != tracked_digest
            or manifest.get("end_tracked_digest") != tracked_digest
            or manifest.get("candidate_unchanged") is not True
        ):
            raise ValueError("source bytes changed or mismatch")
        # Apply the clean-checkout policy when requested by the caller.
        if require_clean and (manifest.get("status") != "" or manifest.get("end_status") != ""):
            raise ValueError("candidate was not clean")
        # Reject coverage recorded with a different configuration.
        if manifest.get("config_sha256") != config_digest:
            raise ValueError("coverage configuration mismatch")
        # Reject receipts produced by another test-tool version set.
        if manifest.get("tool_versions") != tool_versions:
            raise ValueError("test tool versions mismatch")
        artifacts = manifest.get("artifacts_sha256")
        tooling = suite == "tooling-unit"
        expected_artifacts = (ARTIFACTS - {"instrumentation.json"} if tooling else ARTIFACTS) | logs
        # Add the instrumentation log for application suites.
        if not tooling:
            expected_artifacts.add("instrumentation.log")
        # Require the exact artifact inventory for this suite type.
        if not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
            raise ValueError("missing or unexpected artifact receipt")
        # Verify each retained artifact against the manifest digest.
        for name in expected_artifacts:
            path = directory / name
            # Reject missing files and bytes changed after receipt creation.
            if not path.is_file() or digest(path) != artifacts[name]:
                raise ValueError(f"missing or changed artifact: {name}")
        # Prove child-process line and branch instrumentation for application suites.
        if not tooling:
            smoke = json.loads((directory / "instrumentation.json").read_text(encoding="utf-8"))
            # Reject an incomplete or failed child instrumentation receipt.
            if (
                smoke.get("passed") is not True
                or smoke.get("instrumented") is not True
                or smoke.get("branch_data") is not True
                or type(smoke.get("test_exit_code")) is not int
                or smoke["test_exit_code"] != 0
                or smoke.get("config_sha256") != config_digest
                or set(smoke.get("expected_lines", [])) != PROBE_LINES
                or {tuple(arc) for arc in smoke.get("expected_arcs", [])} != PROBE_ARCS
                or not PROBE_LINES <= set(smoke["child_lines"])
                or not PROBE_ARCS <= {tuple(arc) for arc in smoke["child_arcs"]}
            ):
                raise ValueError("child instrumentation proof failed")
        data = CoverageData(basename=str(directory / ".coverage"))
        # Compare the raw branch database against fresh source-based analysis.
        try:
            data.read()
            # Require measured branch data and at least one source file.
            if not data.has_arcs() or not data.measured_files():
                raise ValueError("missing branch database or measured source")
            report = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
            # Require a branch JSON report from the recorded Coverage version.
            if (
                report.get("meta", {}).get("branch_coverage") is not True
                or report["meta"].get("version") != tool_versions["coverage"]
                or not report.get("files")
            ):
                raise ValueError("missing branch JSON or source rows")
            regenerated = regenerated_report(data, source_root, tooling)
            # Reject report rows or totals that disagree with native arcs.
            if (
                normalized_report_files(report) != regenerated["files"]
                or report["totals"] != regenerated["totals"]
                or {key: value for key, value in report["meta"].items() if key != "timestamp"}
                != {key: value for key, value in regenerated["meta"].items() if key != "timestamp"}
            ):
                raise ValueError("coverage JSON disagrees with source and database")
        finally:
            # Release the native database after success or failed artifact validation.
            data.close()
        xml_root = ET.parse(directory / "coverage.xml").getroot()
        # Require a populated XML report from the same native run.
        if xml_root.tag != "coverage" or not xml_root.findall("packages/package/classes/class"):
            raise ValueError("invalid or empty coverage XML")
        totals = report["totals"]
        # Cross-check XML line and branch totals against regenerated JSON.
        if (
            int(xml_root.attrib["lines-valid"]) != totals["num_statements"]
            or int(xml_root.attrib["lines-covered"]) != totals["covered_lines"]
            or int(xml_root.attrib["branches-valid"]) != totals["num_branches"]
            or int(xml_root.attrib["branches-covered"]) != totals["covered_branches"]
        ):
            raise ValueError("XML totals disagree with JSON")
        junit = ET.parse(directory / "tests.xml").getroot()
        cases = junit.findall(".//testcase")
        # Require JUnit test cases before trusting collection outcomes.
        if junit.tag not in {"testsuite", "testsuites"} or not cases:
            raise ValueError("empty JUnit results")
        outcomes = json.loads((directory / "pytest-evidence.json").read_text(encoding="utf-8"))
        # Require a successful, versioned pytest outcome receipt.
        if outcomes.get("schema") != 1 or outcomes.get("exit_code") != 0:
            raise ValueError("failed or malformed pytest outcome record")
        collected = outcomes.get("collected")
        reports = outcomes.get("reports")
        # Require nonempty collection and per-node phase reports.
        if not isinstance(collected, list) or not collected or not isinstance(reports, dict):
            raise ValueError("zero or malformed collection")
        ids = [item["nodeid"] for item in collected]
        # Reject missing or duplicated collected test identities.
        if len(ids) != len(set(ids)) or set(reports) != set(ids):
            raise ValueError("duplicate or missing outcome IDs")
        suites = [junit] if junit.tag == "testsuite" else junit.findall("testsuite")
        # Reject JUnit failures and missing case rows.
        if len(cases) < len(ids) or any(
            int(row.attrib.get(key, "0")) for row in suites for key in ("errors", "failures")
        ):
            raise ValueError("JUnit outcomes disagree with collection")
        # Treat collection errors as native suite failures.
        if outcomes.get("collection_errors"):
            raise ValueError("collection errors")
        executed_cases = 0
        subtest_count = 0
        full_executed: set[str] = set()
        # Validate marker placement and all recorded phases for each case.
        for item in collected:
            nodeid = item["nodeid"]
            suite_markers = item.get("suite_markers")
            # Use the recorded marker for full-suite case classification.
            if suite == "test":
                marker = (
                    suite_markers[0] if isinstance(suite_markers, list) and suite_markers else None
                )
            else:
                # Require the suite marker when this receipt does not represent the full suite.
                marker = "unit" if tooling else suite
            prefix = "tests/unit/tooling/" if tooling else f"tests/{marker}/"
            # Reject unmarked full-suite cases.
            if suite == "test" and marker not in {"unit", "integration"}:
                raise ValueError(f"unmarked or misclassified test: {nodeid}")
            # Require the node path and marker to agree with its suite.
            if item.get("suite_markers") != [marker] or not nodeid.startswith(prefix):
                raise ValueError(f"unmarked or misclassified test: {nodeid}")
            phases = reports[nodeid]
            # Require a phase record for every collected case.
            if not isinstance(phases, list) or not phases:
                raise ValueError(f"missing test outcome: {nodeid}")
            phase_names = [phase["phase"] for phase in phases]
            primary = [phase["phase"] for phase in phases if "subtest_index" not in phase]
            subtests = [phase for phase in phases if "subtest_index" in phase]
            subtest_count += len(subtests)
            # Reject duplicate phases or inconsistent subtest indexing.
            if (
                len(primary) != len(set(primary))
                or phase_names[0] != "setup"
                or any(phase["phase"] != "call" for phase in subtests)
                or [phase["subtest_index"] for phase in subtests] != list(range(len(subtests)))
            ):
                raise ValueError(f"duplicate or incomplete phases: {nodeid}")
            # Reject failed and expected-failure outcomes.
            if any(phase.get("outcome") == "failed" or "wasxfail" in phase for phase in phases):
                raise ValueError(f"failed or expected-failure outcome: {nodeid}")
            # Permit skips only from the reviewed native skip inventory.
            if any(phase.get("outcome") == "skipped" for phase in phases):
                approved = APPROVED_NATIVE_SKIPS.get(nodeid)
                # Check the exact platform and skip reason for the approved case.
                if (
                    not approved
                    or system not in approved[0]
                    or item.get("skip_reasons") != [approved[1]]
                ):
                    raise ValueError(f"unapproved skip: {nodeid}")
            # Count cases only after a successful call and teardown.
            elif any(
                phase.get("phase") == "call" and phase.get("outcome") == "passed"
                for phase in phases
            ):
                # Require teardown evidence for every executed case.
                if "teardown" not in phase_names:
                    raise ValueError(f"missing teardown outcome: {nodeid}")
                executed_cases += 1
                # Track which suites actually executed in a legacy full run.
                if suite == "test":
                    full_executed.add(cast(str, marker))
            else:
                # Reject a collected test with neither a completed call nor an approved skip.
                raise ValueError(f"no executed outcome: {nodeid}")
        # Reject a suite where every collected case skipped.
        if executed_cases == 0:
            raise ValueError("all tests skipped")
        # Cross-check JUnit totals against cases and subtests.
        if sum(int(row.attrib["tests"]) for row in suites) != len(cases) + subtest_count:
            raise ValueError("JUnit outcomes disagree with collection")
        # Require both unit and integration execution in a full receipt.
        if suite == "test" and full_executed != {"unit", "integration"}:
            raise ValueError("full suite did not execute both unit and integration tests")
        return system, suite, directory / ".coverage"
    # Translate malformed or unreadable artifacts into one rejected receipt.
    except (
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        CoverageException,
        json.JSONDecodeError,
        ET.ParseError,
    ) as error:
        raise ValueError(f"Malformed native evidence: {directory}: {error}") from error


def valid_matrix(
    root: Path,
    *,
    sha: str,
    tracked_digest: str,
    config_digest: str,
    tool_versions: dict[str, str],
    systems: set[str] = SYSTEMS,
    allow_full: bool = False,
    source_root: Path = ROOT,
) -> dict[tuple[str, str], Path]:
    """Require exact split records, or allow one full suite per OS for legacy combine.

    Args:
        root: Root of the candidate evidence tree.
        sha: Exact candidate commit expected in every native record.
        tracked_digest: Digest of tracked checkout bytes expected in the receipt.
        config_digest: Digest of the coverage configuration expected in the receipt.
        tool_versions: Installed test-tool versions expected in the receipt.
        systems: Native operating systems required in the evidence matrix.
        allow_full: Whether a complete legacy full-suite receipt is acceptable.
        source_root: Checkout root used to resolve measured source files.

    Returns:
        Validated coverage database paths keyed by system and suite.

    Raises:
        ValueError: Native suite records are duplicate or incomplete.
    """
    records: dict[tuple[str, str], Path] = {}
    # Validate and index each native manifest in the candidate tree.
    for path in sorted(root.rglob("manifest.json")):
        system, suite, database = valid_suite(
            path.parent,
            sha=sha,
            tracked_digest=tracked_digest,
            config_digest=config_digest,
            tool_versions=tool_versions,
            source_root=source_root,
        )
        key = (system, suite)
        # Reject duplicate platform and suite evidence.
        if key in records:
            raise ValueError(f"Duplicate native suite: {system}/{suite}")
        records[key] = database
    expected = {(system, suite) for system in systems for suite in ("unit", "integration")}
    # Permit the documented legacy full-suite shape only when requested.
    if allow_full:
        complete = (
            all(
                {suite for observed, suite in records if observed == system}
                in ({"test"}, {"unit", "integration"})
                for system in systems
            )
            and {system for system, _ in records} == systems
        )
    else:
        # For strict split evidence, require exactly one record for each expected native suite.
        complete = set(records) == expected
    # Reject a matrix with missing or unexpected native records.
    if not complete:
        raise ValueError(
            f"Incomplete native matrix: missing={sorted(expected - set(records))}; "
            f"unexpected={sorted(set(records) - expected)}"
        )
    return records
