# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The veto window of valid-memsafety in conf/svcomp27.json: Deagle's `true`
waits for ULTIMATE GemCutter's run, and GemCutter's `false` vetoes it.

Each test runs cli.run on the strategy of the shipped conf, with its phases'
wall-time limits scaled down (WALL_SCALE of test_no_synthesized_verdicts, so
phase 2 lasts 1.5 s), on components faked below run_component as in that
test, with one fixed scenario."""
import time

import pytest
from benchexec import result as bresult
from test_no_synthesized_verdicts import PROPERTY_FILES, FakeComponents, scaled_conf

from src.cooperace import cli, components, execution, strategy
from src.cooperace.components import REGISTRY, ComponentCommand, ComponentRunner
from src.cooperace.config import load_strategies
from src.cooperace.processes import ComponentGroup

PROPERTY = "valid-memsafety"
GEMCUTTER = "ULTIMATE GemCutter"
DEREF = bresult.RESULT_FALSE_DEREF
# Phase 1 ends at once without a verdict
QUIET_PHASE_1 = {"sv-sanitizers": (0.01, bresult.RESULT_UNKNOWN, None), "Goblint": (0.01, bresult.RESULT_UNKNOWN, None)}


class FixedScenario:
    def __init__(self, behaviour, budget_after=None):
        self.behaviour = {**QUIET_PHASE_1, **behaviour}
        self.budget_after = budget_after


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """A function that runs the scenario of `behaviour` ({tool: (delay,
    status, witness)}) and returns (result, the component it names, the
    output, the seconds the run took)."""
    monkeypatch.setattr(strategy, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(execution, "task_limit_supported", lambda: True)
    conf = scaled_conf()
    task = tmp_path / "task.c"
    task.write_text("int main(void) { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    root = load_strategies(conf, REGISTRY)[PROPERTY]
    names = {REGISTRY[tool].tool().name(): tool for tool in root.tools}

    def go(behaviour, budget_after=None):
        fakes = FakeComponents(FixedScenario(behaviour, budget_after), PROPERTY, names)
        monkeypatch.setattr(execution, "execute_run", fakes.execute_run)
        monkeypatch.setattr(components, "component_status", fakes.component_status)
        monkeypatch.setattr(strategy, "run_cpu_seconds", fakes.run_cpu_seconds)
        runner = ComponentRunner(str(task), str(PROPERTY_FILES[PROPERTY]), "ILP32", property_name=PROPERTY)
        runner.installation_problems = lambda _names: []
        runner.container_problems = lambda: []
        runner.probe_versions = lambda _names: None
        runner.command = lambda actor, spec, witness_dir: ComponentCommand(["component"], str(tmp_path), {})
        fakes.start = time.monotonic()
        result = cli.run(conf, runner, ComponentGroup())
        took = time.monotonic() - fakes.start
        out = capsys.readouterr().out
        deciders = [names[line.removeprefix("CoOpeRace result from: ")] for line in out.splitlines()
                    if line.startswith("CoOpeRace result from: ")]
        return result, (deciders or [None])[0], out, took

    return go


def test_the_conf_holds_deagle_s_true_for_gemcutter():
    root = load_strategies(scaled_conf(), REGISTRY)[PROPERTY]

    assert root.phases[1].tools == ("Deagle", GEMCUTTER) and root.phases[2].tools == ("Deagle",)
    assert root.cpu_time_limits[GEMCUTTER] == 60
    assert root.bracket_of(GEMCUTTER, "false") < root.bracket_of("Deagle", "true")
    assert dict(root.veto_windows) == {("Deagle", "true"): (GEMCUTTER,)}


def test_a_veto_in_the_window_gives_gemcutter_s_false(run):
    result, decider, out, _took = run({"Deagle": (0.02, "true", None), GEMCUTTER: (0.2, DEREF, "passing")})

    assert (result, decider) == ("false(valid-deref)", GEMCUTTER)


def test_a_false_after_the_window_vetoes_nothing_and_deagle_s_true_comes_at_the_window_s_end(run):
    #GemCutter would answer after phase 2's wall-time limit (1.5 s here), which stops it
    result, decider, out, took = run({"Deagle": (0.02, "true", None), GEMCUTTER: (3.0, DEREF, "passing")})

    assert (result, decider) == ("true", "Deagle")
    assert "has passed: stopping ULTIMATE GemCutter" in out
    assert 1.5 <= took < 3.0


@pytest.mark.parametrize("status", ["OUT OF MEMORY", "ERROR (1)", "TIMEOUT", "KILLED", bresult.RESULT_UNKNOWN,
                                    bresult.RESULT_TRUE_PROP, "hang-start", "hang-stop", "raise"])
def test_a_gemcutter_that_ends_without_a_false_vetoes_nothing(run, status):
    result, decider, _out, took = run({"Deagle": (0.02, "true", None), GEMCUTTER: (0.4, status, None)})

    assert (result, decider) == ("true", "Deagle")
    if status != "hang-start":
        #Deagle's true waited for GemCutter's end
        assert took >= 0.4


def test_deagle_s_false_does_not_wait_for_gemcutter(run):
    result, decider, _out, took = run({"Deagle": (0.02, DEREF, "passing"), GEMCUTTER: (1.2, DEREF, "passing")})

    assert (result, decider) == ("false(valid-deref)", "Deagle")
    assert took < 1.0


def test_without_an_answer_of_deagle_gemcutter_runs_only_its_own_window(run):
    result, decider, _out, took = run({"Deagle": (0.02, bresult.RESULT_UNKNOWN, None),
                                       GEMCUTTER: (0.3, bresult.RESULT_UNKNOWN, None)})

    assert (result, decider) == ("unknown", None)
    assert took < 1.0


def test_budget_expiry_in_the_window_gives_unknown(run):
    result, decider, out, _took = run({"Deagle": (0.02, "true", None), GEMCUTTER: (3.0, DEREF, "passing")},
                                      budget_after=0.5)

    assert (result, decider) == ("unknown", None)
    assert "waits for a veto of ULTIMATE GemCutter, which the CPU-time budget stopped" in out


def test_budget_expiry_after_the_window_keeps_deagle_s_true(run):
    #GemCutter ends without a verdict at 0.1 s; Deagle answers true at 0.3 s, which is delivered at once
    result, decider, _out, _took = run({"Deagle": (0.3, "true", None),
                                        GEMCUTTER: (0.1, bresult.RESULT_UNKNOWN, None)}, budget_after=0.6)

    assert (result, decider) == ("true", "Deagle")
