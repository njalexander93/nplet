"""Route exact Cursor identities through durable compressed radix nodes, without authority."""

from __future__ import annotations

from nplet.lifecycle import _invocation_records as records
from nplet.lifecycle import task_workspace as core

MARKER = "cursor-routing.json"
ROOT = "root.json"
NODE_LIMIT = core.MAX_FILE


def legacy_guard(registry: core.Directory, conversation: str | None = None) -> None:
    """Validate retained legacy denial evidence without changing or migrating it.

    Args:
        registry: Canonical directory; its legacy index remains read-only.
        conversation: Optional actual raw identity to fence before parent routing.

    Raises:
        core.WorkspaceError: Index is malformed or names this raw child.
        OSError: Legacy evidence cannot be safely read.
    """
    # Coexisting legacy records remain denial evidence, never migrated authority.
    if not registry.exists("index.json"):
        return
    legacy = registry.json("index.json")
    core.require(
        isinstance(legacy, dict)
        and type(legacy.get("schema_version")) is int
        and legacy["schema_version"] == 2
        and isinstance(legacy.get("children"), dict)
        and all(
            core.DIGEST.fullmatch(key) and isinstance(value, dict)
            for key, value in legacy["children"].items()
        ),
        "INTEGRITY_ERROR",
    )
    # Population validates these bytes without selecting or rewriting a legacy participant.
    if conversation is not None:
        core.require(
            core.participant_key({"host": "cursor", "session_id": conversation})
            not in legacy["children"],
            "HOST_UNSUPPORTED_CHILD_IDENTITY",
        )


def verify(store: core.Store, registry: core.Directory) -> None:
    """Require completed radix population for this exact retained directory.

    Args:
        store: Verified controls; caller holds the registry mutex.
        registry: Canonical retained routing directory.

    Raises:
        core.WorkspaceError: Population is absent or the directory was replaced.
        OSError: Marker access fails.
    """
    # The root marker survives directory deletion and selects this exact routing format.
    core.require(store.control.exists(MARKER), "RECOVERY_REQUIRED")
    marker = store.control.json(MARKER)
    core.require(
        isinstance(marker, dict)
        and set(marker) == {"schema_version", "complete", "registry_identity"}
        and type(marker["schema_version"]) is int
        and marker["schema_version"] == 3
        and marker["complete"] is True
        and marker["registry_identity"] == registry.identity,
        "RECOVERY_REQUIRED",
    )


def _identity(kind: str, native: str) -> str:
    """Validate exact native bytes and derive their namespaced full digest.

    Args:
        kind: Closed call or conversation namespace.
        native: Exact opaque native identity.

    Returns:
        A full lowercase hexadecimal SHA-256 key.

    Raises:
        core.WorkspaceError: Namespace or native identity is invalid.
    """
    # Native text is never trimmed or coerced before hashing its canonical namespace.
    core.require(kind in ("call", "conversation"), "INVALID_REQUEST")
    records.native_text(native)
    return core.sha(core.canonical([kind, native]))


def _name(prefix: str) -> str:
    """Select the mandatory root or deterministic native-digest prefix filename.

    Args:
        prefix: Internally derived hexadecimal prefix, at most sixty-four digits.

    Returns:
        One direct filename beneath the retained registry.
    """
    return "node-" + prefix + ".json" if prefix else ROOT


def _node(registry: core.Directory, prefix: str) -> core.JSONObject:
    """Read a required referenced node and validate its bounded exact-prefix shape.

    Args:
        registry: Canonical registry opened under its mutex.
        prefix: Internally selected root or native-digest prefix.

    Returns:
        Validated node with at most sixteen deterministic child references.

    Raises:
        core.WorkspaceError: Referenced storage or selected terminal identity is invalid.
        OSError: Required node access fails.
    """
    # A missing mandatory root or referenced descendant is uncertainty, never an empty result.
    core.require(registry.exists(_name(prefix)), "RECOVERY_REQUIRED")
    node = core.strict_json(registry.read(_name(prefix), NODE_LIMIT))
    core.require(
        isinstance(node, dict)
        and set(node) == {"schema_version", "prefix", "edges", "route"}
        and type(node["schema_version"]) is int
        and node["schema_version"] == 1
        and node["prefix"] == prefix
        and isinstance(node["edges"], dict)
        and len(node["edges"]) <= 16
        and all(
            len(digit) == 1
            and digit in "0123456789abcdef"
            and isinstance(target, str)
            and len(prefix) < len(target) <= 64
            and all(char in "0123456789abcdef" for char in target)
            and target.startswith(prefix + digit)
            for digit, target in node["edges"].items()
        ),
        "INTEGRITY_ERROR",
    )
    assert isinstance(node, dict)
    # Internal prefixes contain only edges; a full digest contains one exact issue selector.
    if len(prefix) < 64:
        core.require(node["route"] is None, "INTEGRITY_ERROR")
    else:
        route = node["route"]
        core.require(
            len(prefix) == 64
            and not node["edges"]
            and isinstance(route, dict)
            and set(route) == {"schema_version", "kind", "native", "issue_id", "issue_uuid"}
            and type(route["schema_version"]) is int
            and route["schema_version"] == 1
            and route["kind"] in ("call", "conversation"),
            "INTEGRITY_ERROR",
        )
        core.require(_identity(route["kind"], route["native"]) == prefix, "INTEGRITY_ERROR")
        core.issue_id(route["issue_id"])
        core.token(route["issue_uuid"])
    return node


def _publish(registry: core.Directory, node: core.JSONObject) -> None:
    """Durably publish one bounded node with explicit before/after crash boundaries.

    Args:
        registry: Canonical registry opened under its mutex.
        node: Validated or internally constructed complete node.

    Raises:
        core.WorkspaceError: Encoded bytes exceed the identical node reader limit.
        OSError: Atomic node publication fails.
    """
    # Every file has bounded fanout or one bounded identity; occupancy never aggregates histories.
    data = core.canonical(node)
    core.require(len(data) <= NODE_LIMIT, "SIZE_LIMIT")
    label = node["prefix"] or "root"
    core.fault("cursor-radix-before:" + label)
    registry.write(_name(node["prefix"]), data)
    core.fault("cursor-radix-after:" + label)


def prepare(registry: core.Directory) -> None:
    """Prepare only a markerless root while preserving every existing reachable edge.

    Args:
        registry: Fresh or explicitly recovering markerless registry under its mutex.

    Raises:
        core.WorkspaceError: An existing root is unreadable or malformed.
        OSError: Root publication fails.
    """
    # Only fresh initialization and explicit markerless population call this method.
    if not registry.exists(ROOT):
        _publish(registry, {"schema_version": 1, "prefix": "", "edges": {}, "route": None})
    _node(registry, "")


def complete(store: core.Store, registry: core.Directory) -> core.JSONObject:
    """Verify the mandatory root before publishing explicit population completion.

    Args:
        store: Verified repository controls; caller holds the registry mutex.
        registry: Canonical registry with all selected authoritative routes root-reachable.

    Returns:
        Durable completion marker bound to this directory identity.

    Raises:
        core.WorkspaceError: The mandatory root is absent or malformed.
        OSError: Root validation or marker publication fails.
    """
    # Selected claims establish descendant durability; completion never recreates missing storage.
    _node(registry, "")
    marker = {"schema_version": 3, "complete": True, "registry_identity": registry.identity}
    store.control.put(MARKER, marker)
    core.fault("cursor-routing-initialized")
    return marker


def initialize(store: core.Store, registry: core.Directory, existed: bool) -> None:
    """Publish a root before the first transition in a demonstrably new empty registry.

    Args:
        store: Verified controls; caller holds the registry mutex.
        registry: Open registry whose mutex is already durable.
        existed: Whether this registry existed before the current admission.

    Raises:
        core.WorkspaceError: An old registry needs explicit complete population.
        OSError: Root or marker publication fails.
    """
    # Existing completion never permits silent reconstruction of a lost root.
    if store.control.exists(MARKER):
        verify(store, registry)
        return
    core.require(not existed and registry.names() == ["lock"], "RECOVERY_REQUIRED")
    prepare(registry)
    complete(store, registry)


def lookup(registry: core.Directory, kind: str, native: str) -> core.JSONObject | None:
    """Select one exact route through required references without granting authority.

    Args:
        registry: Canonical registry opened under its mutex.
        kind: Closed call or conversation namespace.
        native: Exact native identity.

    Returns:
        Validated selector, or None for a missing edge or validated compressed-prefix mismatch.

    Raises:
        core.WorkspaceError: Selected identity or referenced storage is invalid.
        OSError: Required node access fails.
    """
    # Read each selected reference before treating a compressed-prefix mismatch as unknown.
    digest = _identity(kind, native)
    node = _node(registry, "")
    while len(node["prefix"]) < 64:
        digit = digest[len(node["prefix"])]
        if digit not in node["edges"]:
            return None
        node = _node(registry, node["edges"][digit])
        if not digest.startswith(node["prefix"]):
            return None
    # Exact bytes are checked even if two external identities produce the same full digest.
    route: core.JSONObject = node["route"]
    core.require(route["kind"] == kind and route["native"] == native, "REQUEST_CONFLICT")
    return route


def claim(registry: core.Directory, kind: str, native: str, issue: str, issue_uuid: str) -> None:
    """Make exact issue selection root-reachable before its corresponding authority commit.

    Args:
        registry: Canonical registry opened under its mutex.
        kind: Closed call or conversation namespace.
        native: Exact identity retained in the full-digest terminal record.
        issue: Selected authoritative issue identifier.
        issue_uuid: Immutable identity from recovered issue state.

    Raises:
        core.WorkspaceError: Identity conflicts or referenced storage is invalid.
        OSError: Atomic node publication fails.
    """
    # Existing root-reachable records are immutable issue selectors, never participant authority.
    core.issue_id(issue)
    core.token(issue_uuid)
    digest = _identity(kind, native)
    route = {
        "schema_version": 1,
        "kind": kind,
        "native": native,
        "issue_id": issue,
        "issue_uuid": issue_uuid,
    }
    # Validate the selected path before publication; never recreate a missing referenced leaf.
    selected = lookup(registry, kind, native)
    if selected is not None:
        core.require(selected == route, "REQUEST_CONFLICT")
        core.fault("cursor-route:" + kind)
        return
    root = _node(registry, "")
    if registry.exists(_name(digest)):
        core.require(_node(registry, digest)["route"] == route, "REQUEST_CONFLICT")
    else:
        _publish(registry, {"schema_version": 1, "prefix": digest, "edges": {}, "route": route})
    _attach(registry, root, digest)
    # Failure here leaves restrictive routing durable before the issue may grant authority.
    core.fault("cursor-route:" + kind)


def _attach(registry: core.Directory, parent: core.JSONObject, incoming: str) -> None:
    """Merge one durable descendant without losing selected or orphan branch edges.

    Args:
        registry: Canonical routing directory under its mutex.
        parent: Validated node strictly above the incoming prefix.
        incoming: Prefix of an already durable node.

    Raises:
        core.WorkspaceError: Required nodes or compatible orphan storage are invalid.
        OSError: Atomic branch or parent publication fails.
    """
    # An absent edge needs only its already durable leaf or branch reference.
    digit = incoming[len(parent["prefix"])]
    target = parent["edges"].get(digit)
    if target is None:
        parent["edges"][digit] = incoming
        _publish(registry, parent)
        return
    existing = _node(registry, target)
    if target == incoming:
        return
    # Find the branching prefix; old one-digit paths remain valid compressed paths.
    length = len(parent["prefix"]) + 1
    while length < min(len(target), len(incoming)) and target[length] == incoming[length]:
        length += 1
    prefix = incoming[:length]
    if prefix == target:
        _attach(registry, existing, incoming)
        return
    # Reuse compatible orphan branches rather than overwriting their descendants.
    if registry.exists(_name(prefix)):
        branch = _node(registry, prefix)
        _attach(registry, branch, target)
        if prefix != incoming:
            _attach(registry, branch, incoming)
    else:
        branch = {
            "schema_version": 1,
            "prefix": prefix,
            "edges": {target[length]: target, incoming[length]: incoming},
            "route": None,
        }
        _publish(registry, branch)
    # The new subtree is durable before redirecting its previously reachable parent.
    parent["edges"][digit] = prefix
    _publish(registry, parent)
