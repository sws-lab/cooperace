# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Which witness file counts for a verdict (witnesses.py), and that
ComponentRunner.run_component returns, and so delivers, only a file that
counts.

tests/unit/witnesses holds copies, unchanged, of four witnesses that
sv-sanitizers 0.3.0 and Dartagnan svcomp27 wrote on no-data-race tasks of
sv-benchmarks (format 2.2, violation_sequence; the input_files are paths on
the machine they were run on). The 2027 validators confirmed each of them.
The failing witnesses below are made from them by changing one field."""
import subprocess
import sys
import types
from pathlib import Path

import pytest
from support import make_script, register_stub

import src.cooperace
from src.cooperace import witnesses
from src.cooperace.config import Step

FIXTURES = Path(__file__).resolve().parent / "witnesses"
PASSING = sorted(FIXTURES.glob("*/*.witness.yml"))
LAMPORT = (FIXTURES / "sv-sanitizers" / "lamport-b.witness.yml").read_text()


def changed(text, old, new):
    """`text` with its one occurrence of `old` replaced by `new`."""
    assert text.count(old) == 1
    return text.replace(old, new)


def violation(specification, version='"2.2"'):
    """A violation witness with the specification `specification` and the
    format_version `version`, as YAML text."""
    return changed(changed(LAMPORT, "CHECK( init(main()), LTL(G ! data-race) )", specification),
                   'format_version: "2.2"', f"format_version: {version}")


def correctness(version='"2.1"', specification="CHECK( init(main()), LTL(G ! call(reach_error())) )"):
    """A correctness witness (invariant_set) for unreach-call."""
    return f"""- entry_type: invariant_set
  metadata:
    format_version: {version}
    uuid: 00000000-0000-0000-0000-000000000000
    creation_time: 2026-10-19T00:00:00Z
    producer: {{name: Test, version: "1"}}
    task:
      input_files: [task.i]
      input_file_hashes: {{task.i: "0"}}
      specification: "{specification}"
      data_model: ILP32
      language: C
  content: []
"""


DEREF = "CHECK( init(main()), LTL(G valid-deref) )"
MEMTRACK = "CHECK( init(main()), LTL(G valid-memtrack) )"


# --- the check ---------------------------------------------------------------

def test_the_fixtures_are_there():
    assert [path.relative_to(FIXTURES).as_posix() for path in PASSING] == [
        "dartagnan/fib_safe-5-racy.witness.yml", "dartagnan/lamport-b.witness.yml",
        "sv-sanitizers/09-regions_18-nested_rc.witness.yml", "sv-sanitizers/lamport-b.witness.yml"]


@pytest.mark.parametrize("path", PASSING, ids=lambda path: path.relative_to(FIXTURES).as_posix())
def test_the_svcomp27_witnesses_pass_for_a_false_of_no_data_race(path):
    assert witnesses.file_passes(str(path), "no-data-race", "false")
    assert witnesses.passes(path.read_text(), "no-data-race", "false", "no-data-race")


@pytest.mark.parametrize("path", PASSING, ids=lambda path: path.relative_to(FIXTURES).as_posix())
def test_the_svcomp27_witnesses_do_not_pass_for_another_verdict_or_property(path):
    text = path.read_text()
    assert not witnesses.passes(text, "no-data-race", "true")
    assert not witnesses.passes(text, "unreach-call", "false")
    assert not witnesses.passes(text, "valid-memsafety", "false", "valid-deref")


GRAPHML = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
 <key attr.name="specification" attr.type="string" for="graph" id="specification"/>
 <graph edgedefault="directed">
  <data key="witness-type">violation_witness</data>
  <data key="specification">CHECK( init(main()), LTL(G ! data-race) )</data>
  <node id="N0"><data key="entry">true</data></node>
 </graph>
</graphml>
"""


@pytest.mark.parametrize("text", [
    GRAPHML,
    violation("CHECK( init(main()), LTL(G ! data-race) )", '"2.1"'),
    changed(LAMPORT, "entry_type: violation_sequence", "entry_type: invariant_set"),
    violation("CHECK( init(main()), LTL(G ! overflow) )"),
    "- entry_type: violation_sequence\n  metadata: {format_version: \"2.2\"\n",
    "not a witness at all",
    "",
], ids=["graphml", "format-2.1", "invariant-set-for-false", "other-property", "broken-yaml", "a-string",
        "empty"])
def test_a_witness_of_another_format_kind_or_property_does_not_pass(text):
    assert not witnesses.passes(text, "no-data-race", "false")


@pytest.mark.parametrize("text", [
    violation("CHECK( init(main()), LTL(G ! data-race) )", '"2.4"'),
    violation("CHECK( init(main()), LTL(G ! data-race) )", '"3.0"'),
    violation("CHECK( init(main()), LTL(G ! data-race) )", '"two"'),
    violation("CHECK( init(main()), LTL(G ! data-race) ) CHECK( init(main()), LTL(G ! overflow) )"),
    changed(LAMPORT, "specification: CHECK( init(main()), LTL(G ! data-race) )", "specification: [x]"),
    changed(LAMPORT, "    task:\n", "    other:\n"),
    LAMPORT + LAMPORT,
    "\n".join(line[2:] for line in LAMPORT.splitlines()),
    "- 1\n" + LAMPORT,
], ids=["format-2.4", "format-3.0", "no-version", "two-properties", "specification-not-a-string",
        "no-task", "two-violation-entries", "not-a-list", "an-entry-not-a-mapping"])
def test_a_witness_out_of_bounds_or_malformed_does_not_pass(text):
    assert not witnesses.passes(text, "no-data-race", "false")


def test_the_versions_in_bounds_pass_and_an_unquoted_version_is_read_as_a_number():
    for version in ('"2.3"', "2.2", "2.3"):
        assert witnesses.passes(violation("CHECK( init(main()), LTL(G ! data-race) )", version),
                                "no-data-race", "false")
    assert witnesses.passes(violation("CHECK(init(main()),LTL(G!data-race))"), "no-data-race", "false")


def test_a_correctness_witness_of_2_1_to_2_3_passes_for_a_true_of_unreach_call():
    for version in ('"2.1"', '"2.2"', '"2.3"'):
        assert witnesses.passes(correctness(version), "unreach-call", "true")
    for version in ('"2.0"', '"2.4"'):
        assert not witnesses.passes(correctness(version), "unreach-call", "true")
    assert not witnesses.passes(correctness(), "unreach-call", "false")
    assert not witnesses.passes(correctness(), "no-overflow", "true")
    assert witnesses.passes(correctness(specification="CHECK( init(main()), LTL(G ! overflow) )"),
                            "no-overflow", "true")


def test_a_valid_memsafety_witness_names_the_sub_property_of_the_false():
    deref = violation(DEREF)
    assert witnesses.passes(deref, "valid-memsafety", "false", "valid-deref")
    assert not witnesses.passes(deref, "valid-memsafety", "false", "valid-free")
    assert not witnesses.passes(deref, "valid-memsafety", "false", None)
    assert not witnesses.passes(deref, "valid-memsafety", "true", "valid-deref")
    # The formulas of the whole property name no single sub-property.
    whole = " ".join(witnesses.FORMULAS["valid-memsafety"])
    assert not witnesses.passes(violation(whole), "valid-memsafety", "false", "valid-deref")


def test_a_valid_memtrack_witness_needs_format_2_3():
    assert not witnesses.passes(violation(MEMTRACK), "valid-memsafety", "false", "valid-memtrack")
    assert witnesses.passes(violation(MEMTRACK, '"2.3"'), "valid-memsafety", "false", "valid-memtrack")


def test_which_verdicts_need_a_witness():
    needed = {(prop, verdict) for prop in witnesses.FORMULAS for verdict in ("true", "false")
              if witnesses.needs_witness(prop, verdict)}
    assert needed == {("no-data-race", "false"), ("unreach-call", "true"), ("unreach-call", "false"),
                      ("no-overflow", "true"), ("no-overflow", "false")}
    assert witnesses.witness_format("no-data-race", "true") is None
    assert witnesses.witness_format("valid-memsafety", "true") is None
    assert not witnesses.witness_format("valid-memsafety", "false", "valid-free").required
    assert witnesses.witness_format("valid-memsafety", "false", "valid-memtrack").lowest == (2, 3)
    assert witnesses.witness_format("no-data-race", "unknown") is None


def test_subproperty_of():
    assert witnesses.subproperty_of("false(valid-deref)") == "valid-deref"
    assert witnesses.subproperty_of("false(no-data-race)") == "no-data-race"
    assert witnesses.subproperty_of("false") is None
    assert witnesses.subproperty_of("true") is None


def test_file_passes_refuses_a_graphml_name_and_unreadable_files(tmp_path):
    named_graphml = tmp_path / "witness.graphml"
    named_graphml.write_text(LAMPORT)
    not_utf8 = tmp_path / "latin1-witness.yml"
    not_utf8.write_bytes(b"\xff\xfe" + LAMPORT.encode())

    assert not witnesses.file_passes(str(named_graphml), "no-data-race", "false")
    assert not witnesses.file_passes(str(not_utf8), "no-data-race", "false")
    assert not witnesses.file_passes(str(tmp_path / "missing.yml"), "no-data-race", "false")


def test_passing_witness_is_the_first_file_that_passes(tmp_path):
    files = {"a-witness.graphml": GRAPHML, "b-witness.yml": violation("CHECK( init(main()), LTL(G ! data-race) )",
                                                                       '"2.1"'),
             "c-witness.yml": LAMPORT, "d-witness.yml": LAMPORT}
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    paths = [str(tmp_path / name) for name in files]

    assert witnesses.passing_witness(paths, "no-data-race", "false") == paths[2]
    assert witnesses.passing_witness(paths[:2], "no-data-race", "false") is None
    assert witnesses.passing_witness(paths, "no-data-race", "true") is None
    assert witnesses.passing_witness([], "no-data-race", "false") is None


# --- run_component returns only a file that passes ----------------------------

class FakeExecution(types.SimpleNamespace):
    """Stands in for execution.py, which needs Linux: execute_run writes
    `files` (name to text) into the run's directory, as a component run
    under RunExecutor leaves its witnesses there, and returns a finished run
    whose output is `output`."""

    def __init__(self, files, output):
        super().__init__(files=files, output=output)

    def container_dir_modes(self, *args):
        return {}

    def execute_run(self, cmdline, cwd, environments, group, *, output_dir, **kwargs):
        for name, text in self.files.items():
            Path(output_dir, name).write_text(text)
        run = subprocess.CompletedProcess(cmdline, 0, self.output, "")
        run.termination_reason = run.cpu_time = run.wall_time = run.memory = None
        return run


@pytest.fixture
def fake_execution(monkeypatch):
    """Returns a function that makes run_component run with FakeExecution."""

    def install(files, output):
        fake = FakeExecution(files, output)
        monkeypatch.setitem(sys.modules, "src.cooperace.execution", fake)
        monkeypatch.setattr(src.cooperace, "execution", fake, raising=False)

    return install


@pytest.mark.parametrize("files, status, delivered", [
    ({"a-witness.yml": GRAPHML, "b-witness.yml": LAMPORT, "c-witness.yml": LAMPORT}, "false", "b-witness.yml"),
    ({"witness.graphml": GRAPHML,
      "a-witness.yml": violation("CHECK( init(main()), LTL(G ! data-race) )", '"2.1"'),
      "b-witness.yml": changed(LAMPORT, "entry_type: violation_sequence", "entry_type: invariant_set"),
      "c-witness.yml": violation("CHECK( init(main()), LTL(G ! overflow) )"),
      "d-witness.yml": "not: [yaml"}, "false", None),
    ({"a-witness.yml": LAMPORT}, "true", None),
], ids=["one-passes", "none-passes", "true-of-no-data-race"])
def test_only_a_witness_that_passes_is_returned_and_delivered(
        make_runner, group, fake_execution, tmp_path, monkeypatch, files, status, delivered):
    runner = make_runner()
    register_stub(runner, "Stub A", make_script(tmp_path / "stub", ""))
    fake_execution(files, f"STUB-STATUS: {status}\n")
    runner.prepare()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    try:
        outcome = runner.run_step(Step("Stub A", "all"), group)
        runner.deliver(outcome.witness_files)
        assert outcome.verdict == status
        assert [Path(file).name for file in outcome.witness_files] == ([] if delivered is None else [delivered])
        assert sorted(path.name for path in cwd.iterdir()) == ([] if delivered is None else ["witness.yml"])
        if delivered is not None:
            assert (cwd / "witness.yml").read_text() == files[delivered]
    finally:
        runner.cleanup()
