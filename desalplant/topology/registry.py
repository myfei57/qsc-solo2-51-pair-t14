"""Persistent topology registry with immutable versions and rename history."""

import json
from copy import deepcopy
from typing import Any

from ..clock import LogicalClock
from ..audit.recorder import Recorder
from ..errors import (
    InvalidTopologyError,
    PermissionDeniedError,
    TopologyConflictError,
    UnknownKeyError,
    UnknownTopologyError,
)
from ..store import keys
from ..store.checksum import digest
from ..store.store import Store
from .auth import Principal
from .model import validate_batch

EMPTY_TOPOLOGY = {
    "version": 0,
    "devices": {},
    "probes": {},
    "history": {},
    "replacements": {},
}


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_payload(principal: Principal, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "actor": principal.user,
        "roles": sorted(principal.roles),
        "violations": result.get("violations", []),
        "checksum": result.get("checksum", ""),
    }
    if "version" in result:
        payload["version"] = result["version"]
    if result.get("dry_run"):
        payload["dry_run"] = True
    if result.get("renames") is not None:
        payload["renames"] = result["renames"]
    return payload


class TopologyRegistry:
    """Validates, commits and resolves registered topology versions."""

    def __init__(self, store: Store, clock: LogicalClock, recorder: Recorder) -> None:
        self._store = store
        self._clock = clock
        self._recorder = recorder

    def current(self) -> dict[str, Any]:
        """Detached projection of the active topology version."""
        row = self._store.get(keys.TOPOLOGY_CURRENT)
        if row is None:
            return deepcopy(EMPTY_TOPOLOGY)
        return self._projection(deepcopy(row))

    def version(self, number: int) -> dict[str, Any]:
        """Immutable projection of one committed topology version."""
        if number < 1:
            raise UnknownTopologyError(f"unknown topology version {number}")
        row = self._store.get(keys.topology_version_key(number))
        if row is None:
            raise UnknownTopologyError(f"unknown topology version {number}")
        return self._projection(deepcopy(row))

    def versions(self) -> tuple[dict[str, Any], ...]:
        """Metadata for every immutable topology version, oldest first."""
        return tuple(
            {
                "version": number,
                "device_count": len(self.version(number)["devices"]),
                "probe_count": len(self.version(number)["probes"]),
                "replacement_count": len(self.version(number)["replacements"]),
            }
            for number in range(1, self.current()["version"] + 1)
        )

    def validate(self, batch: Any, expected_version: int | None = None) -> dict[str, Any]:
        """Validate a batch without changing the registry."""
        active = self.current()
        if expected_version is not None and expected_version != active["version"]:
            self._recorder.append(
                "topology.validation.rejected",
                _stable(
                    {
                        "reason": "version_conflict",
                        "expected": expected_version,
                        "current": active["version"],
                    }
                ),
                data={
                    "reason": "version_conflict",
                    "expected_version": expected_version,
                    "current_version": active["version"],
                },
            )
            raise TopologyConflictError(
                f"expected topology version {active['version']}, got {expected_version}"
            )
        normalized, violations = validate_batch(batch, active)
        result = {
            "valid": not violations,
            "current_version": active["version"],
            "violations": [violation.to_dict() for violation in violations],
            "checksum": digest(normalized),
        }
        if not violations:
            result["candidate"] = normalized
        return result

    def submit(self, batch: Any, principal: Principal, dry_run: bool = False, expected_version: int | None = None) -> dict[str, Any]:
        """Validate a complete batch and atomically commit it only when all checks pass."""
        result = self.validate(batch, expected_version)
        active = self.current()
        result["dry_run"] = dry_run
        if not result["valid"]:
            self._clock.advance()
            event = self._recorder.append(
                "topology.validation.rejected",
                _stable({"violations": result["violations"]}),
                data=_event_payload(principal, result),
            )
            raise InvalidTopologyError(result["violations"], event)

        candidate = result.pop("candidate")
        has_renames = bool(candidate["renames"])
        has_structure_changes = self._has_structure_changes(active, candidate)
        if not principal.can_register(has_renames, has_structure_changes):
            denied = {
                "actor": principal.user,
                "roles": sorted(principal.roles),
                "current_version": active["version"],
                "checksum": result["checksum"],
                "dry_run": dry_run,
            }
            self._clock.advance()
            event = self._recorder.append(
                "topology.permission.denied",
                _stable({"actor": principal.user}),
                data=denied,
            )
            raise PermissionDeniedError(f"{principal.user} is not allowed to change topology") from None

        result["version"] = active["version"] if dry_run else active["version"] + 1
        if dry_run:
            self._clock.advance()
            event = self._recorder.append(
                "topology.validation.accepted",
                _stable({"version": result["version"], "dry_run": True}),
                data=_event_payload(principal, result),
            )
            result["event"] = event.to_dict()
            result["violations"] = []
            result["valid"] = True
            return result

        committed = self._commit(active, candidate, principal)
        result.update(committed)
        return result

    def resolve(self, identifier: str, at_version: int | None = None) -> dict[str, Any]:
        """Resolve an identifier as it was understood at a version."""
        active = self.current()
        target_version = active["version"] if at_version is None else at_version
        if target_version < 1 or target_version > active["version"]:
            raise UnknownTopologyError(f"unknown topology version {target_version}")

        candidate = identifier
        chain: list[dict[str, Any]] = []
        while True:
            node = self._node_at(candidate, target_version)
            if node is not None:
                return {
                    "requested_id": identifier,
                    "resolved_id": candidate,
                    "version": target_version,
                    "kind": "device" if candidate in self.version(target_version)["devices"] else "probe",
                    "node": node,
                    "chain": chain,
                    "historical": candidate != identifier,
                }
            next_id = self._replacement_at(candidate, target_version)
            if next_id is None:
                raise UnknownKeyError(f"identifier {identifier} is not present at version {target_version}")
            replacement = deepcopy(self.version(target_version)["replacements"].get(candidate, {}))
            replacement["from_id"] = candidate
            replacement["to_id"] = next_id
            chain.append(replacement)
            candidate = next_id

    def replacement(self, old_id: str) -> dict[str, Any]:
        """Return the replacement chain starting from one historical identifier."""
        active = self.current()
        if old_id in active["devices"] or old_id in active["probes"]:
            raise UnknownTopologyError(f"identifier {old_id} is still active")
        if old_id not in active["history"]:
            raise UnknownTopologyError(f"unknown historical identifier {old_id}")
        chain: list[dict[str, Any]] = []
        current_id = old_id
        while current_id in active["replacements"]:
            replacement = deepcopy(active["replacements"][current_id])
            replacement["from_id"] = current_id
            chain.append(replacement)
            current_id = replacement["to_id"]
        return {
            "old_id": old_id,
            "current_id": current_id,
            "active": current_id in active["devices"] or current_id in active["probes"],
            "chain": chain,
        }

    def audit_events(self, limit: int = 0) -> tuple[dict[str, Any], ...]:
        """Topology conclusions and replacements visible to the console."""
        kinds = (
            "topology.committed",
            "topology.validation.accepted",
            "topology.validation.rejected",
            "topology.permission.denied",
        )
        collected: list[dict[str, Any]] = []
        for event in self._recorder.events():
            if event.kind in kinds:
                collected.append(event.to_dict())
                if limit and len(collected) >= limit:
                    break
        return tuple(collected)

    def _commit(self, active: dict[str, Any], candidate: dict[str, Any], principal: Principal) -> dict[str, Any]:
        version_number = active["version"] + 1
        history = deepcopy(active["history"])
        replacements = deepcopy(active["replacements"])

        active_ids = set(active["devices"]) | set(active["probes"])
        desired_ids = set(candidate["devices"]) | set(candidate["probes"])
        for removed_id in sorted(active_ids - desired_ids):
            history[removed_id] = {"status": "retired", "version": version_number}

        for rename in candidate["renames"]:
            old_id = rename["old_id"]
            new_id = rename["new_id"]
            history[old_id] = {
                "status": "replaced",
                "version": version_number,
                "replaced_by": new_id,
            }
            replacements[old_id] = {
                "to_id": new_id,
                "kind": rename["kind"],
                "effective_version": version_number,
                "actor": principal.user,
            }

        committed = {
            "version": version_number,
            "devices": deepcopy(candidate["devices"]),
            "probes": deepcopy(candidate["probes"]),
            "history": history,
            "replacements": replacements,
        }
        result = {
            "valid": True,
            "violations": [],
            "current_version": version_number,
            "version": version_number,
            "checksum": digest(committed),
            "dry_run": False,
        }
        self._clock.advance()

        def write(event, data) -> None:
            data[keys.TOPOLOGY_CURRENT] = committed
            data[keys.topology_version_key(version_number)] = committed
            for old_id, replacement in replacements.items():
                if replacement["effective_version"] == version_number:
                    data[keys.topology_replacement_key(old_id)] = replacement

        event = self._recorder.append(
            "topology.committed",
            _stable({"version": version_number, "actor": principal.user}),
            write=write,
            data=_event_payload(principal, {**result, "renames": candidate["renames"]}),
        )
        result["event"] = event.to_dict()
        return result

    @staticmethod
    def _has_structure_changes(active: dict[str, Any], candidate: dict[str, Any]) -> bool:
        if active["version"] == 0:
            return True

        rename_to_old = {rename["new_id"]: rename["old_id"] for rename in candidate["renames"]}

        def previous_id(identifier: str) -> str:
            return rename_to_old.get(identifier, identifier)

        def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
            normalized = deepcopy(node)
            normalized.pop("replaces", None)
            normalized["id"] = previous_id(normalized["id"])
            if "device_id" in normalized:
                normalized["device_id"] = previous_id(normalized["device_id"])
            if "parent_id" in normalized:
                normalized["parent_id"] = previous_id(normalized["parent_id"])
            if "probe_ids" in normalized:
                normalized["probe_ids"] = sorted(previous_id(item) for item in normalized["probe_ids"])
            return normalized

        active_graph = {
            kind: {
                identifier: {key: value for key, value in node.items() if key != "replaces"}
                for identifier, node in nodes.items()
            }
            for kind, nodes in (("devices", active["devices"]), ("probes", active["probes"]))
        }
        candidate_graph = {
            "devices": {
                previous_id(identifier): normalize_node(node)
                for identifier, node in candidate["devices"].items()
            },
            "probes": {
                previous_id(identifier): normalize_node(node)
                for identifier, node in candidate["probes"].items()
            },
        }
        return _stable(active_graph) != _stable(candidate_graph)

    @staticmethod
    def _projection(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": int(row.get("version", 0)),
            "devices": deepcopy(row.get("devices", {})),
            "probes": deepcopy(row.get("probes", {})),
            "history": deepcopy(row.get("history", {})),
            "replacements": deepcopy(row.get("replacements", {})),
        }

    def _node_at(self, identifier: str, number: int) -> dict[str, Any] | None:
        snapshot = self.version(number)
        return deepcopy(snapshot["devices"].get(identifier) or snapshot["probes"].get(identifier))

    def _replacement_at(self, identifier: str, number: int) -> str | None:
        replacement = self.version(number)["replacements"].get(identifier)
        if replacement is None:
            return None
        return str(replacement["to_id"])
