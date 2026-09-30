"""Equipment topology registration, migration history and console audit views."""

import json

import pytest

from desalplant.audit.recorder import Recorder
from desalplant.clock import LogicalClock
from desalplant.errors import InvalidTopologyError, PermissionDeniedError, TopologyConflictError
from desalplant.store.store import Store
from desalplant.topology.auth import ADMIN, EDITOR, RENAMER, Principal
from desalplant.topology.registry import TopologyRegistry
from desalplant.console.http import Request
from support import ok


ADMIN_HEADERS = {"X-Topology-User": "admin", "X-Topology-Roles": ADMIN}


def registry():
    store = Store()
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    return TopologyRegistry(store, clock, recorder), recorder


def batch_v1():
    return {
        "devices": [
            {
                "id": "eq-ro-train-s1",
                "area": "ro",
                "stage": 1,
                "probe_ids": ["pt-ro-feed-s1"],
            }
        ],
        "probes": [
            {
                "id": "pt-ro-feed-s1",
                "device_id": "eq-ro-train-s1",
                "stage": 1,
                "replaces": "s1",
            }
        ],
    }


def batch_v2():
    return {
        "devices": [
            {
                "id": "eq-ro-train-s1",
                "area": "ro",
                "stage": 1,
                "probe_ids": ["pt-ro-pressure-s1"],
            }
        ],
        "probes": [
            {
                "id": "pt-ro-pressure-s1",
                "device_id": "eq-ro-train-s1",
                "stage": 1,
                "replaces": "pt-ro-feed-s1",
            }
        ],
    }


def call_topology(console, method, path, body=None, headers=None):
    text = "" if body is None else json.dumps(body)
    request = Request.parse(method, path, text, headers or {})
    return console.handle(request)


def test_valid_batch_commits_an_immutable_version():
    topology, recorder = registry()
    result = topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    assert result["valid"] is True
    assert result["version"] == 1
    current = topology.current()
    assert current["devices"]["eq-ro-train-s1"]["area"] == "ro"
    assert topology.version(1)["probes"]["pt-ro-feed-s1"]["replaces"] == "s1"
    event = recorder.events()[0]
    assert event.kind == "topology.committed"
    assert event.data["renames"] == [
        {"kind": "probe", "new_id": "pt-ro-feed-s1", "old_id": "s1"}
    ]


def test_all_violations_reject_the_whole_batch():
    topology, _ = registry()
    with pytest.raises(InvalidTopologyError) as caught:
        topology.submit(
            {
                "devices": [{"id": "bad-device", "stage": 2}],
                "probes": [{"id": "pt-missing", "device_id": "eq-missing"}],
            },
            Principal("admin", frozenset({ADMIN})),
        )
    codes = {violation["code"] for violation in caught.value.violations}
    assert {"invalid_id", "dangling_owner"} <= codes
    assert topology.current()["version"] == 0
    event = caught.value.event
    assert event.kind == "topology.validation.rejected"
    assert event.data["violations"] == caught.value.violations


def test_duplicate_and_dangling_references_are_rejected():
    topology, _ = registry()
    batch = {
        "devices": [
            {"id": "eq-ro-a", "area": "ro", "probe_ids": ["pt-shared"]},
            {"id": "eq-ro-b", "area": "ro", "probe_ids": ["pt-shared", "missing"]},
        ],
        "probes": [{"id": "pt-shared", "device_id": "eq-ro-a"}],
    }
    with pytest.raises(InvalidTopologyError) as caught:
        topology.submit(batch, Principal("admin", frozenset({ADMIN})))
    codes = {violation["code"] for violation in caught.value.violations}
    assert {"duplicate_probe_owner", "invalid_probe_ref"} <= codes


def test_probe_owner_and_device_listing_must_agree_in_both_directions():
    topology, _ = registry()
    batch = {
        "devices": [{"id": "eq-ro-a", "area": "ro"}],
        "probes": [{"id": "pt-ro-a", "device_id": "eq-ro-a"}],
    }
    with pytest.raises(InvalidTopologyError) as caught:
        topology.submit(batch, Principal("admin", frozenset({ADMIN})))
    assert any(violation["code"] == "missing_probe_listing" for violation in caught.value.violations)


def test_dry_run_validates_without_activating():
    topology, _ = registry()
    result = topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})), dry_run=True)
    assert result["valid"] is True
    assert result["version"] == 0
    assert topology.current()["version"] == 0


def test_expected_version_rejects_stale_batches():
    topology, recorder = registry()
    topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    with pytest.raises(TopologyConflictError) as conflict:
        topology.submit(batch_v2(), Principal("admin", frozenset({ADMIN})), expected_version=0)
    assert conflict.value is not None
    assert recorder.events()[0].kind == "topology.validation.rejected"


def test_rename_migration_remains_resolvable_by_historical_version():
    topology, _ = registry()
    topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    topology.submit(batch_v2(), Principal("admin", frozenset({ADMIN})))

    assert topology.resolve("s1", at_version=1)["resolved_id"] == "pt-ro-feed-s1"
    latest = topology.resolve("s1", at_version=2)
    assert latest["resolved_id"] == "pt-ro-pressure-s1"
    assert [item["from_id"] for item in latest["chain"]] == [
        "s1",
        "pt-ro-feed-s1",
    ]
    replacement = topology.replacement("s1")
    assert replacement["current_id"] == "pt-ro-pressure-s1"
    assert [item["effective_version"] for item in replacement["chain"]] == [1, 2]


def test_historical_versions_and_retired_identifiers_are_immutable():
    topology, _ = registry()
    topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    topology.submit(
        {
            "devices": [{"id": "eq-ro-train-s1", "area": "ro", "stage": 1}],
            "probes": [],
        },
        Principal("admin", frozenset({ADMIN})),
    )
    with pytest.raises(InvalidTopologyError) as caught:
        topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    codes = {violation["code"] for violation in caught.value.violations}
    assert "historical_id_reused" in codes
    assert topology.version(1)["version"] == 1


def test_naming_and_stage_ownership_are_validated():
    topology, _ = registry()
    batch = {
        "devices": [{"id": "eq-ro-train-s1", "area": "ro", "stage": 1}],
        "probes": [
            {"id": "pt-ro-s2", "device_id": "eq-ro-train-s1", "stage": 2},
        ],
    }
    with pytest.raises(InvalidTopologyError) as caught:
        topology.submit(batch, Principal("admin", frozenset({ADMIN})))
    codes = {violation["code"] for violation in caught.value.violations}
    assert "stage_ownership_mismatch" in codes


def test_rename_requires_renamer_and_structure_requires_editor():
    topology, _ = registry()
    with pytest.raises(PermissionDeniedError):
        topology.submit(batch_v1(), Principal("editor", frozenset({EDITOR})))
    assert topology.current()["version"] == 0

    topology.submit(batch_v1(), Principal("admin", frozenset({ADMIN})))
    with pytest.raises(PermissionDeniedError):
        topology.submit(batch_v2(), Principal("editor", frozenset({EDITOR})))
    assert topology.current()["version"] == 1

    topology.submit(batch_v2(), Principal("renamer", frozenset({RENAMER})))
    assert topology.current()["version"] == 2


def test_console_lists_topology_routes(console):
    routes = console.routes()
    assert ("GET", "/api/topology") in routes
    assert ("POST", "/api/topology") in routes
    assert ("GET", "/api/topology/audit") in routes


def test_console_registers_validates_and_exposes_history(console):
    response = call_topology(console, "POST", "/api/topology", batch_v1(), ADMIN_HEADERS)
    assert response.status == 200, response.body
    assert response.body["version"] == 1

    bad = {"devices": [{"id": "bad", "area": "ro"}], "probes": []}
    rejected = call_topology(console, "POST", "/api/topology", bad, ADMIN_HEADERS)
    assert rejected.status == 422
    assert rejected.body["violations"]
    assert rejected.body["event"]["kind"] == "topology.validation.rejected"

    audit = ok(console, "GET", "/api/topology/audit")
    kinds = {event["kind"] for event in audit["events"]}
    assert {"topology.committed", "topology.validation.rejected"} <= kinds

    resolved = ok(console, "GET", "/api/topology/resolve?id=s1&version=1")
    assert resolved["resolved_id"] == "pt-ro-feed-s1"
    replacement = ok(console, "GET", "/api/topology/replacement?old_id=s1")
    assert replacement["chain"][0]["effective_version"] == 1


def test_console_denies_rename_without_permission(console):
    headers = {"X-Topology-User": "editor", "X-Topology-Roles": EDITOR}
    response = call_topology(console, "POST", "/api/topology", batch_v1(), headers)
    assert response.status == 403
    audit = ok(console, "GET", "/api/topology/audit")
    assert audit["events"][0]["kind"] == "topology.permission.denied"
