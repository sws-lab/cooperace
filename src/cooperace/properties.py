# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The properties of SV-COMP's C.Concurrency that a conf can give a strategy
for, the formulas of each and of valid-memsafety's sub-properties, and how a
property is recognized from the content of a property file (recognize, with
same_formulas, which witnesses.py also compares a witness's specification
with).

A property is named as BenchExec names the property file of sv-benchmarks
that holds it: the file name without ".prp" (benchexec.result.Property.name),
for example "no-data-race". recognize reads only the formulas in the file;
the file's name and the task's path play no part. Uses only the standard
library."""
from __future__ import annotations

UNREACH_CALL = "unreach-call"
NO_OVERFLOW = "no-overflow"
VALID_MEMSAFETY = "valid-memsafety"
NO_DATA_RACE = "no-data-race"

# The formulas of each property, as sv-benchmarks' c/properties/<name>.prp
# holds them, one CHECK per element. valid-memsafety is the conjunction of
# three, one per sub-property.
FORMULAS = {
    UNREACH_CALL: ("CHECK( init(main()), LTL(G ! call(reach_error())) )",),
    NO_OVERFLOW: ("CHECK( init(main()), LTL(G ! overflow) )",),
    VALID_MEMSAFETY: ("CHECK( init(main()), LTL(G valid-free) )",
                      "CHECK( init(main()), LTL(G valid-deref) )",
                      "CHECK( init(main()), LTL(G valid-memtrack) )"),
    NO_DATA_RACE: ("CHECK( init(main()), LTL(G ! data-race) )",),
}

# The sub-properties of valid-memsafety, by the name BenchExec gives each in
# a result "false(<name>)", with the formula of each from FORMULAS.
SUBPROPERTY_FORMULAS = {
    "valid-free": FORMULAS[VALID_MEMSAFETY][0],
    "valid-deref": FORMULAS[VALID_MEMSAFETY][1],
    "valid-memtrack": FORMULAS[VALID_MEMSAFETY][2],
}


def _formulas(text: str) -> tuple[str, ...] | None:
    """The CHECK formulas of `text` with all white space removed, sorted, or
    None if `text` has anything before its first "CHECK(". A file without a
    formula gives None."""
    compact = "".join(text.split())
    before, *formulas = compact.split("CHECK(")
    if before or not formulas:
        return None
    return tuple(sorted("CHECK(" + formula for formula in formulas))


def same_formulas(text: str, formulas: tuple[str, ...]) -> bool:
    """Whether `text` holds the CHECK formulas `formulas` and nothing else.
    White space, line breaks included, is ignored, and so is the order of the
    formulas; a formula that is missing, added or repeated makes them differ,
    and so does anything before the first "CHECK(" of `text`."""
    expected = _formulas("\n".join(formulas))
    return expected is not None and _formulas(text) == expected


def recognize(text: str) -> str | None:
    """The name (a key of FORMULAS) of the property whose formulas are those
    of `text`, the content of a property file, or None if they are those of
    no property in FORMULAS, compared with same_formulas."""
    for name, expected in FORMULAS.items():
        if same_formulas(text, expected):
            return name
    return None
