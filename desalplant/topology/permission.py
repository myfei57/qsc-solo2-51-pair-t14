"""Role-based permission for topology changes.

Only engineers and administrators may rename a tag, because a rename is a
migration that rewrites how historical references are interpreted. Viewers and
operators may read the topology; operators may commit a validated batch.
"""

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"


_VIEW = "topology.view"
_APPLY = "topology.apply"
_RENAME = "topology.rename"
_ADMIN = "topology.admin"

PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({_VIEW}),
    Role.OPERATOR: frozenset({_VIEW, _APPLY}),
    Role.ENGINEER: frozenset({_VIEW, _APPLY, _RENAME}),
    Role.ADMIN: frozenset({_VIEW, _APPLY, _RENAME, _ADMIN}),
}


def can(role: Role, permission: str) -> bool:
    """Whether ``role`` holds ``permission``."""
    return permission in PERMISSIONS[role]


def can_rename(role: Role) -> bool:
    """Whether ``role`` may rename a tag (the migration permission)."""
    return can(role, _RENAME)


def parse_role(value: str) -> Role:
    """Parse a role string, raising on an unknown role."""
    try:
        return Role(value)
    except ValueError as error:
        raise ValueError(f"unknown role {value!r}") from error
