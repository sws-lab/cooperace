# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""SAF svcomp27 as a component: its registry entry and options, its
tool-info module (toolinfo/saf.py, BenchExec's), its command line per
property and data model, the status that module gives SAF's real output and
which of those CoOpeRace accepts, and which of its witness files count.

tests/unit/saf-output holds (as .out files) what SAF svcomp27
(10.5281/zenodo.24267653) printed inside CoOpeRace, between "---SAF logs---"
and "---end of SAF logs---", in the competition image user:2027 (5235ed22) at
the pre-run's limits (3 GB, 1 core) on goblint, 18 November 2026
(scripts/component-smoke.py). tests/unit/witnesses/saf holds the witness.yml
of three of those runs, and two that SAF wrote on goblint outside a container
(no-data-race: in the container its TSan replay confirms no race), unchanged.
Each file is named <task>.<property>.<verdict>. No component is run here."""
from pathlib import Path

import pytest

from src.cooperace import components, witnesses
from src.cooperace.components import (
    OPTIONS_FILE,
    PROGRAM_TASK_LIMIT,
    REGISTRY,
    ComponentRunner,
    ComponentVersion,
    WitnessSpec,
    read_component_versions,
)
from src.cooperace.execution import FinishedRun

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "saf-output"
WITNESSES = Path(__file__).resolve().parent / "witnesses" / "saf"
PROPERTIES = ROOT / "tests" / "properties"
PROPERTY_NAMES = ["no-data-race", "unreach-call", "no-overflow", "valid-memsafety"]


def saf_tool():
    """The tool-info object of SAF."""
    return REGISTRY["SAF"].tool()


def saf_runner(tmp_path, property_name, data_model="ILP32", options=()):
    """A runner for a task of `property_name` whose SAF is a directory
    tmp_path/saf holding bin/saf, with `options` as its fm-tools options."""
    directory = tmp_path / "saf"
    (directory / "bin").mkdir(parents=True, exist_ok=True)
    (directory / "share").mkdir(exist_ok=True)
    executable = directory / "bin" / "saf"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    runner = ComponentRunner(str(tmp_path / "task.i"), str(PROPERTIES / f"{property_name}.prp"), data_model,
                             versions={str(directory): ComponentVersion("d", "svcomp27", tuple(options))},
                             property_name=property_name)
    spec = REGISTRY["SAF"]
    runner.registry["SAF"] = components.ComponentSpec(spec.name, spec.module, str(directory), spec.witness,
                                                      spec.task_limit)
    return runner, str(executable)


# --- registry and options -------------------------------------------------------

def test_saf_s_entry():
    spec = REGISTRY["SAF"]

    assert (spec.module, spec.directory) == ("src.cooperace.toolinfo.saf", "saf")
    assert spec.witness == WitnessSpec("component", (), ("witness.yml",))
    assert spec.task_limit == PROGRAM_TASK_LIMIT
    assert not spec.gets_memory_limit
    assert spec.tool().name() == "SAF"


def test_saf_s_options_are_those_of_the_doi_of_tools_pool_txt():
    """saf.yml of fm-tools main (since 40b030b2, 16 November 2026): version
    svcomp27 has no benchexec_toolinfo_options, and bench-defs' saf.xml gives
    none. The DOI is the one tools-pool.txt names, so that a new archive of
    SAF is a change of the lock files only."""
    pool = dict(line.split(": ", 1) for line in (ROOT / "tools-pool.txt").read_text().splitlines()
                if line and not line.startswith("#"))
    versions = read_component_versions(str(ROOT / OPTIONS_FILE))

    assert versions["saf"] == ComponentVersion(pool["saf"], "svcomp27", ())


def test_saf_s_tool_info_module_is_benchexec_s_saf_module_with_a_header():
    """toolinfo/saf.py is benchexec/tools/saf.py of BenchExec 35b96ae5 (the
    bench-defs' pin) with CoOpeRace's comment after the license header."""
    text = (ROOT / "src" / "cooperace" / "toolinfo" / "saf.py").read_text()

    assert "SPDX-" + "License-Identifier: Apache-2.0" in text
    assert "Copied unchanged (below this comment) by CoOpeRace" in text
    assert 'cmd = [executable, "verify", "--property", task.property_file]' in text


# --- the command line -----------------------------------------------------------

@pytest.mark.parametrize("data_model", ["ILP32", "LP64"])
@pytest.mark.parametrize("property_name", PROPERTY_NAMES)
def test_saf_s_command_line_per_property_and_data_model(tmp_path, property_name, data_model):
    """As BenchExec runs SAF in the competition: verify, the property file,
    the data model, the fm-tools options (none) and the task, in SAF's
    directory; no memory limit and no --timeout or --witness."""
    runner, executable = saf_runner(tmp_path, property_name, data_model)

    command = runner.command(saf_tool(), runner.registry["SAF"], "/w", 2_700_000_000)

    assert command.cmdline == [executable, "verify", "--property", str(PROPERTIES / f"{property_name}.prp"),
                               "--data-model", data_model, str(tmp_path / "task.i")]
    assert command.cwd == str(tmp_path / "saf")


def test_an_option_data_model_is_not_passed_twice(tmp_path):
    runner, executable = saf_runner(tmp_path, "no-data-race", "LP64", options=("--data-model", "ILP32"))

    command = runner.command(saf_tool(), runner.registry["SAF"], "/w")

    assert command.cmdline == [executable, "verify", "--property", str(PROPERTIES / "no-data-race.prp"),
                               "--data-model", "ILP32", str(tmp_path / "task.i")]


# --- result ------------------------------------------------------------------------

def status_of(log, returncode=0, termination_reason=None, property_name="no-data-race"):
    """The status component_status gives SAF's output `log` (a file of
    OUTPUT, or text), and the verdict CoOpeRace takes from it on
    `property_name` with acceptance "all"."""
    text = (OUTPUT / log).read_text() if log.endswith(".out") else log
    run = FinishedRun(["saf"], returncode, text, termination_reason=termination_reason)
    status = components.component_status(saf_tool(), ["saf"], run, termination_reason)
    verdict = next((v for v in ("true", "false") if components.confirm_verdict("all", status, v, property_name)),
                   "unknown")
    return status, verdict


@pytest.mark.parametrize(("log", "property_name", "status", "verdict"), [
    ("seq-truncation-ilp32.no-data-race.true.out", "no-data-race", "true", "true"),
    ("arg-reused-struct-race.no-data-race.unknown.out", "no-data-race", "unknown", "unknown"),
    ("lazy01.unreach-call.false.out", "unreach-call", "false(unreach-call)", "false"),
    ("safe006_power.no-overflow.true.out", "no-overflow", "true", "true"),
    ("cta-double-checked-scale.no-overflow.false.out", "no-overflow", "false(no-overflow)", "false"),
])
def test_saf_s_real_output_gives_its_verdict(log, property_name, status, verdict):
    assert status_of(log, property_name=property_name) == (status, verdict)


@pytest.mark.parametrize("property_name", ["no-data-race", "no-overflow", "valid-memsafety"])
def test_a_violation_of_another_property_is_no_verdict(property_name):
    assert status_of("lazy01.unreach-call.false.out", property_name=property_name)[1] == "unknown"


@pytest.mark.parametrize(("line", "verdict"), [("false(valid-deref)", "false"), ("false(valid-free)", "false"),
                                               ("false(valid-memtrack)", "false"), ("true", "true")])
def test_saf_s_memsafety_verdicts(line, verdict):
    assert status_of(f"saf verify: x\n{line}\n", property_name="valid-memsafety") == (line, verdict)


@pytest.mark.parametrize(("reason", "status"), [("cputime", "TIMEOUT"), ("memory", "OUT OF MEMORY"),
                                                ("walltime", "TIMEOUT"), ("killed", "KILLED")])
def test_a_saf_ended_by_a_limit_gives_no_verdict_even_after_printing_one(reason, status):
    """The saf module gives ERROR for every exit code that is not 0, and a
    kill has none, so a verdict line printed before the kill does not count."""
    assert status_of("lazy01.unreach-call.false.out", -9, reason, "unreach-call") == (status, "unknown")


@pytest.mark.parametrize("code", [1, 2, 101])
def test_a_saf_that_fails_gives_no_verdict(code):
    """SAF exits 0 with every verdict; any other exit code is its failure,
    even after a verdict line (Rust's panic exits 101)."""
    assert status_of("false(unreach-call)\n", code, property_name="unreach-call") == (f"ERROR ({code})", "unknown")


def test_output_without_a_verdict_line_is_an_error():
    assert status_of("saf verify: something\n") == ("ERROR", "unknown")


# --- witnesses ---------------------------------------------------------------------

SAF_WITNESSES = sorted(WITNESSES.glob("*.yml"))


def test_the_saf_witnesses_are_there():
    assert [path.name for path in sorted(WITNESSES.iterdir())] == [
        "cta-double-checked-scale.no-overflow.false.yml", "fib_unsafe-10-racy.no-data-race.false.yml",
        "lazy01.unreach-call.false.yml", "safe006_power.no-overflow.true.yml",
        "singleton-b.no-data-race.false.graphml-as-yml"]


@pytest.mark.parametrize("name", ["fib_unsafe-10-racy.no-data-race.false.yml", "lazy01.unreach-call.false.yml",
                                  "safe006_power.no-overflow.true.yml"])
def test_saf_s_witness_counts_for_its_property_and_verdict_only(name):
    path = WITNESSES / name
    _task, property_name, verdict, _yml = path.name.split(".")
    subproperty = property_name if verdict == "false" else None

    assert witnesses.file_passes(str(path), property_name, verdict)
    assert witnesses.passes(path.read_text(), property_name, verdict, subproperty)
    other = "true" if verdict == "false" else "false"
    assert not witnesses.passes(path.read_text(), property_name, other)
    for prop in ("no-data-race", "unreach-call", "no-overflow"):
        if prop != property_name:
            assert not witnesses.passes(path.read_text(), prop, verdict)


def test_saf_s_no_overflow_violation_witness_of_format_2_0_does_not_pass_the_internal_check():
    """SAF writes its no-overflow violation_sequence in format 2.0; the
    witness table (conf/witnesses.json) requires 2.2 for a false of
    no-overflow, so the internal check refuses it (observed on
    pthread-overflow/cta-double-checked-scale, 18 November 2026). Under
    "witnessLint": true WitnessLint 2.4 decides instead; it gives "done" for
    such a witness, so there SAF's false is delivered, as the pre-run scored
    it (observed on pthread-overflow/order-count-before-value)."""
    path = WITNESSES / "cta-double-checked-scale.no-overflow.false.yml"

    assert "format_version: '2.0'" in path.read_text()
    assert not witnesses.file_passes(str(path), "no-overflow", "false")


def test_a_graphml_witness_that_saf_writes_as_witness_yml_does_not_pass():
    """On some no-data-race tasks SAF writes a GraphML witness into
    witness.yml (pthread/singleton-b; the pre-run's "witness-version
    mismatch"); no GraphML witness counts. WitnessLint 2.4 gives it "ERROR
    (unexpected witness version)", so the gate refuses it too."""
    path = WITNESSES / "singleton-b.no-data-race.false.graphml-as-yml"

    assert path.read_text().startswith("<?xml")
    assert not witnesses.file_passes(str(path), "no-data-race", "false")


def test_saf_s_witness_is_taken_from_its_directory_after_a_run(tmp_path):
    (tmp_path / "witness.yml").write_text("x")
    (tmp_path / "witness.graphml").write_text("x")

    spec = REGISTRY["SAF"].witness
    assert components.result_files_patterns(spec) == ["witness.yml"]
    assert components.witness_files_in(spec, str(tmp_path)) == [str(tmp_path / "witness.yml")]
