# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""strategy.execute and run_strategy with a fake StepRunner, whose
components are threads that wait for a given time and return a given
Outcome: which components start and stop, what the run prints, and which
witness it delivers. A component here is stopped as a real one is, through
the ComponentGroup that run_strategy runs it in.

The randomized test at the end checks the criterion of conf/README.md,
"When a tool is stopped", through the threads: on random strategies and
answers, the result equals what test_verdicts.reference computes from the
answers of every component run to its end."""
import dataclasses
import random
import threading
import time
from typing import NamedTuple

import pytest
from test_verdicts import random_case, reference

from src.cooperace import config, strategy
from src.cooperace.processes import ComponentGroup
from src.cooperace.verdicts import FALSE, TRUE

# A component that never ends by itself.
FOREVER = None


class Script(NamedTuple):
    """What a fake component does: it ends `seconds` after it starts
    (FOREVER: only when stopped) with the verdict `verdict` ("true",
    "false" or None for none), with a witness file if `witness`, and with the
    sub-property `subproperty` of a false."""

    seconds: float | None
    verdict: str | None = None
    witness: bool = False
    subproperty: str | None = None


class FakeProcess:
    """What a ComponentGroup records for a running fake component: stop()
    sets `stopped`, as execution.ExecutorRun.stop ends a RunExecutor run."""

    def __init__(self):
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()


class FakeRunner:
    """A StepRunner for `property_name` whose components behave as `scripts`
    (component name to Script) says. Records the components started
    (`started`, in order), those stopped (`stopped`), the Step of each
    (`steps`), the witness files delivered and the calls of prepare and
    cleanup (`calls`). A component's witness file is "<name>.witness.yml";
    its result is the verdict, with "(<sub-property>)" for a false with one."""

    def __init__(self, property_name, scripts):
        self.property_name = property_name
        self.scripts = scripts
        self.lock = threading.Lock()
        self.started, self.stopped, self.steps, self.calls = [], [], {}, []

    def prepare(self):
        self.calls.append("prepare")

    def cleanup(self):
        self.calls.append("cleanup")

    def deliver(self, witness_files):
        self.calls.append(("deliver", list(witness_files)))

    def run_step(self, step, group):
        name = step.component
        process = FakeProcess()
        if not group.add(process):
            return strategy.NO_OUTCOME
        with self.lock:
            self.started.append(name)
            self.steps[name] = step
        try:
            script = self.scripts[name]
            if process.stopped.wait(script.seconds):
                with self.lock:
                    self.stopped.append(name)
                return strategy.NO_OUTCOME
        finally:
            group.remove(process)
        if script.verdict is None:
            return strategy.NO_OUTCOME
        result = script.verdict if script.subproperty is None else f"{script.verdict}({script.subproperty})"
        return strategy.Outcome(script.verdict, name, [f"{name}.witness.yml"] if script.witness else [], result)


def run(conf_strategy, property_name, **scripts):
    """Runs the conf strategy `conf_strategy` with execute and a FakeRunner of
    `scripts` (component name, "_" for "-", to Script); returns the result
    and the runner."""
    scripts = {name.replace("_", "-"): script for name, script in scripts.items()}
    loaded = config.load(conf_strategy, scripts)
    runner = FakeRunner(property_name, scripts)
    return strategy.execute(loaded, runner, ComponentGroup()), runner


def printed(capsys):
    """The lines of the captured standard output."""
    return capsys.readouterr().out.splitlines()


DATA_RACE = {"phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 60}, ["Goblint", "Dartagnan"]],
             "cpuTimeLimits": {"sv-sanitizers": 30},
             "answers": [["Goblint:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
             "witnesses": ["sv-sanitizers", "Dartagnan"]}


# --- the examples of strategy-format item 2 -----------------------------------

def test_a_witnessed_false_ends_the_run_when_goblint_ends_and_dartagnan_never_starts(capsys):
    started = time.monotonic()
    result, runner = run(DATA_RACE, "no-data-race",
                         sv_sanitizers=Script(0.05, FALSE, witness=True), Goblint=Script(0.4),
                         Dartagnan=Script(0.05, FALSE, witness=True))
    lines = printed(capsys)

    assert result == "false"
    assert runner.started == ["sv-sanitizers", "Goblint"]
    assert runner.stopped == []          # Goblint ran to its end
    assert time.monotonic() - started >= 0.4
    assert ("deliver", ["sv-sanitizers.witness.yml"]) in runner.calls
    assert lines[-1] == "CoOpeRace result from: sv-sanitizers"
    assert "CoOpeRace answer: sv-sanitizers false, bracket 2, witness sv-sanitizers.witness.yml" in lines


def test_a_witness_less_false_starts_dartagnan_in_phase_2_and_delivers_its_witness(capsys):
    result, runner = run(DATA_RACE, "no-data-race",
                         sv_sanitizers=Script(0.05, FALSE), Goblint=Script(0.2),
                         Dartagnan=Script(0.05, FALSE, witness=True))
    lines = printed(capsys)

    assert result == "false"
    assert runner.started == ["sv-sanitizers", "Goblint", "Dartagnan"]
    assert ("deliver", ["Dartagnan.witness.yml"]) in runner.calls
    assert "CoOpeRace answer: sv-sanitizers false, bracket 2, no witness that passes the check" in lines
    assert lines[-1] == "CoOpeRace result from: Dartagnan"


def test_goblints_true_overrides_sv_sanitizers_false(capsys):
    result, runner = run(DATA_RACE, "no-data-race",
                         sv_sanitizers=Script(0.05, FALSE, witness=True), Goblint=Script(0.2, TRUE),
                         Dartagnan=Script(0.05, FALSE, witness=True))
    lines = printed(capsys)

    assert result == "true"
    assert "Dartagnan" not in runner.started
    assert ("deliver", []) in runner.calls
    assert "CoOpeRace: sv-sanitizers false in bracket 2 is overridden by true in bracket 1" in lines
    assert lines[-1] == "CoOpeRace result from: Goblint"


CONTRADICTING = {"phases": [["Goblint", "Dartagnan", "Deagle"]],
                 "answers": [["Deagle:true"], ["Goblint:true", "Dartagnan:false"]],
                 "witnesses": ["Dartagnan"]}


def test_a_contradiction_in_one_bracket_ends_in_an_error(capsys):
    result, runner = run(CONTRADICTING, "no-data-race",
                         Goblint=Script(0.05, TRUE), Dartagnan=Script(0.1, FALSE, witness=True), Deagle=Script(0.2))
    lines = printed(capsys)

    assert result == "ERROR (contradiction: Goblint true, Dartagnan false)"
    assert ("deliver", []) in runner.calls
    assert runner.stopped == []          # Deagle, above, was waited for
    assert not any(line.startswith("CoOpeRace verdict: ") for line in lines)
    assert not any(line.startswith("CoOpeRace result from:") for line in lines)


def test_a_contradiction_is_overridden_by_a_later_answer_of_a_higher_bracket(capsys):
    result, _ = run(CONTRADICTING, "no-data-race",
                    Goblint=Script(0.05, TRUE), Dartagnan=Script(0.1, FALSE, witness=True), Deagle=Script(0.2, TRUE))

    assert result == "true"
    assert printed(capsys)[-1] == "CoOpeRace result from: Deagle"


def test_a_contradiction_stops_the_tools_left_once_no_higher_bracket_runs(capsys):
    conf = {"phases": [["A", "B", "C"]], "answers": [["A:true", "B:false", "C"]], "witnesses": []}
    started = time.monotonic()
    result, runner = run(conf, "no-data-race", A=Script(0.05, TRUE), B=Script(0.1, FALSE), C=Script(FOREVER))

    assert result == "ERROR (contradiction: A true, B false)"
    assert runner.stopped == ["C"]
    assert time.monotonic() - started < 5


def test_a_witness_less_false_overriding_a_lower_true_ends_in_unknown(capsys):
    conf = {"phases": [["Dartagnan", "Goblint"]], "answers": [["Dartagnan:false"], ["Goblint:true"]],
            "witnesses": ["Dartagnan"]}
    result, runner = run(conf, "no-data-race", Goblint=Script(0.05, TRUE), Dartagnan=Script(0.1, FALSE))
    lines = printed(capsys)

    assert result == "unknown"
    assert ("deliver", []) in runner.calls
    assert ("CoOpeRace: the verdict false of bracket 1 needs a witness, and no component with an entry for "
            "false wrote one that passes the check: the result is unknown") in lines
    assert not any(line.startswith("CoOpeRace result from:") for line in lines)


V_STAR = {"phases": [["Deagle", "sv-sanitizers", "Dartagnan"]],
          "answers": [["Deagle:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
          "witnesses": ["sv-sanitizers", "Dartagnan"]}


def test_the_unreach_call_example_of_v_star_keeps_dartagnan_for_a_witnessed_true(capsys):
    result, runner = run(V_STAR, "unreach-call",
                         sv_sanitizers=Script(0.05, FALSE, witness=True, subproperty="unreach-call"),
                         Deagle=Script(0.15, TRUE), Dartagnan=Script(0.3, TRUE, witness=True))

    assert result == "true"
    assert runner.stopped == []
    assert ("deliver", ["Dartagnan.witness.yml"]) in runner.calls


def test_the_unreach_call_example_stops_dartagnan_once_deagle_has_given_up(capsys):
    result, runner = run(V_STAR, "unreach-call",
                         sv_sanitizers=Script(0.05, FALSE, witness=True, subproperty="unreach-call"),
                         Deagle=Script(0.15), Dartagnan=Script(FOREVER))

    assert result == "false(unreach-call)"
    assert runner.stopped == ["Dartagnan"]
    assert ("deliver", ["sv-sanitizers.witness.yml"]) in runner.calls


def test_two_sub_properties_in_one_bracket_deliver_the_witness_suppliers(capsys):
    conf = {"phases": [["sv-sanitizers", "Goblint"], ["Deagle"]],
            "answers": [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"]],
            "witnesses": ["Deagle", "sv-sanitizers"]}
    result, runner = run(conf, "valid-memsafety",
                         sv_sanitizers=Script(0.05, FALSE, witness=True, subproperty="valid-free"),
                         Goblint=Script(0.1), Deagle=Script(0.1, FALSE, witness=True, subproperty="valid-deref"))

    assert result == "false(valid-deref)"
    assert runner.started == ["sv-sanitizers", "Goblint", "Deagle"]
    assert ("deliver", ["Deagle.witness.yml"]) in runner.calls
    assert printed(capsys)[-1] == "CoOpeRace result from: Deagle"


def test_a_tool_kept_across_a_phase_boundary_is_started_once(capsys):
    result, runner = run(DATA_RACE, "no-data-race",
                         sv_sanitizers=Script(0.05), Goblint=Script(0.3, TRUE), Dartagnan=Script(0.05))

    assert result == "true"
    assert runner.started == ["sv-sanitizers", "Goblint", "Dartagnan"]
    assert runner.steps["sv-sanitizers"].cpu_time_limit == 30


def test_a_tool_that_waits_is_stopped_at_the_phases_wall_time_limit_and_phase_2_starts(capsys):
    conf = dict(DATA_RACE, phases=[{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 0.2},
                                   ["Goblint", "Dartagnan"]])
    started = time.monotonic()
    result, runner = run(conf, "no-data-race",
                         sv_sanitizers=Script(FOREVER), Goblint=Script(0.6),
                         Dartagnan=Script(0.05, FALSE, witness=True))
    lines = printed(capsys)

    assert result == "false"
    assert runner.started == ["sv-sanitizers", "Goblint", "Dartagnan"]
    assert runner.stopped == ["sv-sanitizers"]
    assert "CoOpeRace: the wall-time limit of phase 1, 0.2 s, has passed: stopping sv-sanitizers" in lines
    assert time.monotonic() - started >= 0.6       # Goblint was not stopped


def test_the_last_phases_wall_time_limit_stops_every_tool(capsys):
    conf = {"phases": [{"tools": ["A", "B"], "wallTimeLimit": 0.2}], "answers": [["A"], ["B"]], "witnesses": []}
    result, runner = run(conf, "no-data-race", A=Script(FOREVER), B=Script(FOREVER))

    assert result == "unknown"
    assert sorted(runner.stopped) == ["A", "B"]


def test_an_answer_without_an_entry_is_printed_and_ignored(capsys):
    conf = {"phases": [["A"]], "answers": [["A:true"]], "witnesses": []}
    result, _ = run(conf, "no-data-race", A=Script(0.01, FALSE, witness=True))

    assert result == "unknown"
    assert "CoOpeRace answer: A false, which has no entry in answers: ignored" in printed(capsys)


# --- execute ------------------------------------------------------------------

def test_execute_prepares_delivers_and_cleans_up_in_order(capsys):
    _, runner = run({"phases": [["A"]], "answers": [["A"]], "witnesses": []}, "no-data-race",
                    A=Script(0.01, TRUE))

    assert runner.calls == ["prepare", ("deliver", []), "cleanup"]


class Defect(Exception):
    pass


def test_an_exception_of_a_component_thread_stops_every_component_and_propagates(capsys):
    loaded = config.load({"phases": [["A", "B"]], "answers": [["A"], ["B"]], "witnesses": []}, {"A", "B"})
    runner = FakeRunner("no-data-race", {"A": Script(FOREVER), "B": Script(0.05)})
    run_step = runner.run_step

    def failing(step, group):
        if step.component == "B":
            time.sleep(0.05)
            raise Defect("a defect of the runner")
        return run_step(step, group)

    runner.run_step = failing
    group = ComponentGroup()

    with pytest.raises(Defect):
        strategy.execute(loaded, runner, group)

    assert runner.stopped == ["A"]
    assert runner.calls == ["prepare", "cleanup"]
    assert group.stopped


# --- the criterion, through the threads ---------------------------------------

# Seconds of a simulated second of test_verdicts.random_case: components end
# within 0.1 s, and every wall-time limit is at least 0.3 s.
DURATION_SCALE = 0.01
LIMIT_SCALE = 0.03


def scaled(case):
    """The random_case `case` with its times scaled to real seconds."""
    loaded, property_name, behaviours = case
    phases = tuple(dataclasses.replace(phase, wall_time_limit=None if phase.wall_time_limit is None
                                       else phase.wall_time_limit * LIMIT_SCALE)
                   for phase in loaded.phases)
    scripts = {}
    for name, behaviour in behaviours.items():
        answer = behaviour.answer
        seconds = None if behaviour.duration is None else behaviour.duration * DURATION_SCALE
        scripts[name] = (Script(seconds) if answer is None
                         else Script(seconds, answer.verdict, answer.witness, answer.subproperty))
    return dataclasses.replace(loaded, phases=phases), property_name, scripts


@pytest.mark.parametrize("seed", range(4))
def test_the_threaded_run_prints_what_running_every_tool_to_the_end_prints(seed, capsys):
    rng = random.Random(1000 + seed)
    for _ in range(10):
        case = random_case(rng)
        loaded, property_name, scripts = scaled(case)
        runner = FakeRunner(property_name, scripts)
        expected = reference(case[0], property_name, {name: b.answer for name, b in case[2].items()})

        result = strategy.execute(loaded, runner, ComponentGroup())

        (delivered,) = [call[1] for call in runner.calls if call[0] == "deliver"]
        verdict, supplier, subproperty = expected
        if verdict == "contradiction":
            assert result.startswith("ERROR (contradiction: "), case
        elif verdict == FALSE and property_name != "no-data-race":
            assert result == f"false({subproperty})", case
        else:
            assert result == verdict, case
        assert delivered == ([] if supplier is None else [f"{supplier}.witness.yml"]), case
        assert len(runner.started) == len(set(runner.started))
        capsys.readouterr()

