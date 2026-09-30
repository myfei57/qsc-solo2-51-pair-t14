"""Write side of the append-only event record."""

from ..clock import LogicalClock
from ..ns.ids import new_id
from ..store import keys
from ..store.store import Store
from . import query
from .event import Event


class Recorder:
    """Appends events and keeps their running sequence number."""

    def __init__(self, store: Store, clock: LogicalClock | None = None) -> None:
        self._store = store
        self._clock = clock
        self._sequence = int(store.get(keys.AUDIT_SEQUENCE, 0) or 0)

    @property
    def sequence(self) -> int:
        """Sequence number of the newest event."""
        return self._sequence

    def log(self, kind: str, detail: str) -> Event:
        """Append an event and return it."""
        return self.append(kind, detail)

    def append(self, kind: str, detail: str, write=None, data=None) -> Event:
        """Append an event, allowing a caller to join other state into the flush."""
        if not kind:
            raise ValueError("event kind must not be empty")

        def mutate(store_data) -> Event:
            event = Event(
                sequence=int(store_data.get(keys.AUDIT_SEQUENCE, 0) or 0) + 1,
                kind=kind,
                detail=detail,
                tick=self._clock.tick if self._clock is not None else 0,
                id=new_id(),
                data=data,
            )
            store_data[keys.AUDIT_SEQUENCE] = event.sequence
            store_data[keys.audit_event_key(event.sequence)] = event.to_dict()
            store_data[keys.audit_last_key(kind)] = event.sequence
            if write is not None:
                write(event, store_data)
            return event

        event = self._store.update(mutate)
        self._sequence = event.sequence
        return event

    def count(self) -> int:
        """Number of events recorded so far."""
        return self._sequence

    def events(self, limit: int = 0) -> tuple[Event, ...]:
        """Newest-first events, capped at ``limit`` when it is positive."""
        return query.list_events(self._store, self._sequence, limit)

    def latest(self, kind: str) -> Event | None:
        """Newest event of a kind, or ``None`` when there is none."""
        sequence = query.latest_sequence(self._store, kind)
        if sequence <= 0:
            return None
        return query.event_at(self._store, sequence)
