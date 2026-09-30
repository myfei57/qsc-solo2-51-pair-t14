"""Key layout of the persisted control state."""

PLANT_STATE = "plant/state"
PLANT_STARTED_AT = "plant/started_at"
PLANT_STOPPED_AT = "plant/stopped_at"
PLANT_MAINTAINED_AT = "plant/maintained_at"

AUDIT_SEQUENCE = "audit/sequence"
AUDIT_EVENT_PREFIX = "audit/event/"
AUDIT_LAST_PREFIX = "audit/last/"

TOPOLOGY_STATE = "topology/state"
TOPOLOGY_MIGRATION_PREFIX = "topology/migration/"


def audit_event_key(sequence: int) -> str:
    """Key holding the event written at ``sequence``."""
    if sequence < 1:
        raise ValueError("audit sequence starts at one")
    return f"{AUDIT_EVENT_PREFIX}{sequence:06d}"


def audit_last_key(kind: str) -> str:
    """Key holding the sequence of the latest event of ``kind``."""
    return f"{AUDIT_LAST_PREFIX}{kind}"


def topology_migration_key(version: int) -> str:
    """Key holding the rename migration recorded at ``version``."""
    if version < 1:
        raise ValueError("migration version starts at one")
    return f"{TOPOLOGY_MIGRATION_PREFIX}{version:06d}"
