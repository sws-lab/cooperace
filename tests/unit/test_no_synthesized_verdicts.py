# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""No synthesized verdicts (cooperace/D15): CoOpeRace reports `true` or
`false(...)` only if a component produced that answer.

For every strategy of conf/svcomp27.json and of conf/svcomp27-theta.json (the
conf variant with Theta, scripts/svcomp-dist.sh CONF_VARIANT=theta), cli.run (and so strategy.execute)
runs the real ComponentRunner.run_component, whose components are faked one
level below it: execution.execute_run waits a random time, or until CoOpeRace
stops the component, and ends as the scenario says (a status, a hang guard
of execution.execute_run, or an exception), writing a witness that passes or
one that does not; component_status returns the scenario's status. The run's
CPU-time budget expires at a random time (strategy.run_cpu_seconds), and the
phases' wall-time limits are those of the conf scaled down, so that both cut
runs short. Over many seeded random scenarios:

- a result `true` or `false(...)` names, in "CoOpeRace result from: ...", a
  component whose status in this run is a verdict of that polarity that
  confirm_verdict accepts on the property, for which the strategy has an
  entry in `answers`, and the result is reported_result of that status;
- when no component's status is a verdict that confirm_verdict accepts, the
  result is exactly `unknown`.
"""
import copy
import json
import random
import threading
import time
from pathlib import Path

import pytest
from benchexec import result as bresult
from support import CORRECTNESS_WITNESS, PASSING_WITNESS

from src.cooperace import cli, components, execution, strategy
from src.cooperace.components import REGISTRY, ComponentCommand, ComponentRunner, confirm_verdict, reported_result
from src.cooperace.config import load_strategies
from src.cooperace.processes import ComponentGroup
from src.cooperace.witnesses import specification, subproperty_of

ROOT = Path(__file__).resolve().parents[2]
CONF = json.loads((ROOT / "conf" / "svcomp27.json").read_text())
PROPERTIES = list(CONF["properties"])
# The confs whose strategies are run, by name. The variant's six sequential
# phases take longer per scenario, so it gets fewer scenarios.
CONFS = {"svcomp27": CONF, "svcomp27-theta": json.loads((ROOT / "conf" / "svcomp27-theta.json").read_text()),
         "svcomp27-fv-theta": json.loads((ROOT / "conf" / "svcomp27-fv-theta.json").read_text())}
PROPERTY_FILES = {name: ROOT / "tests" / "properties" / f"{name}.prp" for name in PROPERTIES}

# The scale of the conf's wall-time limits (45 s and 120 s become 0.45 s and 1.2 s)
WALL_SCALE = 0.01
SCENARIOS_PER_PROPERTY = 60
SCENARIOS = {"svcomp27": SCENARIOS_PER_PROPERTY, "svcomp27-theta": SCENARIOS_PER_PROPERTY // 2,
             "svcomp27-fv-theta": SCENARIOS_PER_PROPERTY // 2}

# Every status a component can end with that matters here: the verdicts of each property, verdicts
# of other properties, and the statuses of a component without a verdict
STATUSES = [bresult.RESULT_TRUE_PROP, bresult.RESULT_FALSE_PROP, bresult.RESULT_FALSE_REACH,
            bresult.RESULT_FALSE_OVERFLOW, bresult.RESULT_FALSE_DATARACE, bresult.RESULT_FALSE_DEREF,
            bresult.RESULT_FALSE_FREE, bresult.RESULT_FALSE_MEMTRACK, bresult.RESULT_FALSE_TERMINATION,
            bresult.RESULT_UNKNOWN, bresult.RESULT_ERROR, "ERROR (1)", "TIMEOUT", "OUT OF MEMORY", "KILLED",
            "SEGMENTATION FAULT", "TIMEOUT (true)", "True", "false(no-data-race) "]


def witness_text(property_name, verdict, status):
    """A witness that witnesses.passes accepts for `verdict` on `property_name`
    with the sub-property of `status`."""
    formulas = " ".join(specification(property_name, verdict, subproperty_of(status) or property_name))
    if verdict == "false":
        return PASSING_WITNESS.read_text().replace("CHECK( init(main()), LTL(G ! data-race) )", formulas)
    return CORRECTNESS_WITNESS.format(specification=formulas)


class Scenario:
    """What each component of a strategy does in one run, drawn from `rng`:
    after `delay` seconds it ends with `status` ("hang-start", "hang-stop" and
    "raise" are a hang guard at the start, one at the stop, and an exception
    instead), writing a passing witness if `witness` is "passing", one that
    passes no check if "failing", none if None; and when the budget expires."""

    def __init__(self, rng, tools):
        self.behaviour = {}
        for tool in tools:
            kind = rng.choices(["status", "hang-start", "hang-stop", "raise"], [16, 1, 1, 1])[0]
            status = rng.choice(STATUSES) if kind == "status" else kind
            self.behaviour[tool] = (rng.uniform(0, 0.3), status, rng.choice(["passing", "failing", None]))
        self.budget_after = rng.choice([None, rng.uniform(0, 0.8)])


class FakeComponents:
    """The fakes of execution.execute_run, components.component_status and the
    budget for `scenario`, recording the status each component ended with."""

    def __init__(self, scenario, property_name, names):
        self.scenario = scenario
        self.property_name = property_name
        #The conf's name of each component, by the name its tool-info module gives it
        self.conf_name = names
        self.ended = {}
        self.lock = threading.Lock()
        self.start = None

    def tool_of_thread(self):
        return threading.current_thread().name.removeprefix("CoOpeRace run of ")

    def execute_run(self, cmdline, cwd, environments, group, *, output_dir, **_limits):
        tool = self.tool_of_thread()
        delay, status, witness = self.scenario.behaviour[tool]
        if status == "hang-start":
            raise execution.ComponentHang("start timed out")
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and not group.stopped:
            time.sleep(0.005)
        if group.stopped:
            if status == "hang-stop":
                raise execution.ComponentHang("did not stop")
            return execution.FinishedRun(cmdline, -9, "", termination_reason="killed")
        if status == "raise":
            raise RuntimeError("a component's run failed")
        if status == "hang-stop":
            raise execution.ComponentHang("did not return after its process ended")
        if witness is not None:
            spec = REGISTRY[tool].witness
            name = spec.files[0] if spec.files else "witness.yml"
            verdict = "true" if status == bresult.RESULT_TRUE_PROP else "false"
            text = witness_text(self.property_name, verdict, status) if witness == "passing" else "- not: a witness\n"
            path = Path(output_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        with self.lock:
            self.ended[tool] = status
        return execution.FinishedRun(cmdline, 0, "", cpu_time=delay, wall_time=delay)

    def component_status(self, actor, cmdline, tool_result, termination_reason=None):
        return self.scenario.behaviour[self.tool_of_thread()][1]

    def run_cpu_seconds(self):
        if self.scenario.budget_after is None or time.monotonic() - self.start < self.scenario.budget_after:
            return 0.0
        return 1e6


@pytest.fixture
def fast_strategies(monkeypatch):
    """The waits of run_strategy shortened, and no task limit probed."""
    monkeypatch.setattr(strategy, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(execution, "task_limit_supported", lambda: True)


def scaled_conf(original=CONF):
    conf = copy.deepcopy(original)
    for entry in conf["properties"].values():
        for phase in entry["phases"]:
            if isinstance(phase, dict) and "wallTimeLimit" in phase:
                phase["wallTimeLimit"] *= WALL_SCALE
    return conf


@pytest.mark.parametrize("property_name", PROPERTIES)
@pytest.mark.parametrize("conf_name", list(CONFS))
def test_a_definite_result_is_a_component_s_accepted_verdict_and_otherwise_unknown(
        conf_name, property_name, tmp_path, monkeypatch, capsys, fast_strategies):
    conf = scaled_conf(CONFS[conf_name])
    root = load_strategies(conf, REGISTRY)[property_name]
    task = tmp_path / "task.c"
    task.write_text("int main(void) { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    names = {REGISTRY[tool].tool().name(): tool for tool in root.tools}
    #The default conf keeps the seeds it had before the variant was added
    rng = random.Random(f"D15 {property_name}" if conf_name == "svcomp27" else f"D15 {conf_name} {property_name}")
    definite = unknown = 0

    for number in range(SCENARIOS[conf_name]):
        scenario = Scenario(rng, root.tools)
        fakes = FakeComponents(scenario, property_name, names)
        monkeypatch.setattr(execution, "execute_run", fakes.execute_run)
        monkeypatch.setattr(components, "component_status", fakes.component_status)
        monkeypatch.setattr(strategy, "run_cpu_seconds", fakes.run_cpu_seconds)
        runner = ComponentRunner(str(task), str(PROPERTY_FILES[property_name]), "ILP32",
                                 property_name=property_name)
        runner.installation_problems = lambda _names: []
        runner.container_problems = lambda: []
        runner.probe_versions = lambda _names: None
        runner.command = lambda actor, spec, witness_dir, memory=None, budget=None: ComponentCommand(
            ["component"], str(tmp_path), {})
        fakes.start = time.monotonic()

        result = cli.run(conf, runner, ComponentGroup())

        out = capsys.readouterr().out
        where = f"scenario {number} of {property_name}: {scenario.behaviour}, budget {scenario.budget_after}"
        accepted = {tool: status for tool, status in fakes.ended.items()
                    if confirm_verdict("all", status, "true", property_name)
                    or confirm_verdict("all", status, "false", property_name)}
        if result == "true" or result.startswith("false"):
            definite += 1
            verdict = "true" if result == "true" else "false"
            deciders = [line.removeprefix("CoOpeRace result from: ") for line in out.splitlines()
                        if line.startswith("CoOpeRace result from: ")]
            assert len(deciders) == 1, where
            tool = names[deciders[0]]
            status = fakes.ended.get(tool)
            assert status is not None, where
            assert confirm_verdict("all", status, verdict, property_name), where
            assert root.bracket_of(tool, verdict) is not None, where
            assert result == reported_result(property_name, status), where
        else:
            assert result == "unknown" or result.startswith("ERROR (contradiction"), where
            if result == "unknown":
                unknown += 1
        if not accepted:
            assert result == "unknown", where

    #The scenarios reach both kinds of result
    assert definite > 0 and unknown > 0
