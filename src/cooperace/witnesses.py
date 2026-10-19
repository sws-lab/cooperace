# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Which witness file of a component run counts as the witness of its verdict,
by the witness formats SV-COMP 2027 accepts in the C.*.Concurrency categories
(FORMATS). A file counts only if `passes` holds for its text: it is a YAML
list of entries, exactly one of which has the entry_type of the verdict
("violation_sequence" for false, "invariant_set" for true), with a
metadata.format_version in the bounds of the WitnessFormat and a
metadata.task.specification whose formulas are those of the property, or of
the sub-property for valid-memsafety (properties.same_formulas). A GraphML
file never counts. passing_witness picks the file of a run that counts.

The check reads the witness's kind, version and property and nothing else:
it does not establish that the file follows the format's schema or that a
validator confirms it.

Imports PyYAML, which BenchExec's benchexec.model imports too; the
competition image has it as python3-yaml."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import yaml

from .properties import (
    FORMULAS,
    NO_DATA_RACE,
    NO_OVERFLOW,
    SUBPROPERTY_FORMULAS,
    UNREACH_CALL,
    VALID_MEMSAFETY,
    same_formulas,
)

VIOLATION = "violation_sequence"
CORRECTNESS = "invariant_set"

# The highest format version SV-COMP 2027 accepts outside demo mode. Its rules
# page also lists 2.4 in demo mode, but no C.*.Concurrency cell names 2.4.
HIGHEST_VERSION = (2, 3)


@dataclass(frozen=True)
class WitnessFormat:
    """The witness a verdict can be delivered with: an entry of `entry_type`
    whose format_version is at least `lowest` and at most `highest`, as
    (major, minor). `required` is whether SV-COMP scores the verdict only
    with such a witness; if not, the witness is in demo mode and is
    validated without affecting the score."""

    entry_type: str
    lowest: tuple[int, int]
    highest: tuple[int, int] = HIGHEST_VERSION
    required: bool = True


# The witness formats of SV-COMP 2027 for C.*.Concurrency, from its rules page
# (a draft read 2026-10-19), by property and verdict. A (property, verdict)
# without an entry has no witness format ("not supported"): nothing is
# delivered with it, and it scores without one.
FORMATS = {
    (NO_DATA_RACE, "false"): WitnessFormat(VIOLATION, (2, 2)),
    (UNREACH_CALL, "true"): WitnessFormat(CORRECTNESS, (2, 1)),
    (UNREACH_CALL, "false"): WitnessFormat(VIOLATION, (2, 2)),
    (NO_OVERFLOW, "true"): WitnessFormat(CORRECTNESS, (2, 1)),
    (NO_OVERFLOW, "false"): WitnessFormat(VIOLATION, (2, 2)),
    (VALID_MEMSAFETY, "false"): WitnessFormat(VIOLATION, (2, 2), required=False),
}

# A violation of valid-memtrack needs format 2.3, the first that can express it.
_MEMTRACK = WitnessFormat(VIOLATION, (2, 3), required=False)

_VERSION = re.compile(r"(\d+)\.(\d+)")

# The C loader of PyYAML where it was built with libyaml, else the Python one
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def witness_format(property_name: str, verdict: str, subproperty: str | None = None) -> WitnessFormat | None:
    """The WitnessFormat of the verdict `verdict` ("true" or "false") on the
    property `property_name` (a key of properties.FORMULAS), with, for a
    false of valid-memsafety, the sub-property `subproperty` it names ("valid-free",
    "valid-deref" or "valid-memtrack"); None if no witness counts for it.
    `subproperty` is ignored for the other properties."""
    if property_name == VALID_MEMSAFETY and verdict == "false" and subproperty == "valid-memtrack":
        return _MEMTRACK
    return FORMATS.get((property_name, verdict))


def needs_witness(property_name: str, verdict: str) -> bool:
    """Whether SV-COMP 2027 scores the verdict `verdict` on the property
    `property_name` only with a witness that passes: true for a false of
    no-data-race, unreach-call and no-overflow, and a true of the last two."""
    format_ = FORMATS.get((property_name, verdict))
    return format_ is not None and format_.required


def specification(property_name: str, verdict: str, subproperty: str | None = None) -> tuple[str, ...] | None:
    """The formulas a witness of the verdict `verdict` on `property_name`
    names as its metadata.task.specification: the property's FORMULAS, or,
    for valid-memsafety, the formula of the sub-property `subproperty` of a
    false, None if `subproperty` is not one of SUBPROPERTY_FORMULAS."""
    if property_name == VALID_MEMSAFETY:
        if verdict != "false" or subproperty not in SUBPROPERTY_FORMULAS:
            return None
        return (SUBPROPERTY_FORMULAS[subproperty],)
    return FORMULAS[property_name]


def subproperty_of(result: str) -> str | None:
    """The sub-property a result "false(<name>)" names, such as
    "valid-deref" in components.reported_result's "false(valid-deref)", or
    None for a result without parentheses."""
    match = re.fullmatch(r"false\((.*)\)", result)
    return match.group(1) if match else None


def version_of(value: object) -> tuple[int, int] | None:
    """A metadata.format_version as (major, minor): from a string "2.2", or
    from the number 2.2 that YAML reads an unquoted 2.2 as; None for
    anything else."""
    if isinstance(value, bool) or not isinstance(value, (str, float)):
        return None
    match = _VERSION.fullmatch(str(value).strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def passes(text: str, property_name: str, verdict: str, subproperty: str | None = None) -> bool:
    """Whether `text`, the content of a witness file, counts as the witness
    of the verdict `verdict` on `property_name` (with `subproperty` as in
    witness_format): False if witness_format has no format for it; else
    `text` parses as YAML to a list of mappings, exactly one of which has the
    format's entry_type, and that entry's metadata.format_version
    (version_of) is in the format's bounds and its
    metadata.task.specification is a string with the formulas of
    `specification` (properties.same_formulas). GraphML text does not parse
    to a list, so it never passes."""
    format_ = witness_format(property_name, verdict, subproperty)
    formulas = specification(property_name, verdict, subproperty)
    if format_ is None or formulas is None:
        return False
    try:
        entries = yaml.load(text, Loader=_LOADER)
    except (yaml.YAMLError, RecursionError):
        return False
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        return False
    matching = [entry for entry in entries if entry.get("entry_type") == format_.entry_type]
    if len(matching) != 1:
        return False
    metadata = matching[0].get("metadata")
    if not isinstance(metadata, dict):
        return False
    version = version_of(metadata.get("format_version"))
    if version is None or not format_.lowest <= version <= format_.highest:
        return False
    task = metadata.get("task")
    spec = task.get("specification") if isinstance(task, dict) else None
    return isinstance(spec, str) and same_formulas(spec, formulas)


def file_passes(path: str, property_name: str, verdict: str, subproperty: str | None = None) -> bool:
    """`passes` for the file `path`, read as UTF-8; False for a file whose
    name ends in .graphml, and for one that cannot be read or decoded."""
    if path.lower().endswith(".graphml"):
        return False
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read()
    except (OSError, UnicodeDecodeError):
        return False
    return passes(text, property_name, verdict, subproperty)


def passing_witness(paths: Iterable[str], property_name: str, verdict: str,
                    subproperty: str | None = None) -> str | None:
    """The first of the witness files `paths` of one component run for which
    file_passes holds, for the verdict `verdict` on `property_name` with
    `subproperty`; None if none does."""
    for path in paths:
        if file_passes(path, property_name, verdict, subproperty):
            return path
    return None
