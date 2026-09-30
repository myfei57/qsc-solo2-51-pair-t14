"""Naming rules for registered equipment and probes."""

import re
from dataclasses import dataclass

DEVICE_PREFIX = "eq-"
PROBE_PREFIXES = ("pt-", "tt-", "ft-", "at-", "lt-")
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
DEVICE_PATTERN = re.compile(rf"^{re.escape(DEVICE_PREFIX)}{SLUG}$")
PROBE_PATTERN = re.compile(rf"^(?:{'|'.join(PROBE_PREFIXES)}){SLUG}$")
STAGE_PATTERN = re.compile(r"(?:^|-)s([1-9][0-9]*)(?:-|$)")


@dataclass(frozen=True)
class Violation:
    """One validation failure, attached to a stable logical location."""

    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict:
        """JSON-friendly projection."""
        return {"code": self.code, "path": self.path, "message": self.message}


def is_device_id(value: str) -> bool:
    """Whether ``value`` follows the equipment identifier rule."""
    return bool(DEVICE_PATTERN.fullmatch(value or ""))


def is_probe_id(value: str) -> bool:
    """Whether ``value`` follows a supported probe identifier rule."""
    return bool(PROBE_PATTERN.fullmatch(value or ""))


def kind_for(value: str) -> str:
    """Topology kind implied by a registered identifier."""
    if is_device_id(value):
        return "device"
    if is_probe_id(value):
        return "probe"
    return ""


def stage_from_id(value: str) -> int:
    """Positive membrane stage encoded as ``sN`` in an identifier, or zero."""
    match = STAGE_PATTERN.search(value or "")
    return int(match.group(1)) if match else 0


def validate_id(value: str, kind: str, path: str) -> list[Violation]:
    """Validate that an identifier is present and matches its kind."""
    if not isinstance(value, str) or not value:
        return [Violation("missing_id", "identifier must not be empty", path)]
    checker = is_device_id if kind == "device" else is_probe_id
    prefixes = DEVICE_PREFIX if kind == "device" else ", ".join(PROBE_PREFIXES)
    if not checker(value):
        return [
            Violation(
                "invalid_id",
                f"{kind} identifier must use the {prefixes} naming rule",
                path,
            )
        ]
    return []
