"""Records held in the topology registry."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Equipment:
    """A physical asset in the plant topology."""

    id: str
    name: str
    system: str
    points: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection used by the console."""
        return {
            "id": self.id,
            "name": self.name,
            "system": self.system,
            "points": list(self.points),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Equipment":
        """Rebuild equipment from a persisted row."""
        return cls(
            id=str(row["id"]),
            name=str(row.get("name", "")),
            system=str(row.get("system", "")),
            points=tuple(str(tag) for tag in row.get("points", ())),
        )


@dataclass(frozen=True)
class MeasurementPoint:
    """A tagged measurement point belonging to a piece of equipment."""

    id: str
    name: str
    system: str
    equipment: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection used by the console."""
        return {
            "id": self.id,
            "name": self.name,
            "system": self.system,
            "equipment": self.equipment,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "MeasurementPoint":
        """Rebuild a measurement point from a persisted row."""
        return cls(
            id=str(row["id"]),
            name=str(row.get("name", "")),
            system=str(row.get("system", "")),
            equipment=str(row.get("equipment", "")),
        )


@dataclass(frozen=True)
class Migration:
    """One immutable rename in the versioned migration chain."""

    version: int
    old_id: str
    new_id: str
    actor: str
    reason: str
    tick: int
    id: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection used by the console."""
        return {
            "id": self.id,
            "version": self.version,
            "old_id": self.old_id,
            "new_id": self.new_id,
            "actor": self.actor,
            "reason": self.reason,
            "tick": self.tick,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Migration":
        """Rebuild a migration from a persisted row."""
        return cls(
            version=int(row["version"]),
            old_id=str(row["old_id"]),
            new_id=str(row["new_id"]),
            actor=str(row.get("actor", "")),
            reason=str(row.get("reason", "")),
            tick=int(row.get("tick", 0)),
            id=str(row.get("id", "")),
        )
