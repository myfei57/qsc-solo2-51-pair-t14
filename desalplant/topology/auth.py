"""Authorization for topology changes.

The service trusts X-Topology-* headers only when an authenticating gateway
strips client-supplied values. Renaming identifiers is intentionally a separate
permission from ordinary equipment registration.
"""

from dataclasses import dataclass

ADMIN = "topology-admin"
EDITOR = "topology-editor"
RENAMER = "topology-renamer"
ROLES = frozenset({ADMIN, EDITOR, RENAMER})


@dataclass(frozen=True)
class Principal:
    """Authenticated caller and the roles trusted by the console."""

    user: str
    roles: frozenset[str]

    @classmethod
    def anonymous(cls) -> "Principal":
        """Caller used before the authenticating gateway supplies an identity."""
        return cls(user="anonymous", roles=frozenset())

    @classmethod
    def from_header(cls, raw_roles: str = "", user: str = "") -> "Principal":
        """Parse the canonical ``role1,role2`` console header."""
        roles = frozenset(part.strip() for part in raw_roles.split(",") if part.strip())
        return cls(user=user.strip() or "anonymous", roles=roles)

    def can_view(self) -> bool:
        """Topology history is operational information visible to the console."""
        return True

    def can_register(self, has_renames: bool, has_structure_changes: bool) -> bool:
        """Whether this principal may submit the requested batch kind."""
        if ADMIN in self.roles:
            return True
        if has_renames and RENAMER not in self.roles:
            return False
        if has_structure_changes and EDITOR not in self.roles:
            return False
        return has_renames or has_structure_changes
