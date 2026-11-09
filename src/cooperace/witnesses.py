# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Which witness file of a component run counts as the witness of its verdict,
by the witness formats SV-COMP accepts, which the witness table
conf/witnesses.json of TOOL_DIR records (conf/README.md, "The witness table").
A file counts only if `passes` holds for its text: it is a YAML list of
entries, of which those of the verdict's kind (WITNESS_ENTRY_TYPES: a
"violation_sequence" for false; an "invariant_set", a
"ghost_instrumentation" or both for true) each have a different entry_type,
one metadata.format_version in the bounds of the WitnessFormat, and a
metadata.task.specification that names the formulas of the property, or of
the sub-property for valid-memsafety (properties.names_formulas). A GraphML
file never counts. passing_witness picks the file of a run that counts.

The check reads the witness's kind, version and property and nothing else:
it does not establish that the file follows the format's schema or that a
validator confirms it. file_passes checks a file of more than
IN_PROCESS_BYTES in a Python process of its own, for at most CHECK_SECONDS.

load_table checks a witness table and raises WitnessTableError for one that
conf/README.md says CoOpeRace refuses; table reads conf/witnesses.json once
per process, and every function below that needs a format reads it from
there.

Imports PyYAML, which BenchExec's benchexec.model imports too; the
competition image has it as python3-yaml."""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import yaml

from . import TOOL_DIR
from .properties import (
    FORMULAS,
    SUBPROPERTY_FORMULAS,
    VALID_MEMSAFETY,
    names_formulas,
)

VIOLATION = "violation_sequence"
CORRECTNESS = "invariant_set"
GHOST = "ghost_instrumentation"

# The entry types a witness whose WitnessFormat has the entry_type of the key
# may consist of. A correctness witness of format 2.1 and higher may hold
# ghost_instrumentation beside, or instead of, its invariant_set: Goblint
# svcomp27 with witness.yaml.entry-types ["ghost_instrumentation"] writes only
# that entry, which WitnessLint 2.3 accepts as a correctness witness.
WITNESS_ENTRY_TYPES = {VIOLATION: (VIOLATION,), CORRECTNESS: (CORRECTNESS, GHOST)}

# The first format version that has the entry type ghost_instrumentation: a
# ghost entry of a lower version never passes, whatever the witness table says.
GHOST_SINCE = (2, 1)

# The entry_type a witness of each verdict has.
ENTRY_TYPES = {"true": CORRECTNESS, "false": VIOLATION}

# The witness table, relative to TOOL_DIR.
TABLE_FILE = os.path.join("conf", "witnesses.json")

# The keys of the witness table, of a format in it, and of a sub-property's
# format.
TABLE_KEYS = ("highestVersion", "properties")
FORMAT_KEYS = ("minimumVersion", "required", "subproperties")
SUBPROPERTY_KEYS = ("minimumVersion",)


class WitnessTableError(ValueError):
    """A witness table that load_table refuses; the message names the
    defect."""


@dataclass(frozen=True)
class WitnessFormat:
    """The witness a verdict can be delivered with: an entry of `entry_type`
    whose format_version is at least `lowest` and at most `highest`, as
    (major, minor). `required` is whether SV-COMP scores the verdict only
    with such a witness; if not, the witness is in demo mode and is
    validated without affecting the score."""

    entry_type: str
    lowest: tuple[int, int]
    highest: tuple[int, int]
    required: bool = True


@dataclass(frozen=True)
class WitnessTable:
    """A witness table as load_table returns it. `highest` is its
    highestVersion. `formats` maps (property, verdict) to the WitnessFormat of
    the verdict; a (property, verdict) without an entry has no witness format
    ("not supported"): nothing is delivered with it, and it scores without
    one. `subproperty_formats` maps (property, verdict, sub-property) to the
    WitnessFormat that replaces that of (property, verdict) for a false
    naming the sub-property."""

    highest: tuple[int, int]
    formats: Mapping[tuple[str, str], WitnessFormat]
    subproperty_formats: Mapping[tuple[str, str, str], WitnessFormat]


_VERSION = re.compile(r"(\d+)\.(\d+)")

# The C loader of PyYAML where it was built with libyaml, else the Python one
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Witness files of more bytes than this are checked in a process of their own
# (file_passes). CoOpeRace runs with a switch interval of 1000 s
# (execution.guard_clone), so Python code that runs long in one thread holds
# every other thread up: in the competition image, on one core, the check of
# a 14 MB witness took 4 to 5 s with libyaml and a sleeping thread woke 5 s
# late, and a 71 MB one took 33 s. Below this, the check takes about 0.25 s
# with libyaml and 1.6 s with the Python loader; the witnesses of the
# components are far smaller (at most 44 KB in the E7 sweep).
IN_PROCESS_BYTES = 1 << 20

# Seconds the check in a process of its own may take; a file whose check
# takes longer does not pass. The run's CPU-time budget (runCpuTimeBudget of
# conf/svcomp27.json, 870 s) is 30 s below the competition's 900 s, and a
# check still running when the budget is reached uses CPU time the run does
# not have, so this is shorter: with libyaml it covers about 40 MB.
CHECK_SECONDS = 20.0

# The address space in bytes the process of such a check may take; a file
# whose check needs more does not pass. The check takes about 30 times the
# file's size (440 MB for 14 MB), outside every component's memory limit and
# inside the run's, so this keeps a large witness from ending the whole run
# by its memory limit; it covers about 30 MB.
CHECK_MEMORY_BYTES = 1 << 30

# The program of a check in a process of its own (_passes_apart). argv: the
# limit of its address space in bytes, TOOL_DIR, the file, and the
# WitnessFormat and formulas to check as JSON. Prints 1 if the file passes,
# else 0.
_CHECK = """
import json, resource, sys
limit = int(sys.argv[1])
_, hard = resource.getrlimit(resource.RLIMIT_AS)
resource.setrlimit(resource.RLIMIT_AS, (limit if hard == resource.RLIM_INFINITY else min(limit, hard), hard))
sys.path.insert(0, sys.argv[2])
from src.cooperace import witnesses
data = json.loads(sys.argv[4])
format_ = witnesses.WitnessFormat(data["entry_type"], tuple(data["lowest"]), tuple(data["highest"]),
                                  data["required"])
text = witnesses.read_witness(sys.argv[3])
print(int(text is not None and witnesses.text_passes(text, format_, tuple(data["formulas"]))))
"""


def _table_version(value: object, where: str) -> tuple[int, int]:
    """The version string `value` ("2.2") of the witness table as (major,
    minor). Raises WitnessTableError, naming `where`, for anything but a
    string of two numbers separated by a dot; a JSON number is refused, since
    2.10 and 2.1 are the same number."""
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise WitnessTableError(f"{where} is {json.dumps(value)}, not a version \"major.minor\" as a string")
    return int(match.group(1)), int(match.group(2))


def _keys(value: object, allowed: tuple[str, ...], required: tuple[str, ...], where: str) -> dict:
    """`value` if it is a JSON object whose keys are among `allowed` and
    include `required`; raises WitnessTableError naming `where` otherwise."""
    if not isinstance(value, dict):
        raise WitnessTableError(f"{where} is not a JSON object")
    unknown = [key for key in value if key not in allowed]
    if unknown:
        raise WitnessTableError(f"{where} has the key {unknown[0]!r}, which is not one of {', '.join(allowed)}")
    missing = [key for key in required if key not in value]
    if missing:
        raise WitnessTableError(f"{where} has no {missing[0]!r}")
    return value


def _format(value: dict, where: str, entry_type: str, highest: tuple[int, int], required: bool) -> WitnessFormat:
    """The WitnessFormat of entries of `entry_type` from the minimumVersion of
    `value`, the object at `where` in the witness table, up to `highest`.
    Raises WitnessTableError if the minimumVersion is not a version or is
    above `highest`."""
    lowest = _table_version(value["minimumVersion"], f"{where}.minimumVersion")
    if lowest > highest:
        raise WitnessTableError(f"{where}.minimumVersion {value['minimumVersion']} is above highestVersion "
                                f"{highest[0]}.{highest[1]}")
    return WitnessFormat(entry_type, lowest, highest, required)


def load_table(data: object) -> WitnessTable:
    """The WitnessTable of `data`, a witness table as read from JSON. Raises
    WitnessTableError if `data` is not an object with exactly
    TABLE_KEYS; if highestVersion or a minimumVersion is not a version
    "major.minor"; if `properties` names a property that is not a key of
    properties.FORMULAS or lacks one; if a property's object does not have
    exactly the keys "true" and "false"; if a verdict's value is neither null
    nor an object with "minimumVersion" and "required" (a boolean) and
    optionally "subproperties"; if "subproperties" is given for another
    property than valid-memsafety's false, or names another sub-property
    than one of properties.SUBPROPERTY_FORMULAS, or gives one anything but
    a "minimumVersion"; or if a minimumVersion is above highestVersion."""
    table = _keys(data, TABLE_KEYS, TABLE_KEYS, "the witness table")
    highest = _table_version(table["highestVersion"], "highestVersion")
    properties = table["properties"]
    if not isinstance(properties, dict):
        raise WitnessTableError("properties is not a JSON object")
    for name in properties:
        if name not in FORMULAS:
            raise WitnessTableError(f"properties names {name!r}, which is not one of {', '.join(FORMULAS)}")
    formats: dict[tuple[str, str], WitnessFormat] = {}
    subproperty_formats: dict[tuple[str, str, str], WitnessFormat] = {}
    for name in FORMULAS:
        if name not in properties:
            raise WitnessTableError(f"properties has no {name!r}")
        verdicts = _keys(properties[name], tuple(ENTRY_TYPES), tuple(ENTRY_TYPES), f"properties.{name}")
        for verdict, entry_type in ENTRY_TYPES.items():
            where = f"properties.{name}.{verdict}"
            if verdicts[verdict] is None:
                continue
            format_ = _keys(verdicts[verdict], FORMAT_KEYS, ("minimumVersion", "required"), where)
            if not isinstance(format_["required"], bool):
                raise WitnessTableError(f"{where}.required is {json.dumps(format_['required'])}, not true or false")
            required = format_["required"]
            formats[(name, verdict)] = _format(format_, where, entry_type, highest, required)
            if "subproperties" not in format_:
                continue
            if (name, verdict) != (VALID_MEMSAFETY, "false"):
                raise WitnessTableError(f"{where} has subproperties, which only properties.{VALID_MEMSAFETY}.false "
                                        "can have")
            subproperties = format_["subproperties"]
            if not isinstance(subproperties, dict):
                raise WitnessTableError(f"{where}.subproperties is not a JSON object")
            for subproperty, override in subproperties.items():
                at = f"{where}.subproperties.{subproperty}"
                if subproperty not in SUBPROPERTY_FORMULAS:
                    raise WitnessTableError(f"{where}.subproperties names {subproperty!r}, which is not one of "
                                            f"{', '.join(SUBPROPERTY_FORMULAS)}")
                override = _keys(override, SUBPROPERTY_KEYS, SUBPROPERTY_KEYS, at)
                subproperty_formats[(name, verdict, subproperty)] = _format(override, at, entry_type, highest, required)
    return WitnessTable(highest, formats, subproperty_formats)


def read_table(path: str) -> WitnessTable:
    """load_table of the JSON in the file `path`. Raises WitnessTableError,
    with a message that begins "the witness table `path`: ", if the file
    cannot be read, is not JSON, or load_table refuses it."""
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return load_table(data)
    except (OSError, ValueError) as error:
        raise WitnessTableError(f"the witness table {path}: {error}") from error


@functools.cache
def table() -> WitnessTable:
    """read_table of TABLE_FILE in TOOL_DIR, read once per process. Raises
    WitnessTableError as read_table does; cli.main calls it before any
    component starts, so that a refused table ends CoOpeRace there."""
    return read_table(os.path.join(TOOL_DIR, TABLE_FILE))


def witness_format(property_name: str, verdict: str, subproperty: str | None = None) -> WitnessFormat | None:
    """The WitnessFormat of the verdict `verdict` ("true" or "false") on the
    property `property_name` (a key of properties.FORMULAS) in the witness
    table (table), with, for a false of valid-memsafety, the sub-property
    `subproperty` it names ("valid-free", "valid-deref" or
    "valid-memtrack"), whose own format the table may give; None if no
    witness counts for it."""
    formats = table()
    override = formats.subproperty_formats.get((property_name, verdict, subproperty))
    return override or formats.formats.get((property_name, verdict))


def needs_witness(property_name: str, verdict: str) -> bool:
    """Whether the witness table (table) says that SV-COMP scores the
    verdict `verdict` on the property `property_name` only with a witness
    that passes: its format for them has `required` true."""
    format_ = table().formats.get((property_name, verdict))
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
    `text` parses as YAML to a list of mappings; those whose entry_type is
    one of WITNESS_ENTRY_TYPES of the format's entry_type are at least one
    and have different entry types; they have one and the same
    metadata.format_version (version_of), as WitnessLint requires of the
    entries of a witness, and it is in the format's bounds; and the
    metadata.task.specification of each is a string that names the formulas
    of `specification` (properties.names_formulas); a ghost_instrumentation entry
    needs version GHOST_SINCE or higher. GraphML text does not parse to a
    list, so it never passes."""
    format_ = witness_format(property_name, verdict, subproperty)
    formulas = specification(property_name, verdict, subproperty)
    if format_ is None or formulas is None:
        return False
    return text_passes(text, format_, formulas)


def text_passes(text: str, format_: WitnessFormat, formulas: tuple[str, ...]) -> bool:
    """`passes` of `text` for the WitnessFormat `format_` and the formulas
    `formulas` of the verdict's specification."""
    try:
        entries = yaml.load(text, Loader=_LOADER)
    except (yaml.YAMLError, RecursionError):
        return False
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        return False
    kinds = WITNESS_ENTRY_TYPES[format_.entry_type]
    matching = [entry for entry in entries if entry.get("entry_type") in kinds]
    types = [entry["entry_type"] for entry in matching]
    if not matching or len(set(types)) != len(types):
        return False
    versions = set()
    for entry in matching:
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            return False
        versions.add(version_of(metadata.get("format_version")))
        task = metadata.get("task")
        spec = task.get("specification") if isinstance(task, dict) else None
        if not (isinstance(spec, str) and names_formulas(spec, formulas)):
            return False
    if len(versions) != 1:
        return False
    version = versions.pop()
    if version is not None and version < GHOST_SINCE and GHOST in types:
        return False
    return version is not None and format_.lowest <= version <= format_.highest


def read_witness(path: str) -> str | None:
    """The text of the file `path`, read as UTF-8; None if it cannot be
    read or decoded."""
    try:
        with open(path, encoding="utf-8") as file:
            return file.read()
    except (OSError, UnicodeDecodeError):
        return None


def file_passes(path: str, property_name: str, verdict: str, subproperty: str | None = None) -> bool:
    """`passes` for the file `path`, read as UTF-8; False for a file whose
    name ends in .graphml, and for one that cannot be read or decoded. A file
    of more than IN_PROCESS_BYTES is checked in a process of its own
    (_passes_apart), so that the thread that waits for it does not hold the
    GIL; it does not pass if that check takes longer than CHECK_SECONDS or
    more address space than CHECK_MEMORY_BYTES, or fails. This never raises,
    so a witness can cost the component its witness but not CoOpeRace its
    verdict line."""
    if path.lower().endswith(".graphml"):
        return False
    format_ = witness_format(property_name, verdict, subproperty)
    formulas = specification(property_name, verdict, subproperty)
    if format_ is None or formulas is None:
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size > IN_PROCESS_BYTES:
        return _passes_apart(path, size, format_, formulas)
    text = read_witness(path)
    return text is not None and text_passes(text, format_, formulas)


def _passes_apart(path: str, size: int, format_: WitnessFormat, formulas: tuple[str, ...]) -> bool:
    """text_passes for the file `path` of `size` bytes, checked by _CHECK in
    a process of its own (sys.executable, in TOOL_DIR) with the address
    space CHECK_MEMORY_BYTES, which subprocess.run kills after
    CHECK_SECONDS. False, with a line "CoOpeRace: ..." on stderr, if the
    process cannot be started, takes longer, or does not print 0 or 1 and
    end with status 0."""
    data = json.dumps({"entry_type": format_.entry_type, "lowest": format_.lowest, "highest": format_.highest,
                       "required": format_.required, "formulas": list(formulas)})
    command = [sys.executable, "-c", _CHECK, str(CHECK_MEMORY_BYTES), TOOL_DIR, os.path.abspath(path), data]
    try:
        done = subprocess.run(command, cwd=TOOL_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        problem = f"took longer than {CHECK_SECONDS:g} s"
    except (OSError, subprocess.SubprocessError) as error:
        problem = f"could not run ({type(error).__name__}: {error})"
    else:
        printed = done.stdout.strip()
        if done.returncode == 0 and printed in ("0", "1"):
            return printed == "1"
        last = (done.stderr.strip().splitlines() or [f"printed {printed[-60:]!r}"])[-1]
        problem = f"ended with exit code {done.returncode} ({last})"
    print(f"CoOpeRace: the check of the witness {path} ({size} bytes) {problem}: it does not pass",
          file=sys.stderr, flush=True)
    return False


def passing_witness(paths: Iterable[str], property_name: str, verdict: str,
                    subproperty: str | None = None) -> str | None:
    """The first of the witness files `paths` of one component run for which
    file_passes holds, for the verdict `verdict` on `property_name` with
    `subproperty`; None if none does."""
    for path in paths:
        if file_passes(path, property_name, verdict, subproperty):
            return path
    return None
