"""Append-only, immutable migration chain for topology renames.

A rename is recorded as a migration with a version. The chain is never
rewritten: historical references are resolved by walking the renames that were
in effect at the version the reference was made.
"""

from ..ns.ids import new_id as generate_id
from ..store import keys
from ..store.store import Store
from .record import Migration


class MigrationChain:
    """The immutable history of topology renames."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def record(
        self,
        version: int,
        old_id: str,
        new_id: str,
        actor: str,
        reason: str,
        tick: int,
    ) -> Migration:
        """Append a rename migration and return it."""
        migration = Migration(
            version=version,
            old_id=old_id,
            new_id=new_id,
            actor=actor,
            reason=reason,
            tick=tick,
            id=generate_id(),
        )
        self._store.put(keys.topology_migration_key(version), migration.to_dict())
        return migration

    def history(self) -> tuple[Migration, ...]:
        """All migrations in version order."""
        migrations = []
        for key in self._store.keys(keys.TOPOLOGY_MIGRATION_PREFIX):
            row = self._store.get(key)
            if isinstance(row, dict):
                migrations.append(Migration.from_dict(row))
        return tuple(sorted(migrations, key=lambda item: item.version))

    def replacement(self, old_id: str) -> Migration | None:
        """The migration that replaced ``old_id``, or ``None``."""
        for migration in self.history():
            if migration.old_id == old_id:
                return migration
        return None

    def resolve(self, tag: str, at_version: int) -> str:
        """Resolve ``tag`` through renames in effect at ``at_version``.

        Walks the chain from ``tag``, following each rename whose version is at
        most ``at_version``. The result is how the tag was known at that
        version: a historical reference is interpreted by the naming in effect
        when it was made.
        """
        current = tag
        for migration in self.history():
            if migration.version > at_version:
                break
            if migration.old_id == current:
                current = migration.new_id
        return current
