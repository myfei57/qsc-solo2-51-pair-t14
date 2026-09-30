"""Topology service: validation, batch apply, versioned rename and audit.

The service is the entry point behind the console. Every configuration change
is validated as a whole before it is committed, every rename is a versioned
migration, and every validation conclusion and replacement is written to the
audit record.
"""

import copy
import json
from typing import Any

from ..audit.recorder import Recorder
from ..clock import LogicalClock
from ..errors import (
    TopologyNotFoundError,
    TopologyPermissionError,
    TopologyValidationError,
)
from ..store.store import Store
from . import naming
from .migration import MigrationChain
from .permission import Role, can_rename, parse_role
from .record import Equipment, MeasurementPoint
from .registry import TopologyRegistry
from .validation import validate_state


class TopologyService:
    """Orchestrates registry, validation, migration and audit."""

    def __init__(self, store: Store, clock: LogicalClock, recorder: Recorder) -> None:
        self._store = store
        self._clock = clock
        self._recorder = recorder
        self._registry = TopologyRegistry(store)
        self._chain = MigrationChain(store)

    # ------------------------------------------------------------- reading

    def current(self) -> dict[str, Any]:
        """The committed topology with its version."""
        return {
            "version": self._registry.version(),
            "equipment": [item.to_dict() for item in self._registry.equipment().values()],
            "points": [point.to_dict() for point in self._registry.points().values()],
        }

    def equipment(self) -> list[dict[str, Any]]:
        """Registered equipment as JSON-friendly rows."""
        return [item.to_dict() for item in self._registry.equipment().values()]

    def points(self) -> list[dict[str, Any]]:
        """Registered measurement points as JSON-friendly rows."""
        return [point.to_dict() for point in self._registry.points().values()]

    def migrations(self) -> list[dict[str, Any]]:
        """The immutable migration history as JSON-friendly rows."""
        return [migration.to_dict() for migration in self._chain.history()]

    def resolve(self, tag: str, at_version: int | None = None) -> dict[str, Any]:
        """Resolve a tag at a version, reporting whether it moved.

        When ``at_version`` is omitted the current version is used.
        """
        if at_version is None:
            at_version = self._registry.version()
        resolved = self._chain.resolve(tag, at_version)
        return {
            "id": tag,
            "at_version": at_version,
            "resolved": resolved,
            "changed": resolved != tag,
        }

    # ------------------------------------------------------------- validation

    def validate(
        self, equipment: list[Equipment], points: list[MeasurementPoint]
    ) -> dict[str, Any]:
        """Validate a target state without committing it (dry run).

        The conclusion is always written to the audit record.
        """
        violations = validate_state(equipment, points)
        conclusion = {"valid": not violations, "violations": violations}
        self._recorder.log(
            "topology.validate",
            json.dumps(
                {
                    "valid": not violations,
                    "violations": violations,
                    "equipment_count": len(equipment),
                    "point_count": len(points),
                }
            ),
        )
        return conclusion

    # ------------------------------------------------------------- apply

    def apply(
        self,
        equipment: list[Equipment],
        points: list[MeasurementPoint],
        actor: str,
    ) -> dict[str, Any]:
        """Validate and commit a batch as one atomic change.

        A single violation rejects the whole batch: nothing is written.
        """
        violations = validate_state(equipment, points)
        if violations:
            self._recorder.log(
                "topology.reject",
                json.dumps({"violations": violations, "actor": actor}),
            )
            raise TopologyValidationError(violations)
        version = self._registry.commit(equipment, points)
        self._recorder.log(
            "topology.apply",
            json.dumps(
                {
                    "version": version,
                    "actor": actor,
                    "equipment_count": len(equipment),
                    "point_count": len(points),
                }
            ),
        )
        return {"applied": True, "version": version}

    # ------------------------------------------------------------- rename

    def rename(
        self,
        old_id: str,
        new_id: str,
        actor: str,
        role: str,
        reason: str,
    ) -> dict[str, Any]:
        """Rename a tag as a versioned migration.

        Requires the rename permission. The new state is validated as a whole
        before the migration is recorded and committed.
        """
        parsed_role: Role = parse_role(role)
        if not can_rename(parsed_role):
            self._recorder.log(
                "topology.permission_denied",
                json.dumps(
                    {
                        "actor": actor,
                        "role": role,
                        "old_id": old_id,
                        "new_id": new_id,
                    }
                ),
            )
            raise TopologyPermissionError(
                f"role {role!r} is not allowed to rename topology tags"
            )

        if not old_id:
            raise TopologyValidationError(["old id must not be empty"])
        if not new_id:
            raise TopologyValidationError(["new id must not be empty"])
        if old_id == new_id:
            raise TopologyValidationError(["new id must differ from old id"])
        if self._registry.contains(new_id):
            raise TopologyValidationError([f"id {new_id!r} is already registered"])

        equipment_map = self._registry.equipment()
        points_map = self._registry.points()

        if old_id in equipment_map:
            kind = "equipment"
            if not naming.valid_equipment_id(new_id):
                raise TopologyValidationError(
                    [f"equipment id {new_id!r} does not match the naming rule"]
                )
            equipment, points = self._rename_equipment(
                equipment_map, points_map, old_id, new_id
            )
        elif old_id in points_map:
            kind = "point"
            if not naming.valid_point_id(new_id):
                raise TopologyValidationError(
                    [f"measurement point tag {new_id!r} does not match the naming rule"]
                )
            equipment, points = self._rename_point(
                equipment_map, points_map, old_id, new_id
            )
        else:
            raise TopologyNotFoundError(
                f"id {old_id!r} is not registered as equipment or a measurement point"
            )

        violations = validate_state(equipment, points)
        if violations:
            self._recorder.log(
                "topology.reject",
                json.dumps(
                    {
                        "violations": violations,
                        "actor": actor,
                        "old_id": old_id,
                        "new_id": new_id,
                    }
                ),
            )
            raise TopologyValidationError(violations)

        version = self._registry.commit(equipment, points)
        migration = self._chain.record(
            version=version,
            old_id=old_id,
            new_id=new_id,
            actor=actor,
            reason=reason,
            tick=self._clock.tick,
        )
        self._recorder.log(
            "topology.rename",
            json.dumps(
                {
                    "version": version,
                    "old_id": old_id,
                    "new_id": new_id,
                    "kind": kind,
                    "actor": actor,
                    "reason": reason,
                    "migration_id": migration.id,
                }
            ),
        )
        return {
            "renamed": True,
            "version": version,
            "kind": kind,
            "migration": migration.to_dict(),
        }

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _rename_equipment(
        equipment_map: dict[str, Equipment],
        points_map: dict[str, MeasurementPoint],
        old_id: str,
        new_id: str,
    ) -> tuple[list[Equipment], list[MeasurementPoint]]:
        """Rename equipment and repoint every measurement point at it."""
        equipment = copy.deepcopy(equipment_map)
        points = copy.deepcopy(points_map)
        item = equipment.pop(old_id)
        equipment[new_id] = Equipment(
            id=new_id, name=item.name, system=item.system, points=item.points
        )
        for tag, point in list(points.items()):
            if point.equipment == old_id:
                points[tag] = MeasurementPoint(
                    id=point.id,
                    name=point.name,
                    system=point.system,
                    equipment=new_id,
                )
        return list(equipment.values()), list(points.values())

    @staticmethod
    def _rename_point(
        equipment_map: dict[str, Equipment],
        points_map: dict[str, MeasurementPoint],
        old_id: str,
        new_id: str,
    ) -> tuple[list[Equipment], list[MeasurementPoint]]:
        """Rename a measurement point and update every equipment that lists it."""
        equipment = copy.deepcopy(equipment_map)
        points = copy.deepcopy(points_map)
        point = points.pop(old_id)
        points[new_id] = MeasurementPoint(
            id=new_id,
            name=point.name,
            system=point.system,
            equipment=point.equipment,
        )
        for identifier, item in list(equipment.items()):
            if old_id in item.points:
                equipment[identifier] = Equipment(
                    id=item.id,
                    name=item.name,
                    system=item.system,
                    points=tuple(
                        new_id if tag == old_id else tag for tag in item.points
                    ),
                )
        return list(equipment.values()), list(points.values())
