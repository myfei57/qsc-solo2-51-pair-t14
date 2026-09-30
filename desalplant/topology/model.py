"""Validation and normalization for a whole topology registration batch."""

from copy import deepcopy
from typing import Any

from .naming import Violation, is_device_id, is_probe_id, kind_for, stage_from_id, validate_id


def _record(violations: list[Violation], code: str, message: str, path: str = "") -> None:
    violations.append(Violation(code=code, message=message, path=path))


def _integer_stage(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return list(value)


def _normalize_node(row: dict[str, Any], kind: str, index: int, violations: list[Violation]) -> dict[str, Any] | None:
    path = f"{kind}s/{index}"
    identifier = row.get("id")
    id_errors = validate_id(identifier, kind, f"{path}/id")
    if id_errors:
        violations.extend(id_errors)
        return None

    normalized: dict[str, Any] = {"id": identifier}
    if kind == "device":
        area = row.get("area", "")
        if not isinstance(area, str) or not area:
            _record(violations, "missing_area", "device must declare an owning area", f"{path}/area")
        else:
            normalized["area"] = area

    stage = _integer_stage(row.get("stage"))
    if stage < 0:
        _record(violations, "invalid_stage", "stage must be a positive integer or zero", f"{path}/stage")
    elif stage:
        normalized["stage"] = stage

    encoded_stage = stage_from_id(identifier)
    explicit_stage = stage if stage > 0 else 0
    if encoded_stage and explicit_stage and encoded_stage != explicit_stage:
        _record(
            violations,
            "stage_mismatch",
            f"identifier stage s{encoded_stage} does not match declared stage {explicit_stage}",
            path,
        )

    if kind == "probe":
        device_id = row.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            _record(violations, "missing_owner", "probe must declare one owning device", f"{path}/device_id")
        else:
            normalized["device_id"] = device_id
        if "area" in row:
            area = row.get("area")
            if not isinstance(area, str) or not area:
                _record(violations, "invalid_area", "probe area must be a non-empty string", f"{path}/area")
            else:
                normalized["area"] = area
    else:
        parent_id = row.get("parent_id")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                _record(violations, "invalid_parent", "parent_id must reference a device", f"{path}/parent_id")
            else:
                normalized["parent_id"] = parent_id
        probe_ids = _string_list(row.get("probe_ids"))
        if probe_ids is None:
            _record(violations, "invalid_probe_list", "probe_ids must be a list of probe identifiers", f"{path}/probe_ids")
        elif probe_ids:
            normalized["probe_ids"] = sorted(probe_ids)

    attributes = row.get("attributes", {})
    if not isinstance(attributes, dict) or any(not isinstance(key, str) for key in attributes):
        _record(violations, "invalid_attributes", "attributes must be an object with string keys", f"{path}/attributes")
    elif attributes:
        normalized["attributes"] = deepcopy(attributes)

    replaces = row.get("replaces")
    if replaces is not None:
        if not isinstance(replaces, str) or not replaces:
            _record(violations, "invalid_replacement", "replaces must identify one previous node", f"{path}/replaces")
        elif replaces == identifier:
            _record(violations, "empty_rename", "replaces must differ from the new identifier", f"{path}/replaces")
        else:
            normalized["replaces"] = replaces

    return normalized


def _rows(batch: dict[str, Any], name: str, violations: list[Violation]) -> list[Any]:
    rows = batch.get(name, [])
    if not isinstance(rows, list):
        _record(violations, "invalid_batch", f"{name} must be a list", name)
        return []
    return rows


def _replacement_kind(old_id: str, declared: str) -> str:
    inferred = kind_for(old_id)
    return inferred or declared


def validate_batch(batch: Any, current: dict[str, Any]) -> tuple[dict[str, Any], list[Violation]]:
    """Validate and normalize a desired full topology against the active version."""
    violations: list[Violation] = []
    if not isinstance(batch, dict):
        return {}, [Violation("invalid_batch", "batch must be a JSON object")]

    raw_devices = _rows(batch, "devices", violations)
    raw_probes = _rows(batch, "probes", violations)
    if not raw_devices and not raw_probes:
        _record(violations, "empty_batch", "topology batch must register at least one node")

    devices: dict[str, dict[str, Any]] = {}
    probes: dict[str, dict[str, Any]] = {}
    renames: list[dict[str, str]] = []

    for kind, rows, target in (
        ("device", raw_devices, devices),
        ("probe", raw_probes, probes),
    ):
        for index, row in enumerate(rows):
            path = f"{kind}s/{index}"
            if not isinstance(row, dict):
                _record(violations, "invalid_node", f"{kind} entry must be an object", path)
                continue
            normalized = _normalize_node(row, kind, index, violations)
            if normalized is None:
                continue
            identifier = normalized["id"]
            if identifier in devices or identifier in probes:
                _record(violations, "duplicate_id", "identifier is registered more than once", f"{path}/id")
                continue
            target[identifier] = normalized
            if normalized.get("replaces"):
                old_id = normalized["replaces"]
                if any(item["old_id"] == old_id for item in renames):
                    _record(violations, "duplicate_replacement", "previous identifier is replaced more than once", f"{path}/replaces")
                else:
                    renames.append(
                        {
                            "old_id": old_id,
                            "new_id": identifier,
                            "kind": _replacement_kind(old_id, kind),
                        }
                    )

    desired_ids = set(devices) | set(probes)
    current_devices: dict[str, dict[str, Any]] = current.get("devices", {})
    current_probes: dict[str, dict[str, Any]] = current.get("probes", {})
    current_ids = set(current_devices) | set(current_probes)
    history: dict[str, dict[str, Any]] = current.get("history", {})

    for node_id in desired_ids:
        if node_id in history:
            _record(violations, "historical_id_reused", "retired or replaced identifiers are immutable", node_id)

    for replacement in renames:
        old_id = replacement["old_id"]
        new_id = replacement["new_id"]
        kind = replacement["kind"]
        path = f"{kind}s/{new_id}/replaces"
        if old_id in desired_ids:
            _record(violations, "replaced_id_still_active", "replaced identifier cannot remain in the desired topology", path)
        if old_id in history:
            _record(violations, "already_replaced", "identifier has already been replaced and cannot be replaced again", path)
        if current.get("version", 0) > 0:
            active_node = current_devices.get(old_id) if kind == "device" else current_probes.get(old_id)
            other_active = old_id in current_ids and active_node is None
            if active_node is None:
                _record(violations, "unknown_replacement_source", "replaces must reference an identifier in the active version", path)
            if other_active:
                _record(violations, "replacement_kind_mismatch", "replacement changes the topology kind of an identifier", path)
        declared_kind = kind_for(new_id)
        if declared_kind and declared_kind != kind:
            _record(violations, "replacement_kind_mismatch", "new identifier does not match the replacement topology kind", path)

    owners: dict[str, str] = {}
    for probe_id, probe in probes.items():
        device_id = probe.get("device_id")
        if isinstance(device_id, str) and device_id:
            owners[probe_id] = device_id

    listed_probe_owners: dict[str, str] = {}
    for device_id, device in devices.items():
        parent_id = device.get("parent_id")
        if parent_id is not None and parent_id not in devices:
            _record(violations, "dangling_parent", "parent device is absent from the desired topology", f"devices/{device_id}/parent_id")
        for probe_id in device.get("probe_ids", []):
            listed_owner = listed_probe_owners.get(probe_id)
            if listed_owner is not None and listed_owner != device_id:
                _record(
                    violations,
                    "duplicate_probe_listing",
                    "probe is listed by multiple devices",
                    f"devices/{device_id}/probe_ids",
                )
            listed_probe_owners[probe_id] = device_id
            owner = owners.get(probe_id)
            if owner is not None and owner != device_id:
                _record(
                    violations,
                    "duplicate_probe_owner",
                    "probe is listed by a device other than its single owner",
                    f"devices/{device_id}/probe_ids",
                )
            if not is_probe_id(probe_id):
                _record(violations, "invalid_probe_ref", "probe reference must use a probe identifier", f"devices/{device_id}/probe_ids")
            elif probe_id not in probes:
                _record(violations, "dangling_probe", "probe reference points to an absent probe", f"devices/{device_id}/probe_ids")
            elif probes[probe_id].get("device_id") != device_id:
                _record(violations, "ownership_conflict", "listed probe belongs to another device", f"devices/{device_id}/probe_ids")

    for probe_id, owner in owners.items():
        if listed_probe_owners.get(probe_id) != owner:
            _record(
                violations,
                "missing_probe_listing",
                "probe owner must explicitly list every attached probe",
                f"devices/{owner}/probe_ids",
            )

    for probe_id, probe in probes.items():
        device_id = probe.get("device_id")
        if not isinstance(device_id, str):
            continue
        if not is_device_id(device_id) or device_id not in devices:
            _record(violations, "dangling_owner", "probe points to an absent or invalid device", f"probes/{probe_id}/device_id")
            continue
        owner_stage = devices[device_id].get("stage") or stage_from_id(device_id)
        probe_stage = probe.get("stage") or stage_from_id(probe_id)
        if owner_stage and probe_stage and owner_stage != probe_stage:
            _record(violations, "stage_ownership_mismatch", "probe stage does not match its owning device stage", f"probes/{probe_id}")

    normalized_batch = {"devices": devices, "probes": probes, "renames": renames}
    return normalized_batch, violations
