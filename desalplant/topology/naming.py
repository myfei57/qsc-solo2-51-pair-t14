"""Naming rules for topology measurement points and equipment.

A tag is the human-readable identifier of a measurement point. Equipment ids
name the physical assets the points belong to. Both follow a fixed rule so
that a configuration change can be checked before it is committed.
"""

import re

# ISA-style measurement point tag: two to four uppercase letters, a dash, three
# digits and an optional letter suffix. Examples: PT-101, FT-201A, TT-302.
POINT_PATTERN = re.compile(r"^[A-Z]{2,4}-\d{3}[A-Z]?$")

# Kebab-case equipment id: a leading letter followed by lowercase letters, digits
# and dashes. Examples: tank-a, pump-hp, ro-array-1.
EQUIPMENT_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Subsystems a piece of equipment or a point can belong to.
SYSTEMS = (
    "intake",
    "filter",
    "ro",
    "brine",
    "chem",
    "pump",
    "tank",
    "flush",
)


def valid_point_id(tag: str) -> bool:
    """Whether ``tag`` matches the measurement point naming rule."""
    return bool(POINT_PATTERN.match(tag))


def valid_equipment_id(identifier: str) -> bool:
    """Whether ``identifier`` matches the equipment naming rule."""
    return bool(EQUIPMENT_PATTERN.match(identifier))


def valid_system(system: str) -> bool:
    """Whether ``system`` is a known subsystem."""
    return system in SYSTEMS
