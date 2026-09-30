"""Store-backed topology registry.

The registry keeps the complete topology (equipment and measurement points) as
a single document, so a batch apply is one atomic write. Nothing is committed
until the whole target state passes validation.
"""

from typing import Any

from ..store import keys
from ..store.store import Store
from .record import Equipment, MeasurementPoint

EMPTY_STATE: dict[str, Any] = {"version": 0, "equipment": {}, "points": {}}


class TopologyRegistry:
    """Read and write the committed topology state."""

    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------- state

    def load(self) -> dict[str, Any]:
        """The committed topology document, empty when never written."""
        document = self._store.get(keys.TOPOLOGY_STATE)
        if not isinstance(document, dict):
            return dict(EMPTY_STATE)
        return document

    def equipment(self) -> dict[str, Equipment]:
        """Registered equipment keyed by id."""
        document = self.load()
        rows = document.get("equipment", {})
        return {
            identifier: Equipment.from_dict(row)
            for identifier, row in rows.items()
        }

    def points(self) -> dict[str, MeasurementPoint]:
        """Registered measurement points keyed by tag."""
        document = self.load()
        rows = document.get("points", {})
        return {tag: MeasurementPoint.from_dict(row) for tag, row in rows.items()}

    def version(self) -> int:
        """Current topology version, zero before the first apply."""
        document = self.load()
        return int(document.get("version", 0) or 0)

    def contains(self, identifier: str) -> bool:
        """Whether an id is registered as equipment or a point."""
        document = self.load()
        return identifier in document.get("equipment", {}) or identifier in document.get(
            "points", {}
        )

    # ------------------------------------------------------------- apply

    def commit(
        self, equipment: list[Equipment], points: list[MeasurementPoint]
    ) -> int:
        """Atomically replace the topology and advance the version.

        The caller is responsible for validating the target state first; this
        method writes without re-checking.
        """
        version = self.version() + 1
        document = {
            "version": version,
            "equipment": {item.id: item.to_dict() for item in equipment},
            "points": {point.id: point.to_dict() for point in points},
        }
        self._store.put(keys.TOPOLOGY_STATE, document)
        return version
