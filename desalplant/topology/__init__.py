"""Equipment topology registry: naming rules, validation, versioned migration.

Measurement points and equipment are registered by naming rules. A configuration
change is validated as a whole before it is committed, so a partial failure
rejects the entire batch. Renames are versioned migrations: the old id is
traceable to who replaced it at which version, and historical references are
resolved by the naming in effect when they were made.
"""

from .migration import MigrationChain
from .permission import PERMISSIONS, Role, can, can_rename, parse_role
from .record import Equipment, MeasurementPoint, Migration
from .registry import TopologyRegistry
from .service import TopologyService
from .validation import validate_state

__all__ = [
    "PERMISSIONS",
    "Equipment",
    "MeasurementPoint",
    "Migration",
    "MigrationChain",
    "Role",
    "TopologyRegistry",
    "TopologyService",
    "can",
    "can_rename",
    "parse_role",
    "validate_state",
]
