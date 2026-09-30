"""Key layout of the persisted control state."""

PLANT_STATE = "plant/state"
PLANT_STARTED_AT = "plant/started_at"
PLANT_STOPPED_AT = "plant/stopped_at"
PLANT_MAINTAINED_AT = "plant/maintained_at"

AUDIT_SEQUENCE = "audit/sequence"
AUDIT_EVENT_PREFIX = "audit/event/"
AUDIT_LAST_PREFIX = "audit/last/"
TOPOLOGY_CURRENT = "topology/current"
TOPOLOGY_VERSION_PREFIX = "topology/version/"
TOPOLOGY_REPLACEMENT_PREFIX = "topology/replacement/"


def topology_version_key(version: int) -> str:
    """Key holding an immutable topology version snapshot."""
    if version < 1:
        raise ValueError("topology versions start at one")
    return f"{TOPOLOGY_VERSION_PREFIX}{version:06d}"


def topology_replacement_key(old_id: str) -> str:
    """Key holding the latest replacement record for a historical identifier."""
    if not old_id:
        raise ValueError("replacement identifier must not be empty")
    return f"{TOPOLOGY_REPLACEMENT_PREFIX}{old_id}"


def audit_event_key(sequence: int) -> str:
    """Key holding the event written at ``sequence``."""
    if sequence < 1:
        raise ValueError("audit sequence starts at one")
    return f"{AUDIT_EVENT_PREFIX}{sequence:06d}"


def audit_last_key(kind: str) -> str:
    """Key holding the sequence of the latest event of ``kind``."""
    return f"{AUDIT_LAST_PREFIX}{kind}"
