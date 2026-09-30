"""Batch validation for a target topology state.

A batch is the complete target state of the registry. It is validated as a
whole before anything is committed: a single violation rejects the entire
batch, so partial failures never take effect.
"""

from . import naming
from .record import Equipment, MeasurementPoint


def _duplicates(ids: list[str]) -> list[str]:
    """Ids appearing more than once, in first-seen order."""
    seen: set[str] = set()
    repeated: list[str] = []
    for identifier in ids:
        if identifier in seen and identifier not in repeated:
            repeated.append(identifier)
        seen.add(identifier)
    return repeated


def validate_state(
    equipment: list[Equipment], points: list[MeasurementPoint]
) -> list[str]:
    """Validate a target state and return the list of violations.

    An empty list means the state is valid and may be committed.
    """
    violations: list[str] = []

    # 1. Naming rules: every id and system must be well formed.
    for item in equipment:
        if not naming.valid_equipment_id(item.id):
            violations.append(
                f"equipment id {item.id!r} does not match the naming rule"
            )
        if not naming.valid_system(item.system):
            violations.append(
                f"equipment {item.id!r} names unknown system {item.system!r}"
            )
    for point in points:
        if not naming.valid_point_id(point.id):
            violations.append(
                f"measurement point tag {point.id!r} does not match the naming rule"
            )
        if not naming.valid_system(point.system):
            violations.append(
                f"measurement point {point.id!r} names unknown system {point.system!r}"
            )

    # 2. Uniqueness: no duplicate ids, no id used for both kinds.
    equipment_ids = [item.id for item in equipment]
    point_ids = [point.id for point in points]
    for identifier in _duplicates(equipment_ids):
        violations.append(f"duplicate equipment id {identifier!r}")
    for identifier in _duplicates(point_ids):
        violations.append(f"duplicate measurement point tag {identifier!r}")
    for identifier in sorted(set(equipment_ids) & set(point_ids)):
        violations.append(
            f"id {identifier!r} is used as both equipment and measurement point"
        )

    # 3. Ownership: every point belongs to an existing piece of equipment and
    #    the point's system matches the equipment's system.
    equipment_by_id = {item.id: item for item in equipment}
    for point in points:
        owner = equipment_by_id.get(point.equipment)
        if owner is None:
            violations.append(
                f"measurement point {point.id!r} points to missing equipment "
                f"{point.equipment!r}"
            )
        elif point.system != owner.system:
            violations.append(
                f"measurement point {point.id!r} system {point.system!r} differs "
                f"from its equipment system {owner.system!r}"
            )

    # 4. No dangling references: every point listed by equipment exists.
    point_ids_set = set(point_ids)
    for item in equipment:
        for tag in item.points:
            if tag not in point_ids_set:
                violations.append(
                    f"equipment {item.id!r} references missing measurement point "
                    f"{tag!r}"
                )

    return violations
