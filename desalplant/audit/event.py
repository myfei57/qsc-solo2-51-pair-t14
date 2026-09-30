"""One row of the append-only event record."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Event:
    """An immutable record of something the line did."""

    sequence: int
    kind: str
    detail: str
    tick: int = 0
    id: str = ""
    data: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection used by the console."""
        row = {
            "id": self.id,
            "sequence": self.sequence,
            "kind": self.kind,
            "detail": self.detail,
            "tick": self.tick,
        }
        if self.data is not None:
            row["data"] = dict(self.data)
        return row

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Event":
        """Rebuild an event from a persisted row."""
        data = row.get("data")
        return cls(
            sequence=int(row["sequence"]),
            kind=str(row["kind"]),
            detail=str(row.get("detail", "")),
            tick=int(row.get("tick", 0)),
            id=str(row.get("id", "")),
            data=dict(data) if isinstance(data, Mapping) else None,
        )
