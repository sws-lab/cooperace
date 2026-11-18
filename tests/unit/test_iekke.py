# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""iekke 2.0 (10.5281/zenodo.24279954) as a component that can decide every
property: its registry entry and options, its command line per property and
data model, the time budget CoOpeRace passes its wrapper (DEAGLE_BUDGET,
ComponentSpec.budget_variable), the status that BenchExec's iekke module
gives its real output and which of those CoOpeRace accepts, which of its
witness files count, and the test conf conf/svcomp27-iekke.json against the
rules for the competition's limits, the invariant of cooperace/D15 and the
first-verdict scenarios.

tests/unit/iekke-output holds (as .out files) the output of iekke 24279954 run
by CoOpeRace (conf/svcomp27-iekke.json limits, BenchExec 4e89886c in the
competition image user:2027 5235ed22, 3 GB, 1 core) on goblint, 18 November
2026, and tests/unit/witnesses/iekke the witness.yml each of those runs wrote,
unchanged. Each file is named <task>.<property>.<verdict>. No component is
run here: the version that the iekke module asks the tool for is set on the
tool-info object."""
import json
from pathlib import Path

import pytest
import test_config
import test_first_verdict
import test_no_synthesized_verdicts as d15

from src.cooperace import components, config, execution, strategy, witnesses
from src.cooperace.components import (
    OPTIONS_FILE,
    REGISTRY,
    TASK_LIMIT,
    ComponentRunner,
    ComponentVersion,
    WitnessSpec,
    read_component_versions,
)
from src.cooperace.config import Step
from src.cooperace.execution import FinishedRun

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "iekke-output"
WITNESSES = Path(__file__).resolve().parent / "witnesses" / "iekke"
PROPERTIES = ROOT / "tests" / "properties"
IEKKE_CONF_PATH = ROOT / "conf" / "svcomp27-iekke.json"
IEKKE_CONF = json.loads(IEKKE_CONF_PATH.read_text())
DOI = "10.5281/zenodo.24279954"


def download_tools_lock(root):
    """The components of tools.txt and of tools-pool.txt, by
    test_registry's scripts/download-tools.py."""
    from test_registry import download_tools

    return download_tools.read_lock_files(root / "tools.txt", root / "tools-pool.txt")


def iekke_tool():
    """The tool-info object of iekke, with the version its wrapper prints."""
    tool = REGISTRY["iekke"].tool()
    tool.version = lambda executable: "2.0"
    return tool


def iekke_runner(tmp_path, property_name, data_model="ILP32"):
    """A runner for a task of `property_name` whose iekke is a directory
    tmp_path/iekke holding an executable iekke."""
    directory = tmp_path / "iekke"
    directory.mkdir(exist_ok=True)
    wrapper = directory / "iekke"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    runner = ComponentRunner(str(tmp_path / "task.i"), str(PROPERTIES / f"{property_name}.prp"), data_model,
                             versions={str(directory): ComponentVersion(DOI, "2.0", ())},
                             property_name=property_name)
    spec = REGISTRY["iekke"]
    runner.registry["iekke"] = components.ComponentSpec(
        "iekke", spec.module, str(directory), spec.witness, budget_variable=spec.budget_variable,
        budget_margin=spec.budget_margin)
    return runner, str(wrapper)


# --- registry and options -------------------------------------------------------

def test_iekke_s_entry():
    spec = REGISTRY["iekke"]

    assert (spec.module, spec.directory, spec.task_limit) == ("benchexec.tools.iekke", "iekke", TASK_LIMIT)
    assert spec.witness == WitnessSpec("component", (), ("witness.yml",))
    assert (spec.budget_variable, spec.budget_margin, spec.gets_memory_limit) == ("DEAGLE_BUDGET", 30, False)
    assert spec.tool().name() == "iekke"


def test_only_iekke_reads_a_time_budget():
    assert [name for name, spec in REGISTRY.items() if spec.budget_variable] == ["iekke"]


def test_iekke_s_options_and_lock_line_are_those_of_version_2_0_of_its_fm_tools_entry():
    """iekke.yml of fm-tools main 05bf3109 (18 November 2026): version 2.0,
    10.5281/zenodo.24279954, no options; its pre-run of 17 November covers the
    task set, so tools-unlisted.txt has no exception for it."""
    versions = read_component_versions(str(ROOT / OPTIONS_FILE))
    archive, pool = download_tools_lock(ROOT)

    assert versions["iekke"] == ComponentVersion(DOI, "2.0", ())
    assert pool["iekke"] == DOI and "iekke" not in archive
    assert "iekke" not in (ROOT / "tools-unlisted.txt").read_text().split("#")[-1]


# --- the command line and the budget ---------------------------------------------

@pytest.mark.parametrize(("data_model", "flag"), [("ILP32", "--32"), ("LP64", "--64")])
@pytest.mark.parametrize("property_name", ["no-data-race", "unreach-call", "no-overflow", "valid-memsafety"])
def test_iekke_s_command_line_per_property_and_data_model(tmp_path, property_name, data_model, flag):
    """As bench-defs' iekke.xml runs it: the property file, the task and the
    data model; the iekke module passes no options."""
    runner, wrapper = iekke_runner(tmp_path, property_name, data_model)

    command = runner.command(iekke_tool(), runner.registry["iekke"], "/w", 2_700_000_000, 600)

    assert command.cmdline == [wrapper, str(PROPERTIES / f"{property_name}.prp"), str(tmp_path / "task.i"), flag]
    assert command.cwd == str(tmp_path / "iekke")


@pytest.mark.parametrize(("budget", "value"), [(600, "570"), (870, "840"), (440.9, "410"), (31, "1"), (12, "0"),
                                               (0, "0")])
def test_iekke_gets_its_budget_less_the_margin_in_whole_seconds(tmp_path, budget, value):
    runner, _wrapper = iekke_runner(tmp_path, "unreach-call")

    command = runner.command(iekke_tool(), runner.registry["iekke"], "/w", None, budget)

    assert command.environments == {"newEnv": {"DEAGLE_BUDGET": value}}


def test_without_a_budget_iekke_s_environment_is_its_module_s(tmp_path):
    """Then its wrapper keeps its own default, 870 s, as in the competition."""
    runner, _wrapper = iekke_runner(tmp_path, "unreach-call")

    assert runner.command(iekke_tool(), runner.registry["iekke"], "/w", None, None).environments == {}


def test_a_component_without_a_budget_variable_gets_no_budget(tmp_path):
    runner, _wrapper = iekke_runner(tmp_path, "unreach-call")
    spec = runner.registry["iekke"]
    unmarked = components.ComponentSpec(spec.name, spec.module, spec.directory, spec.witness)

    assert runner.command(iekke_tool(), unmarked, "/w", None, 600).environments == {}


@pytest.mark.parametrize(("cpu", "run_budget", "used", "expected"), [
    (600, 870, 0.0, 600),      # first in its property: its own limit
    (600, 870, 400.0, 470),    # started late: what is left of the run's budget
    (None, 870, 100.0, 770),   # no limit of its own
    (600, None, 400.0, 600),   # no run budget
    (600, 870, None, 600),     # the run's CPU time unknown
    (600, 870, 900.0, 0),      # nothing left
    (None, None, 10.0, None),  # nothing bounds it
])
def test_component_time_budget(cpu, run_budget, used, expected):
    step = Step("iekke", "all", cpu_time_limit=cpu, run_cpu_time_budget=run_budget)

    assert components.component_time_budget(step, cpu, used) == expected


def test_run_component_passes_what_is_left_of_the_run_s_budget(tmp_path, monkeypatch, capsys):
    """run_component reads the run's CPU time when iekke starts: started
    after 400 s, with its own limit of 600 s and the run's budget of 870 s,
    iekke may run 470 s, and its wrapper is told 440."""
    monkeypatch.setattr(components, "run_cpu_seconds", lambda: 400.0)
    runner, _wrapper = iekke_runner(tmp_path, "unreach-call")
    seen = {}
    real_command = runner.command

    def command(actor, spec, witness_dir, memory=None, budget=None):
        seen["environments"] = real_command(actor, spec, witness_dir, memory, budget).environments
        raise RuntimeError("stop here")

    monkeypatch.setattr(runner, "command", command)
    runner.work_dir = str(tmp_path)
    with pytest.raises(RuntimeError, match="stop here"):
        runner.run_component(iekke_tool(), Step("iekke", "all", cpu_time_limit=600, run_cpu_time_budget=870), None)

    assert seen["environments"] == {"newEnv": {"DEAGLE_BUDGET": "440"}}
    assert "Time budget of iekke: 470 s (DEAGLE_BUDGET=440)" in capsys.readouterr().out


def test_other_components_announce_no_budget(capsys):
    step = Step("Deagle", "all", cpu_time_limit=15, run_cpu_time_budget=870)

    assert components.announce_time_budget(REGISTRY["Deagle"], step, 15) is None
    assert capsys.readouterr().out == ""


# --- result ------------------------------------------------------------------------

def status_of(log, returncode=0, termination_reason=None, property_name="no-data-race"):
    """The status component_status gives iekke's output `log` (a file of
    OUTPUT, or text), and the verdict CoOpeRace takes from it on
    `property_name` with acceptance "all"."""
    text = (OUTPUT / log).read_text() if log.endswith(".out") else log
    run = FinishedRun(["iekke"], returncode, text, termination_reason=termination_reason)
    status = components.component_status(iekke_tool(), ["iekke"], run, termination_reason)
    verdict = next((v for v in ("true", "false") if components.confirm_verdict("all", status, v, property_name)),
                   "unknown")
    return status, verdict


OUTPUTS = [
    ("28-race_reach_08-cond_racefree.no-data-race.true.out", "no-data-race", "true", "true"),
    ("header-words-ilp32.no-data-race.true.out", "no-data-race", "true", "true"),
    ("05-lval_ls_05-glob_idx_rc.no-data-race.false.out", "no-data-race", "false(no-data-race)", "false"),
    ("header-words.no-data-race.false.out", "no-data-race", "false(no-data-race)", "false"),
    ("unreach-unreachable-single-init.unreach-call.true.out", "unreach-call", "true", "true"),
    ("rfi006_power.oepc.unreach-call.false.out", "unreach-call", "false(unreach-call)", "false"),
    ("tls_destructor_worker.no-overflow.true.out", "no-overflow", "true", "true"),
    ("order-flag-before-result.no-overflow.false.out", "no-overflow", "false(no-overflow)", "false"),
    ("bigshot_s.valid-memsafety.true.out", "valid-memsafety", "true", "true"),
    ("bigshot_s2.valid-memsafety.false.out", "valid-memsafety", "false(valid-deref)", "false"),
]


def test_the_iekke_outputs_are_there():
    assert sorted(path.name for path in OUTPUT.glob("*.out")) == sorted(row[0] for row in OUTPUTS)


@pytest.mark.parametrize(("log", "property_name", "status", "verdict"), OUTPUTS)
def test_iekke_s_real_output_gives_its_verdict(log, property_name, status, verdict):
    assert status_of(log, property_name=property_name) == (status, verdict)


def test_iekke_s_unknown_exit_is_unknown():
    """The wrapper ends without an answer with UNKNOWN and exit code 1."""
    assert status_of("try unwind=2 rounds=2\n  -> no verdict\nUNKNOWN\n", 1) == ("unknown", "unknown")


@pytest.mark.parametrize(("reason", "status"), [("cputime", "TIMEOUT"), ("memory", "OUT OF MEMORY"),
                                                ("walltime", "TIMEOUT")])
def test_an_iekke_ended_by_a_limit_without_an_answer_gives_no_verdict(reason, status):
    """Killed by its limit (the wrapper's own SIGTERM handler exits 143, a
    SIGKILL gives -9), the iekke module's ERROR is refined to the limit."""
    assert status_of("try unwind=2 rounds=2\n", -9, reason, "unreach-call") == (status, "unknown")
    assert status_of("try unwind=2 rounds=2\n", 143, reason, "unreach-call") == (status, "unknown")


@pytest.mark.parametrize("reason", ["cputime", "memory", "walltime"])
def test_in_the_first_verdict_mode_a_verdict_of_an_iekke_ended_by_its_limit_is_not_accepted(
        tmp_path, monkeypatch, reason):
    """The iekke module reads SUCCESSFUL on any line before it looks at how the
    run ended, so its status is "true" even for a run that its CPU-time,
    memory or wall-time limit ended; run_component does not accept it."""
    runner, _wrapper = iekke_runner(tmp_path, "unreach-call")
    runner.work_dir = str(tmp_path)
    output = (OUTPUT / "unreach-unreachable-single-init.unreach-call.true.out").read_text()

    def execute_run(command, cwd, environments, group, log_file, dir_modes, **limits):
        (Path(limits["output_dir"]) / "witness.yml").write_text(
            (WITNESSES / "unreach-unreachable-single-init.unreach-call.true.yml").read_text())
        return FinishedRun(command, -9, output, termination_reason=reason)

    monkeypatch.setattr(execution, "execute_run", execute_run)
    monkeypatch.setattr(components, "run_cpu_seconds", lambda: 0.0)
    step = Step("iekke", "all", cpu_time_limit=600, run_cpu_time_budget=870, first_verdict=True)

    assert status_of(output, -9, reason, "unreach-call") == ("true", "true")
    assert runner.run_component(iekke_tool(), step, components.ComponentGroup()).verdict == "unknown"


# --- witnesses ---------------------------------------------------------------------

IEKKE_WITNESSES = sorted(WITNESSES.glob("*.yml"))


def test_the_iekke_witnesses_are_there():
    assert [path.name for path in IEKKE_WITNESSES] == [
        "05-lval_ls_05-glob_idx_rc.no-data-race.false.yml", "header-words.no-data-race.false.yml",
        "order-flag-before-result.no-overflow.false.yml", "rfi006_power.oepc.unreach-call.false.yml",
        "tls_destructor_worker.no-overflow.true.yml", "unreach-unreachable-single-init.unreach-call.true.yml"]


@pytest.mark.parametrize("path", IEKKE_WITNESSES, ids=lambda path: path.name)
def test_iekke_s_witness_counts_for_its_property_and_verdict_only(path):
    task_and_property, verdict, _yml = path.name.rsplit(".", 2)
    property_name = task_and_property.rsplit(".", 1)[1]
    subproperty = property_name if verdict == "false" else None

    assert witnesses.file_passes(str(path), property_name, verdict)
    assert witnesses.passes(path.read_text(), property_name, verdict, subproperty)
    other = "true" if verdict == "false" else "false"
    assert not witnesses.passes(path.read_text(), property_name, other)
    for prop in ("no-data-race", "unreach-call", "no-overflow"):
        if prop != property_name:
            assert not witnesses.passes(path.read_text(), prop, verdict)


def test_iekke_s_correctness_witness_is_an_invariant_set_without_invariants():
    text = (WITNESSES / "unreach-unreachable-single-init.unreach-call.true.yml").read_text()

    assert "entry_type: 'invariant_set'" in text and "format_version: '2.1'" in text
    assert text.rstrip().endswith("content: []")


def test_iekke_s_witness_is_taken_from_its_directory_after_a_run(tmp_path):
    """witness.yml, which the wrapper writes into its working directory, is a
    candidate; the wrapper's intermediate witness-content.yml and
    error-witness.graphml are not."""
    for name in ("witness.yml", "witness-content.yml", "error-witness.graphml"):
        (tmp_path / name).write_text("x")

    spec = REGISTRY["iekke"].witness
    assert components.result_files_patterns(spec) == ["witness.yml"]
    assert components.witness_files_in(spec, str(tmp_path)) == [str(tmp_path / "witness.yml")]


# --- the test conf conf/svcomp27-iekke.json ----------------------------------------

def test_the_iekke_conf_runs_iekke_alone_first_in_every_property_in_the_first_verdict_mode():
    strategies = config.load_strategies(IEKKE_CONF, REGISTRY)

    assert set(strategies) == {"no-data-race", "unreach-call", "no-overflow", "valid-memsafety"}
    for loaded in strategies.values():
        assert loaded.first_verdict and loaded.witness_lint
        assert loaded.phases[0].tools == ("iekke",)
        assert loaded.cpu_time_limits["iekke"] == 600 and loaded.memory_limits["iekke"] == "90%"
        assert loaded.bracket_of("iekke", "true") is not None and loaded.bracket_of("iekke", "false") is not None


def test_the_iekke_conf_adds_only_iekke_to_the_archive():
    archive, pool = download_tools_lock(ROOT)
    names = {name for loaded in config.load_strategies(IEKKE_CONF, REGISTRY).values() for name in loaded.tools}

    assert {REGISTRY[name].directory for name in names} - set(archive) == {"iekke"}
    assert "iekke" in pool


@pytest.mark.parametrize("rule", [
    test_config.test_the_svcomp27_conf_gives_every_property_a_budget_below_the_competitions_900_s,
    test_config.test_the_svcomp27_conf_limits_the_memory_of_each_tool_that_runs_until_the_budget,
    test_config.test_the_svcomp27_conf_limits_the_memory_of_every_phase_1_tool,
    test_config.test_the_svcomp27_conf_limits_the_memory_of_every_phase_to_95_percent,
    test_config.test_the_svcomp27_conf_ends_every_phase_of_tools_with_cpu_time_limits_by_a_wall_time_limit,
    test_config.test_the_svcomp27_conf_keeps_the_task_limits_of_every_phase_to_4096,
], ids=lambda rule: rule.__name__.removeprefix("test_the_svcomp27_conf_"))
def test_the_iekke_conf_keeps_the_rules_for_the_competition_s_limits(rule):
    rule(REGISTRY, IEKKE_CONF_PATH)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(strategy, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(execution, "task_limit_supported", lambda: True)


@pytest.mark.parametrize("property_name", d15.PROPERTIES)
def test_d15_on_the_iekke_conf(property_name, tmp_path, monkeypatch, capsys, fast):
    """The invariant of cooperace/D15 (test_no_synthesized_verdicts.py) on the
    strategies of the iekke conf, 30 scenarios per property."""
    monkeypatch.setitem(d15.CONFS, "svcomp27-iekke", IEKKE_CONF)
    monkeypatch.setitem(d15.SCENARIOS, "svcomp27-iekke", d15.SCENARIOS_PER_PROPERTY // 2)
    d15.test_a_definite_result_is_a_component_s_accepted_verdict_and_otherwise_unknown(
        "svcomp27-iekke", property_name, tmp_path, monkeypatch, capsys, None)


@pytest.mark.parametrize("lint", [False, True], ids=["internal-check", "witnesslint"])
@pytest.mark.parametrize("property_name", list(test_first_verdict.PROPERTY_FILES))
def test_first_verdict_scenarios_on_the_iekke_conf(property_name, lint, tmp_path, monkeypatch, capsys, fast):
    conf_strategy = {key: value for key, value in IEKKE_CONF["properties"][property_name].items()
                     if key != "witnessLint"}
    conf_strategy = test_first_verdict.scaled(conf_strategy, test_first_verdict.FV_THETA_WALL_SCALE)
    test_first_verdict.check_scenarios(property_name, lambda _rng: conf_strategy,
                                       f"iekke {property_name}" + (" lint" if lint else ""), lint,
                                       tmp_path, monkeypatch, capsys)
