"""Equipment topology registry: naming, validation, batch apply, versioned rename."""

import pytest

from desalplant.errors import (
    TopologyNotFoundError,
    TopologyPermissionError,
    TopologyValidationError,
)
from desalplant.topology.naming import (
    valid_equipment_id,
    valid_point_id,
    valid_system,
)
from desalplant.topology.permission import Role, can_rename, parse_role
from desalplant.topology.record import Equipment, MeasurementPoint
from desalplant.topology.service import TopologyService

from support import call, ok, refused


# --------------------------------------------------------------- helpers


def pump_unit() -> tuple[list[Equipment], list[MeasurementPoint]]:
    """A valid batch: one pump with two points."""
    equipment = [
        Equipment(id="pump-hp", name="High Pressure Pump", system="pump"),
    ]
    points = [
        MeasurementPoint(
            id="PT-101",
            name="HP Pump Discharge Pressure",
            system="pump",
            equipment="pump-hp",
        ),
        MeasurementPoint(
            id="FT-101",
            name="HP Pump Flow",
            system="pump",
            equipment="pump-hp",
        ),
    ]
    return equipment, points


def tank_unit() -> tuple[list[Equipment], list[MeasurementPoint]]:
    """A valid batch: one tank with one point."""
    equipment = [
        Equipment(id="tank-a", name="Product Tank A", system="tank"),
    ]
    points = [
        MeasurementPoint(
            id="LT-401",
            name="Product Tank A Level",
            system="tank",
            equipment="tank-a",
        ),
    ]
    return equipment, points


def topology_service(store) -> TopologyService:
    """A topology service over a fresh store."""
    from desalplant.audit.recorder import Recorder
    from desalplant.clock import LogicalClock

    return TopologyService(store, LogicalClock(), Recorder(store))


# --------------------------------------------------------------- naming


def test_point_ids_follow_the_isa_rule():
    assert valid_point_id("PT-101")
    assert valid_point_id("FT-201A")
    assert valid_point_id("TT-302")
    assert not valid_point_id("pt-101")
    assert not valid_point_id("PT101")
    assert not valid_point_id("PT-1")
    assert not valid_point_id("PT-1010")


def test_equipment_ids_follow_the_kebab_rule():
    assert valid_equipment_id("tank-a")
    assert valid_equipment_id("pump-hp")
    assert valid_equipment_id("ro-array-1")
    assert not valid_equipment_id("TankA")
    assert not valid_equipment_id("tank_a")
    assert not valid_equipment_id("1-tank")


def test_systems_are_known_subsystems():
    assert valid_system("pump")
    assert valid_system("tank")
    assert not valid_system("electrical")


# --------------------------------------------------------------- validation


def test_valid_batch_passes(store):
    service = topology_service(store)
    equipment, points = pump_unit()
    conclusion = service.validate(equipment, points)
    assert conclusion["valid"] is True
    assert conclusion["violations"] == []


def test_duplicate_equipment_id_is_rejected(store):
    service = topology_service(store)
    equipment = [
        Equipment(id="pump-hp", name="A", system="pump"),
        Equipment(id="pump-hp", name="B", system="pump"),
    ]
    conclusion = service.validate(equipment, [])
    assert conclusion["valid"] is False
    assert any("duplicate equipment id" in v for v in conclusion["violations"])


def test_duplicate_point_tag_is_rejected(store):
    service = topology_service(store)
    equipment, _ = pump_unit()
    points = [
        MeasurementPoint(id="PT-101", name="A", system="pump", equipment="pump-hp"),
        MeasurementPoint(id="PT-101", name="B", system="pump", equipment="pump-hp"),
    ]
    conclusion = service.validate(equipment, points)
    assert conclusion["valid"] is False
    assert any("duplicate measurement point tag" in v for v in conclusion["violations"])


def test_id_used_for_both_kinds_is_rejected(store):
    service = topology_service(store)
    equipment = [Equipment(id="PT-101", name="A", system="pump")]
    points = [
        MeasurementPoint(id="PT-101", name="B", system="pump", equipment="PT-101"),
    ]
    conclusion = service.validate(equipment, points)
    assert conclusion["valid"] is False
    assert any("both equipment and measurement point" in v for v in conclusion["violations"])


def test_point_without_equipment_is_rejected(store):
    service = topology_service(store)
    equipment, _ = pump_unit()
    points = [
        MeasurementPoint(
            id="PT-999",
            name="Orphan",
            system="pump",
            equipment="pump-missing",
        ),
    ]
    conclusion = service.validate(equipment, points)
    assert conclusion["valid"] is False
    assert any("missing equipment" in v for v in conclusion["violations"])


def test_point_system_mismatch_is_rejected(store):
    service = topology_service(store)
    equipment, _ = pump_unit()
    points = [
        MeasurementPoint(
            id="PT-101",
            name="A",
            system="tank",
            equipment="pump-hp",
        ),
    ]
    conclusion = service.validate(equipment, points)
    assert conclusion["valid"] is False
    assert any("differs from its equipment system" in v for v in conclusion["violations"])


def test_equipment_listing_a_missing_point_is_rejected(store):
    service = topology_service(store)
    equipment = [
        Equipment(id="pump-hp", name="A", system="pump", points=("PT-999",)),
    ]
    conclusion = service.validate(equipment, [])
    assert conclusion["valid"] is False
    assert any("missing measurement point" in v for v in conclusion["violations"])


def test_bad_equipment_naming_is_rejected(store):
    service = topology_service(store)
    equipment = [Equipment(id="PumpHP", name="A", system="pump")]
    conclusion = service.validate(equipment, [])
    assert conclusion["valid"] is False
    assert any("does not match the naming rule" in v for v in conclusion["violations"])


# --------------------------------------------------------------- batch apply


def test_apply_commits_a_valid_batch(store):
    service = topology_service(store)
    equipment, points = pump_unit()
    result = service.apply(equipment, points, actor="engineer")
    assert result["applied"] is True
    assert result["version"] == 1
    current = service.current()
    assert current["version"] == 1
    assert len(current["equipment"]) == 1
    assert len(current["points"]) == 2


def test_apply_rejects_an_invalid_batch_without_writing(store):
    service = topology_service(store)
    equipment, points = pump_unit()
    service.apply(equipment, points, actor="engineer")
    # Now try to apply a batch with a duplicate id.
    bad_equipment = [
        Equipment(id="pump-hp", name="A", system="pump"),
        Equipment(id="pump-hp", name="B", system="pump"),
    ]
    with pytest.raises(TopologyValidationError) as raised:
        service.apply(bad_equipment, [], actor="engineer")
    assert len(raised.value.violations) >= 1
    # The committed state is unchanged.
    assert service.current()["version"] == 1
    assert len(service.current()["equipment"]) == 1


def test_apply_is_all_or_nothing(store):
    service = topology_service(store)
    equipment, points = pump_unit()
    service.apply(equipment, points, actor="engineer")
    # A batch that is valid in isolation but drops a point that equipment still
    # references must be rejected as a whole.
    new_equipment = [
        Equipment(id="pump-hp", name="A", system="pump", points=("PT-101",)),
    ]
    with pytest.raises(TopologyValidationError):
        service.apply(new_equipment, [], actor="engineer")
    assert service.current()["version"] == 1
    assert len(service.current()["points"]) == 2


def test_apply_advances_the_version(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    assert service.current()["version"] == 1
    service.apply(*tank_unit(), actor="engineer")
    assert service.current()["version"] == 2


# --------------------------------------------------------------- versioned migration


def test_rename_records_a_migration(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    result = service.rename(
        old_id="PT-101",
        new_id="PT-201",
        actor="engineer",
        role="engineer",
        reason="re-tagged during the train revamp",
    )
    assert result["renamed"] is True
    assert result["version"] == 2
    assert result["kind"] == "point"
    assert result["migration"]["old_id"] == "PT-101"
    assert result["migration"]["new_id"] == "PT-201"
    assert result["migration"]["actor"] == "engineer"
    assert len(service.migrations()) == 1


def test_rename_equipment_repoints_its_points(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    service.rename(
        old_id="pump-hp",
        new_id="pump-high",
        actor="engineer",
        role="engineer",
        reason="standardised the equipment id",
    )
    points = service.points()
    assert all(point["equipment"] == "pump-high" for point in points)
    assert not any(point["equipment"] == "pump-hp" for point in points)


def test_rename_point_updates_equipment_listings(store):
    service = topology_service(store)
    equipment, points = pump_unit()
    # List the point on the equipment so the rename must update the listing.
    equipment[0] = Equipment(
        id="pump-hp", name="A", system="pump", points=("PT-101", "FT-101")
    )
    service.apply(equipment, points, actor="engineer")
    service.rename(
        old_id="PT-101",
        new_id="PT-201",
        actor="engineer",
        role="engineer",
        reason="re-tagged",
    )
    listed = service.equipment()[0]["points"]
    assert "PT-201" in listed
    assert "PT-101" not in listed


def test_resolve_follows_the_rename_chain(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    service.rename(
        old_id="PT-101",
        new_id="PT-201",
        actor="engineer",
        role="engineer",
        reason="first rename",
    )
    # At version 1 the rename had not happened yet.
    assert service.resolve("PT-101", at_version=1)["resolved"] == "PT-101"
    # At version 2 the rename is in effect.
    assert service.resolve("PT-101", at_version=2)["resolved"] == "PT-201"
    # At the current version the rename is in effect.
    assert service.resolve("PT-101")["resolved"] == "PT-201"
    # An unknown tag resolves to itself.
    assert service.resolve("PT-999", at_version=2)["resolved"] == "PT-999"


def test_resolve_follows_chained_renames(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    service.rename("PT-101", "PT-201", "eng", "engineer", "step one")
    service.rename("PT-201", "PT-301", "eng", "engineer", "step two")
    assert service.resolve("PT-101", at_version=1)["resolved"] == "PT-101"
    assert service.resolve("PT-101", at_version=2)["resolved"] == "PT-201"
    assert service.resolve("PT-101", at_version=3)["resolved"] == "PT-301"


def test_replacement_is_traceable_to_actor_and_version(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    service.rename("PT-101", "PT-201", "alice", "engineer", "revamp")
    migration = service._chain.replacement("PT-101")
    assert migration is not None
    assert migration.version == 2
    assert migration.actor == "alice"
    assert migration.new_id == "PT-201"


def test_rename_unknown_id_is_rejected(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    with pytest.raises(TopologyNotFoundError):
        service.rename("PT-999", "PT-201", "eng", "engineer", "x")


def test_rename_to_an_existing_id_is_rejected(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    with pytest.raises(TopologyValidationError):
        service.rename("PT-101", "FT-101", "eng", "engineer", "x")


def test_rename_with_a_bad_new_name_is_rejected(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    with pytest.raises(TopologyValidationError):
        service.rename("PT-101", "pt-101", "eng", "engineer", "x")


# --------------------------------------------------------------- permission


def test_viewer_and_operator_cannot_rename(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    for role in ("viewer", "operator"):
        with pytest.raises(TopologyPermissionError):
            service.rename("PT-101", "PT-201", "eng", role, "x")


def test_engineer_and_admin_can_rename(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    result = service.rename("PT-101", "PT-201", "eng", "engineer", "x")
    assert result["renamed"] is True
    result = service.rename("FT-101", "FT-201", "eng", "admin", "x")
    assert result["renamed"] is True


def test_parse_role_rejects_unknown_roles():
    with pytest.raises(ValueError):
        parse_role("superuser")


def test_can_rename_reflects_the_role():
    assert can_rename(Role.ENGINEER) is True
    assert can_rename(Role.ADMIN) is True
    assert can_rename(Role.OPERATOR) is False
    assert can_rename(Role.VIEWER) is False


# --------------------------------------------------------------- audit


def test_validate_logs_a_conclusion(store):
    from desalplant.audit.recorder import Recorder
    from desalplant.clock import LogicalClock

    service = topology_service(store)
    service.validate(*pump_unit())
    events = Recorder(store).events()
    assert events[0].kind == "topology.validate"
    assert "valid" in events[0].detail


def test_apply_logs_the_commit(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    from desalplant.audit.recorder import Recorder

    events = Recorder(store).events()
    assert events[0].kind == "topology.apply"
    assert "version" in events[0].detail


def test_rename_logs_the_replacement(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    service.rename("PT-101", "PT-201", "eng", "engineer", "revamp")
    from desalplant.audit.recorder import Recorder

    events = Recorder(store).events()
    assert events[0].kind == "topology.rename"
    assert "PT-101" in events[0].detail
    assert "PT-201" in events[0].detail


def test_rejected_apply_logs_the_violations(store):
    service = topology_service(store)
    bad = [
        Equipment(id="pump-hp", name="A", system="pump"),
        Equipment(id="pump-hp", name="B", system="pump"),
    ]
    with pytest.raises(TopologyValidationError):
        service.apply(bad, [], actor="engineer")
    from desalplant.audit.recorder import Recorder

    events = Recorder(store).events()
    assert events[0].kind == "topology.reject"
    assert "duplicate equipment id" in events[0].detail


def test_permission_denied_is_logged(store):
    service = topology_service(store)
    service.apply(*pump_unit(), actor="engineer")
    with pytest.raises(TopologyPermissionError):
        service.rename("PT-101", "PT-201", "eng", "viewer", "x")
    from desalplant.audit.recorder import Recorder

    events = Recorder(store).events()
    assert events[0].kind == "topology.permission_denied"


# --------------------------------------------------------------- console: read


def test_topology_starts_empty(console):
    body = ok(console, "GET", "/api/topology")
    assert body["version"] == 0
    assert body["equipment"] == []
    assert body["points"] == []


def test_topology_points_and_equipment_endpoints(console):
    ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    points = ok(console, "GET", "/api/topology/points")
    assert points["points"][0]["id"] == "PT-101"
    equipment = ok(console, "GET", "/api/topology/equipment")
    assert equipment["equipment"][0]["id"] == "pump-hp"


def test_topology_migrations_endpoint(console):
    ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    ok(
        console,
        "POST",
        "/api/topology/rename",
        {
            "old_id": "PT-101",
            "new_id": "PT-201",
            "actor": "eng",
            "role": "engineer",
            "reason": "revamp",
        },
    )
    migrations = ok(console, "GET", "/api/topology/migrations")
    assert len(migrations["migrations"]) == 1
    assert migrations["migrations"][0]["old_id"] == "PT-101"


def test_topology_resolve_endpoint(console):
    ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    ok(
        console,
        "POST",
        "/api/topology/rename",
        {
            "old_id": "PT-101",
            "new_id": "PT-201",
            "actor": "eng",
            "role": "engineer",
            "reason": "revamp",
        },
    )
    at_one = ok(console, "GET", "/api/topology/resolve?id=PT-101&at_version=1")
    assert at_one["resolved"] == "PT-101"
    at_two = ok(console, "GET", "/api/topology/resolve?id=PT-101&at_version=2")
    assert at_two["resolved"] == "PT-201"


# --------------------------------------------------------------- console: write


def test_topology_validate_endpoint_reports_without_committing(console):
    body = ok(
        console,
        "POST",
        "/api/topology/validate",
        {
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    assert body["valid"] is True
    # The dry run did not commit anything.
    assert ok(console, "GET", "/api/topology")["version"] == 0


def test_topology_apply_endpoint_commits(console):
    body = ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    assert body["applied"] is True
    assert body["version"] == 1


def test_topology_apply_endpoint_rejects_invalid_batch(console):
    response = refused(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "A", "system": "pump", "points": []},
                {"id": "pump-hp", "name": "B", "system": "pump", "points": []},
            ],
            "points": [],
        },
    )
    assert response.status == 422
    # Nothing was committed.
    assert ok(console, "GET", "/api/topology")["version"] == 0


def test_topology_rename_endpoint_requires_permission(console):
    ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    response = refused(
        console,
        "POST",
        "/api/topology/rename",
        {
            "old_id": "PT-101",
            "new_id": "PT-201",
            "actor": "eng",
            "role": "viewer",
            "reason": "revamp",
        },
    )
    assert response.status == 403
    # The tag was not renamed.
    assert ok(console, "GET", "/api/topology")["points"][0]["id"] == "PT-101"


def test_topology_rename_endpoint_succeeds_for_engineer(console):
    ok(
        console,
        "POST",
        "/api/topology/apply",
        {
            "actor": "engineer",
            "equipment": [
                {"id": "pump-hp", "name": "HP Pump", "system": "pump", "points": []}
            ],
            "points": [
                {
                    "id": "PT-101",
                    "name": "Pressure",
                    "system": "pump",
                    "equipment": "pump-hp",
                }
            ],
        },
    )
    body = ok(
        console,
        "POST",
        "/api/topology/rename",
        {
            "old_id": "PT-101",
            "new_id": "PT-201",
            "actor": "eng",
            "role": "engineer",
            "reason": "revamp",
        },
    )
    assert body["renamed"] is True
    assert body["migration"]["old_id"] == "PT-101"
    assert body["migration"]["new_id"] == "PT-201"
    assert ok(console, "GET", "/api/topology")["points"][0]["id"] == "PT-201"
