"""Recognizing the property of a property file: properties.recognize."""
from pathlib import Path

import pytest

from src.cooperace import properties

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "properties"

# The four property files of C.Concurrency as sv-benchmarks has them at
# c9ab60909 (the last commit to change them, read at 7efe28dd2), line
# endings included. The copies in tests/properties are an earlier version
# of the same files, with CRLF line endings.
SV_BENCHMARKS = {
    "unreach-call": "CHECK( init(main()), LTL(G ! call(reach_error())) )\n\n",
    "no-overflow": "CHECK( init(main()), LTL(G ! overflow) )\n\n",
    "valid-memsafety": ("CHECK( init(main()), LTL(G valid-free) )\n"
                        "CHECK( init(main()), LTL(G valid-deref) )\n"
                        "CHECK( init(main()), LTL(G valid-memtrack) )\n\n"),
    "no-data-race": "CHECK( init(main()), LTL(G ! data-race) )\n\n",
}


def test_the_properties_are_named_as_benchexec_names_their_files():
    assert list(properties.FORMULAS) == ["unreach-call", "no-overflow", "valid-memsafety", "no-data-race"]


@pytest.mark.parametrize("name", list(SV_BENCHMARKS))
def test_the_property_files_of_sv_benchmarks_are_recognized(name):
    assert properties.recognize(SV_BENCHMARKS[name]) == name


@pytest.mark.parametrize("name", list(SV_BENCHMARKS))
def test_the_copies_in_tests_properties_are_recognized(name):
    assert properties.recognize((FIXTURES / f"{name}.prp").read_text()) == name


@pytest.mark.parametrize("name", ["coverage-branches", "coverage-conditions", "coverage-error-call",
                                  "coverage-statements", "def-behavior", "termination", "valid-memcleanup"])
def test_the_other_property_files_of_sv_benchmarks_are_not_recognized(name):
    assert properties.recognize((FIXTURES / f"{name}.prp").read_text()) is None


@pytest.mark.parametrize("text, name", [
    ("CHECK(init(main()),LTL(G!data-race))", "no-data-race"),
    ("CHECK(init(main()),\n  LTL(G !   data-race))\n\n", "no-data-race"),
    ("\tCHECK( init(main()), LTL(G ! overflow) )\r\n", "no-overflow"),
    ("CHECK( init(main()), LTL(G valid-memtrack) ) CHECK( init(main()), LTL(G valid-free) )\n"
     "CHECK( init(main()), LTL(G valid-deref) )", "valid-memsafety"),
], ids=["no-space", "line-breaks", "tab-crlf", "memsafety-other-order"])
def test_white_space_and_the_order_of_the_formulas_are_ignored(text, name):
    assert properties.recognize(text) == name


@pytest.mark.parametrize("text", [
    "",
    "\n\n",
    "CHECK( init(main()), LTL(G ! data-races) )",
    "CHECK( init(main()), LTL(G data-race) )",
    "LTL(G ! data-race)",
    "CHECK( init(main()), LTL(G ! data-race) ) extra",
    "# comment\nCHECK( init(main()), LTL(G ! data-race) )",
    "CHECK( init(main()), LTL(G ! data-race) )\nCHECK( init(main()), LTL(G ! overflow) )",
    "CHECK( init(main()), LTL(G ! data-race) )\nCHECK( init(main()), LTL(G ! data-race) )",
    "CHECK( init(main()), LTL(G valid-free) )\nCHECK( init(main()), LTL(G valid-deref) )",
    "CHECK( init(main()), LTL(G valid-deref) )",
    SV_BENCHMARKS["valid-memsafety"] + "CHECK( init(main()), LTL(G valid-deref) )",
    "CHECK( init(main()), LTL(G valid-free) )\nCHECK( init(main()), LTL(G valid-deref) )\n"
    "CHECK( init(main()), LTL(G valid-memcleanup) )",
    "CHECK( init(f()), LTL(G ! call(reach_error())) )",
], ids=["empty", "blank", "different-atom", "no-negation", "no-check", "trailing-text", "leading-text",
        "two-properties", "repeated", "memsafety-two-of-three", "memsafety-one", "memsafety-repeated",
        "memsafety-with-memcleanup", "other-entry"])
def test_a_text_that_is_not_exactly_one_property_is_not_recognized(text):
    assert properties.recognize(text) is None
