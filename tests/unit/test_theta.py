# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Theta svcomp27 (10.5281/zenodo.24128717) as a component: its registry
entry and options, its command line per property and data model with its
memory limit (ComponentSpec.gets_memory_limit), the status that BenchExec's
theta module gives its real output and which of those CoOpeRace accepts, and
which of its witness files count.

tests/unit/theta-output holds (as .out files) the output of Theta svcomp27, run alone with
BenchExec 4e89886c's theta module in the competition image user:2027
(5235ed22) at the pre-run's limits (3 GB, 1 core) on goblint, 16 November
2026, from the line after BenchExec's header. tests/unit/witnesses/theta holds
the witness.yml each of those runs wrote, unchanged. Each file is named
<task>.<property>.<verdict>. No component is run here: the version that
the theta module's cmdline asks the tool for is set on the tool-info
object."""
import subprocess
from pathlib import Path

import pytest

from src.cooperace import components, witnesses
from src.cooperace.components import (
    OPTIONS_FILE,
    REGISTRY,
    TASK_LIMIT,
    ComponentRunner,
    ComponentVersion,
    WitnessSpec,
    read_component_versions,
)
from src.cooperace.execution import FinishedRun

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "theta-output"
WITNESSES = Path(__file__).resolve().parent / "witnesses" / "theta"
PROPERTIES = ROOT / "tests" / "properties"
THETA_OPTIONS = ("--svcomp", "--portfolio", "STABLE", "--loglevel", "RESULT")


def theta_tool():
    """The tool-info object of Theta, with the version that theta-start.sh
    --version prints for 24128717, so that its cmdline runs nothing."""
    tool = REGISTRY["Theta"].tool()
    tool.version = lambda executable: "8.10.1"
    return tool


def theta_runner(tmp_path, property_name, data_model="ILP32"):
    """A runner for a task of `property_name` whose Theta is a directory
    tmp_path/theta holding theta-start.sh, with Theta's options."""
    directory = tmp_path / "theta"
    directory.mkdir(exist_ok=True)
    start = directory / "theta-start.sh"
    start.write_text("#!/bin/sh\nexit 0\n")
    start.chmod(0o755)
    runner = ComponentRunner(str(tmp_path / "task.i"), str(PROPERTIES / f"{property_name}.prp"), data_model,
                             versions={str(directory): ComponentVersion("d", "svcomp27", THETA_OPTIONS)},
                             property_name=property_name)
    runner.registry["Theta"] = components.ComponentSpec(
        "Theta", "benchexec.tools.theta", str(directory), REGISTRY["Theta"].witness, gets_memory_limit=True)
    return runner, str(start)


# --- registry and options -------------------------------------------------------

def test_theta_s_entry():
    spec = REGISTRY["Theta"]

    assert (spec.module, spec.directory, spec.task_limit) == ("benchexec.tools.theta", "theta", TASK_LIMIT)
    assert spec.witness == WitnessSpec("component", (), ("witness.yml",))
    assert spec.tool().name() == "Theta"


def test_only_theta_is_given_its_memory_limit():
    assert [name for name, spec in REGISTRY.items() if spec.gets_memory_limit] == ["Theta"]


def test_theta_s_options_are_those_of_version_svcomp27_of_its_fm_tools_entry():
    """theta.yml of fm-tools main 75f310bc (16 November 2026): version svcomp27,
    10.5281/zenodo.24128717, whose pre-run of 13 November covers the task set.
    The version svcomp27-validator shares the DOI and adds --witness."""
    versions = read_component_versions(str(ROOT / OPTIONS_FILE))

    assert versions["theta"] == ComponentVersion("10.5281/zenodo.24128717", "svcomp27", THETA_OPTIONS)


# --- the command line -----------------------------------------------------------

@pytest.mark.parametrize("data_model", ["ILP32", "LP64"])
@pytest.mark.parametrize("property_name", ["no-data-race", "unreach-call", "no-overflow", "valid-memsafety"])
def test_theta_s_command_line_per_property_and_data_model(tmp_path, property_name, data_model):
    """As bench-defs' theta.xml runs it: the task, the fm-tools options, the
    property file, the data model and the memory limit in bytes."""
    runner, start = theta_runner(tmp_path, property_name, data_model)

    command = runner.command(theta_tool(), runner.registry["Theta"], "/w", 2_700_000_000)

    assert command.cmdline == [start, str(tmp_path / "task.i"), *THETA_OPTIONS,
                               "--property", str(PROPERTIES / f"{property_name}.prp"),
                               "--architecture", data_model, "--memlimit", "2700000000"]
    assert command.cwd == str(tmp_path / "theta")


def test_without_a_memory_limit_of_its_own_theta_gets_the_run_s(tmp_path, monkeypatch):
    monkeypatch.setattr(components, "run_memory_limit", lambda: 3_000_000_000)
    runner, _start = theta_runner(tmp_path, "no-data-race")

    command = runner.command(theta_tool(), runner.registry["Theta"], "/w", None)

    assert command.cmdline[-2:] == ["--memlimit", "3000000000"]


def test_without_any_memory_limit_theta_gets_no_memlimit(tmp_path, monkeypatch):
    """As BenchExec's theta module gives a run without a memory limit."""
    monkeypatch.setattr(components, "run_memory_limit", lambda: None)
    runner, _start = theta_runner(tmp_path, "no-data-race")

    command = runner.command(theta_tool(), runner.registry["Theta"], "/w", None)

    assert "--memlimit" not in command.cmdline


def test_a_component_not_marked_gets_empty_limits(tmp_path):
    """The Theta module given the same entry without gets_memory_limit passes
    no --memlimit, whatever the component's memory limit."""
    runner, _start = theta_runner(tmp_path, "no-data-race")
    spec = runner.registry["Theta"]
    unmarked = components.ComponentSpec(spec.name, spec.module, spec.directory, spec.witness)

    command = runner.command(theta_tool(), unmarked, "/w", 2_700_000_000)

    assert "--memlimit" not in command.cmdline


def test_run_component_passes_the_step_s_memory_limit(tmp_path, monkeypatch):
    """run_component computes the step's limit before the command line: a
    share of 90% of a 3 GB run is passed as --memlimit."""
    from src.cooperace import processes
    from src.cooperace.config import Step

    monkeypatch.setattr(components, "run_memory_limit", lambda: 3_000_000_000)
    monkeypatch.setattr(processes, "run_memory_limit", lambda: 3_000_000_000)
    runner, _start = theta_runner(tmp_path, "no-data-race")
    seen = {}

    def command(actor, spec, witness_dir, memory=None):
        seen["memory"] = memory
        raise RuntimeError("stop here")

    monkeypatch.setattr(runner, "command", command)
    runner.work_dir = str(tmp_path)
    with pytest.raises(RuntimeError, match="stop here"):
        runner.run_component(theta_tool(), Step("Theta", "all", memory_limit="90%"), None)

    assert seen["memory"] == 2_700_000_000


# --- result ------------------------------------------------------------------------

def status_of(log, returncode=0, termination_reason=None, property_name="no-data-race"):
    """The status component_status gives Theta's output `log` (a file of
    OUTPUT, or text), and the verdict CoOpeRace takes from it on
    `property_name` with acceptance "all"."""
    text = (OUTPUT / log).read_text() if log.endswith(".out") else log
    run = FinishedRun(["theta-start.sh"], returncode, text, termination_reason=termination_reason)
    status = components.component_status(theta_tool(), ["theta-start.sh"], run, termination_reason)
    verdict = next((v for v in ("true", "false") if components.confirm_verdict("all", status, v, property_name)),
                   "unknown")
    return status, verdict


@pytest.mark.parametrize(("log", "property_name", "status", "verdict"), [
    ("atomic-qual-both-atomic.no-data-race.true.out", "no-data-race", "true", "true"),
    ("bigshot_p.no-data-race.false.out", "no-data-race", "false(no-data-race)", "false"),
    ("chl-sre-subst.unreach-call.true.out", "unreach-call", "true", "true"),
    ("unwind1-nondet.unreach-call.false.out", "unreach-call", "false(unreach-call)", "false"),
    ("barrier-posix-count.no-overflow.false.out", "no-overflow", "false(no-overflow)", "false"),
])
def test_theta_s_real_output_gives_its_verdict(log, property_name, status, verdict):
    assert status_of(log, property_name=property_name) == (status, verdict)


@pytest.mark.parametrize("property_name", ["unreach-call", "no-overflow", "valid-memsafety"])
def test_a_violation_of_another_property_is_no_verdict(property_name):
    assert status_of("bigshot_p.no-data-race.false.out", property_name=property_name)[1] == "unknown"


@pytest.mark.parametrize(("reason", "status"), [("cputime", "TIMEOUT"), ("memory", "OUT OF MEMORY"),
                                                ("walltime", "TIMEOUT"), ("killed", "KILLED")])
def test_a_theta_ended_by_a_limit_gives_no_verdict_even_after_printing_one(reason, status):
    """BenchExec's theta module gives ERROR for every run that was ended
    (run.was_terminated), so a SafetyResult printed before the kill, of a
    portfolio that was still writing its witness, does not count."""
    assert status_of("chl-sre-subst.unreach-call.true.out", -9, reason, "unreach-call") == (status, "unknown")


@pytest.mark.parametrize(("code", "status"), [
    (200, "ERROR (out of memory, after parsing finished)"),
    (201, "ERROR (inner timeout, after parsing finished)"),
    (202, "ERROR (server error, after parsing finished)"),
    (210, "ERROR (frontend failed, after parsing finished)"),
    (221, "ERROR (solver error, after parsing finished)"),
    (1, "ERROR (generic error, after parsing finished)"),
])
def test_theta_s_error_exits_are_no_verdict(code, status):
    log = "ParsingResult Success\nsubprocess: (SafetyResult Unknown)\n"

    assert status_of(log, code) == (status, "unknown")


def test_an_unknown_after_a_safe_of_an_earlier_line_is_unknown():
    """determine_result takes the last SafetyResult line."""
    log = "subprocess: (SafetyResult Safe)\nsubprocess: (SafetyResult Unknown)\n"

    assert status_of(log) == ("unknown", "unknown")


# --- witnesses ---------------------------------------------------------------------

THETA_WITNESSES = sorted(WITNESSES.glob("*.yml"))


def test_the_theta_witnesses_are_there():
    assert [path.name for path in THETA_WITNESSES] == [
        "barrier-posix-count.no-overflow.false.yml", "bigshot_p.no-data-race.false.yml",
        "chl-sre-subst.unreach-call.true.yml", "unwind1-nondet.unreach-call.false.yml"]


@pytest.mark.parametrize("path", THETA_WITNESSES, ids=lambda path: path.name)
def test_theta_s_witness_counts_for_its_property_and_verdict_only(path):
    _task, property_name, verdict, _yml = path.name.split(".")
    subproperty = property_name if verdict == "false" else None

    assert witnesses.file_passes(str(path), property_name, verdict)
    assert witnesses.passes(path.read_text(), property_name, verdict, subproperty)
    other = "true" if verdict == "false" else "false"
    assert not witnesses.passes(path.read_text(), property_name, other)
    for prop in ("no-data-race", "unreach-call", "no-overflow"):
        if prop != property_name:
            assert not witnesses.passes(path.read_text(), prop, verdict)


def test_theta_s_witness_is_taken_from_its_directory_after_a_run(tmp_path):
    """witness.yml, which Theta writes into its working directory, is a
    candidate; witness.graphml, which it writes beside it, is not."""
    (tmp_path / "witness.yml").write_text("x")
    (tmp_path / "witness.graphml").write_text("x")

    spec = REGISTRY["Theta"].witness
    assert components.result_files_patterns(spec) == ["witness.yml"]
    assert components.witness_files_in(spec, str(tmp_path)) == [str(tmp_path / "witness.yml")]


def test_finished_run_is_a_completed_process():
    assert issubclass(FinishedRun, subprocess.CompletedProcess)
