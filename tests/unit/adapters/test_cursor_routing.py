"""Verify exact nonauthorizing Cursor routes and explicit population markers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nplet.adapters import cursor_routing as routing
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "alteration", ["valid", "absent", "version-bool", "complete-int", "foreign"]
)
def test_population_marker_requires_exact_complete_registry(alteration: str) -> None:
    """Refuse incomplete population and replaced directories before pointer lookup.

    Args:
        alteration: Exact marker boundary to validate.
    """
    registry = MagicMock()
    registry.identity = {"device": 1, "inode": 2}
    store = MagicMock()
    store.control.exists.return_value = alteration != "absent"
    marker = {"schema_version": 3, "complete": True, "registry_identity": registry.identity}
    if alteration == "version-bool":
        marker["schema_version"] = True
    elif alteration == "complete-int":
        marker["complete"] = 1
    elif alteration == "foreign":
        marker["registry_identity"] = {"device": 1, "inode": 3}
    store.control.json.return_value = marker
    # Completion binds the exact retained directory; verification never rewrites it.
    if alteration == "valid":
        routing.verify(store, registry)
    else:
        with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
            routing.verify(store, registry)
    store.control.put.assert_not_called()


@pytest.mark.parametrize(
    "existing,marked,contents,allowed",
    [
        (False, False, ["lock"], True),
        (True, False, ["lock"], False),
        (False, False, ["lock", "retained.json"], False),
        (True, True, ["lock"], True),
    ],
)
def test_initialization_never_automatically_adopts_existing_registry(
    existing: bool, marked: bool, contents: list[str], allowed: bool
) -> None:
    """Create completion only for a new empty registry or reuse verified completion.

    Args:
        existing: Prior registry existence.
        marked: Existing completion marker.
        contents: Retained direct filenames.
        allowed: Expected initialization decision.
    """
    registry = MagicMock()
    registry.identity = "directory-identity"
    registry.names.return_value = contents
    registry.exists.return_value = True
    registry.read.return_value = core.canonical(
        {"schema_version": 1, "prefix": "", "edges": {}, "route": None}
    )
    store = MagicMock()
    store.control.exists.return_value = marked
    store.control.json.return_value = {
        "schema_version": 3,
        "complete": True,
        "registry_identity": registry.identity,
    }
    if allowed:
        routing.initialize(store, registry, existing)
        assert store.control.put.call_count == int(not marked)
    else:
        with pytest.raises(core.WorkspaceError, match="RECOVERY_REQUIRED"):
            routing.initialize(store, registry, existing)
        store.control.put.assert_not_called()


@pytest.mark.parametrize(
    "alteration", ["valid", "absent", "foreign-native", "version-bool", "extra", "kind-list"]
)
def test_lookup_validates_only_exact_selected_pointer(alteration: str) -> None:
    """Preserve exact native bytes and refuse malformed selected terminal records.

    Args:
        alteration: Selected terminal presence or corruption.
    """
    registry = MagicMock()
    registry.exists.return_value = True
    digest = core.sha(core.canonical(["call", "call\n"]))
    pointer = {
        "schema_version": 1,
        "kind": "call",
        "native": "call\n",
        "issue_id": "TEST-1",
        "issue_uuid": "issue-uuid",
    }
    if alteration == "foreign-native":
        pointer["native"] = "call"
    elif alteration == "version-bool":
        pointer["schema_version"] = True
    elif alteration == "extra":
        pointer["authority"] = True
    elif alteration == "kind-list":
        pointer["kind"] = []

    def read(name: str, _limit: int) -> bytes:
        """Render a coherent selected chain with one deliberately varied endpoint.

        Args:
            name: Exact selected node filename.
            _limit: Production bounded reader limit.

        Returns:
            Persisted node bytes.
        """
        prefix = "" if name == "root.json" else name[5:-5]
        edges = (
            {digest[len(prefix)]: digest[: len(prefix) + 1]}
            if len(prefix) < 64 and alteration != "absent"
            else {}
        )
        return core.canonical(
            {
                "schema_version": 1,
                "prefix": prefix,
                "edges": edges,
                "route": pointer if len(prefix) == 64 else None,
            }
        )

    registry.read.side_effect = read
    # An unknown edge is absence; malformed endpoints remain errors.
    if alteration in {"valid", "absent"}:
        assert routing.lookup(registry, "call", "call\n") == (
            pointer if alteration == "valid" else None
        )
    else:
        with pytest.raises(core.WorkspaceError, match="INTEGRITY_ERROR"):
            routing.lookup(registry, "call", "call\n")
    registry.write.assert_not_called()


@pytest.mark.parametrize("kind,native", [("unknown", "call"), ("call", ""), ("call", "nul\0call")])
def test_lookup_rejects_invalid_external_identity(kind: str, native: str) -> None:
    """Reject invalid identities before opening storage.

    Args:
        kind: External namespace.
        native: External native bytes.
    """
    registry = MagicMock()
    with pytest.raises(core.WorkspaceError, match="INVALID_REQUEST"):
        routing.lookup(registry, kind, native)
    registry.exists.assert_not_called()


@pytest.mark.parametrize("retained", ["absent", "valid", "malformed"])
def test_legacy_population_guard_validates_without_migrating_records(retained: str) -> None:
    """Preserve valid coexisting legacy denial bytes.

    Args:
        retained: Legacy index presence or validity.
    """
    registry = MagicMock()
    registry.exists.return_value = retained != "absent"
    registry.json.return_value = (
        {"schema_version": 2, "children": {"a" * 64: {"status": "closed"}}}
        if retained == "valid"
        else {"schema_version": 1, "children": {}}
    )
    if retained == "malformed":
        with pytest.raises(core.WorkspaceError, match="INTEGRITY_ERROR"):
            routing.legacy_guard(registry)
    else:
        routing.legacy_guard(registry)
    registry.put.assert_not_called()


@pytest.mark.parametrize(
    "node",
    [
        None,
        [],
        {"schema_version": True, "prefix": "", "edges": {}, "route": None},
        {"schema_version": 1, "prefix": "", "edges": {"f": "0"}, "route": None},
    ],
)
def test_selected_node_structure_fails_closed(node: object) -> None:
    """Refuse malformed required nodes.

    Args:
        node: Untrusted persisted envelope.
    """
    registry = MagicMock()
    registry.exists.return_value = True
    registry.read.return_value = core.canonical(node)
    with pytest.raises(core.WorkspaceError, match="INTEGRITY_ERROR"):
        routing.lookup(registry, "conversation", "child")
    registry.write.assert_not_called()
