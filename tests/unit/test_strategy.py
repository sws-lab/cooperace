# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The strategy: run_sequence, run_parallel and their nesting, with fake
components. Each fake runs a real `sleep` child through run_in_session, so that
stopping a loser really ends a process; only run_component is replaced."""
import threading
import time

import pytest

from src.cooperace import config, strategy
from src.cooperace.processes import run_in_session
from src.cooperace.strategy import NO_OUTCOME, Outcome

# Letters for the components, so that a conf reads as a tree. All are names
# that the registry has.
NAMES = {
    "a": "Goblint",
    "b": "Deagle",
    "c": "Dartagnan",
    "d": "ULTIMATE Automizer",
    "e": "ULTIMATE GemCutter",
    "f": "ULTIMATE Taipan",
}
LONG = 60  # seconds a component that must be stopped before it ends sleeps


def entry(item):
    """A conf `tools` element for a letter, or for a list of items."""
    if isinstance(item, list):
        return [entry(element) for element in item]
    return {NAMES[item]: "all"}


def conf(run_type, *items):
    return {"runType": run_type, "tools": [entry(item) for item in items]}


class Script:
    """Replaces runner.run_component with a fake that, for the component named by
    letter, sleeps `seconds` in a child process and then returns the Outcome
    of `verdict` (with `witness_files` for an accepted one), as run_component does. `started`
    holds, in the order their children ended, the letters whose child was
    started; `returncodes` the exit status of each child."""

    def __init__(self, runner, tmp_path, **components):
        self.runner = runner
        self.tmp_path = tmp_path
        self.components = {NAMES[letter]: (letter, *spec)
                           for letter, spec in components.items()}
        self.lock = threading.Lock()
        self.started = []
        self.returncodes = {}
        runner.run_component = self.run_component

    def run_component(self, actor, step, group):
        letter, seconds, verdict, *witness = self.components[actor.name()]
        result = run_in_session(["sleep", str(seconds)], str(self.tmp_path), group)
        if result.returncode is None:
            return NO_OUTCOME
        with self.lock:
            self.started.append(letter)
            self.returncodes[letter] = result.returncode
        if group.stopped:
            return NO_OUTCOME
        if verdict in ("true", "false"):
            return Outcome(verdict, actor.name(), witness[0] if witness else [], verdict)
        return NO_OUTCOME


def load(runner, tree):
    return config.load(tree, runner.registry)


def run(runner, tree, group):
    """What execute does with the conf `tree`, without the witness delivery."""
    return strategy.run_node(load(runner, tree), group, runner.run_step)


# --- run_sequence ----------------------------------------------------------

def test_sequence_stops_at_the_first_accepted_verdict(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b", "c")
    script = Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.1, "true"), c=(0.1, "false"))

    outcome = run(runner, tree, group)

    assert outcome == Outcome("true", "Deagle", [], "true")
    assert script.started == ["a", "b"]


def test_sequence_takes_a_false_verdict_too(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b")
    Script(runner, tmp_path, a=(0.1, "false"), b=(0.1, "true"))

    assert run(runner, tree, group).verdict == "false"


def test_sequence_without_an_accepted_verdict_gives_no_outcome(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b")
    script = Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.1, "unknown"))

    assert run(runner, tree, group) == NO_OUTCOME
    assert script.started == ["a", "b"]


def test_empty_sequence_gives_no_outcome(make_runner, group):
    assert run(make_runner(), conf("sequential"), group) == NO_OUTCOME


def test_sequence_in_a_stopped_group_starts_nothing(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b")
    script = Script(runner, tmp_path, a=(0.1, "true"), b=(0.1, "true"))
    group.stop()

    assert run(runner, tree, group) == NO_OUTCOME
    assert script.started == []


def test_outcome_carries_the_witness_files_of_the_returned_component(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b", "c")
    Script(runner, tmp_path,
           a=(0.1, "unknown", ["/run/a/witness.yml"]),
           b=(0.1, "true", ["/run/b/witness.graphml"]),
           c=(0.1, "false", ["/run/c/witness.graphml"]))

    outcome = run(runner, tree, group)

    assert outcome.component == "Deagle"
    assert outcome.witness_files == ["/run/b/witness.graphml"]


def test_run_step_gives_no_witness_files_for_an_unknown_verdict(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a")
    Script(runner, tmp_path, a=(0.1, "unknown", ["/run/a/witness.yml"]))
    (step,) = load(runner, tree).steps

    assert runner.run_step(step, group) == NO_OUTCOME


def test_run_step_forgets_the_witness_files_of_the_previous_component(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", "a", "b")
    Script(runner, tmp_path, a=(0.1, "true", ["/run/a/witness.yml"]), b=(0.1, "true"))
    first, second = load(runner, tree).steps

    assert runner.run_step(first, group).witness_files == ["/run/a/witness.yml"]
    assert runner.run_step(second, group).witness_files == []


# --- run_parallel ------------------------------------------------------------

def test_parallel_returns_the_first_accepted_verdict_and_stops_the_loser(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b")
    script = Script(runner, tmp_path, a=(LONG, "true"), b=(0.3, "false", ["/run/b/w.yml"]))

    started = time.monotonic()
    outcome = run(runner, tree, group)
    elapsed = time.monotonic() - started

    assert outcome == Outcome("false", "Deagle", ["/run/b/w.yml"], "false")
    assert elapsed < 10  # the 60 s sleep was ended, not waited for
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] == 0


def test_parallel_returns_the_earlier_verdict_not_the_earlier_listed(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b", "c")
    Script(runner, tmp_path, a=(1.5, "true"), b=(LONG, "false"), c=(0.2, "true"))

    outcome = run(runner, tree, group)

    assert outcome.component == "Dartagnan"


def test_parallel_skips_an_unknown_verdict_and_waits_for_the_next(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b")
    Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.6, "true"))

    assert run(runner, tree, group).component == "Deagle"


def test_parallel_without_an_accepted_verdict_waits_for_all_and_gives_no_outcome(
        make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b")
    script = Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.5, "unknown"))

    assert run(runner, tree, group) == NO_OUTCOME
    assert script.returncodes == {"a": 0, "b": 0}


def test_parallel_leaves_no_component_running_when_it_returns(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b", "c")
    script = Script(runner, tmp_path, a=(LONG, "true"), b=(0.2, "true"), c=(LONG, "false"))

    run(runner, tree, group)

    # Every child has been reaped by the thread that waited for it.
    assert sorted(script.returncodes) == ["a", "b", "c"]
    assert group.subgroups[0].processes == set()


def test_exception_in_a_parallel_branch_leaves_the_other_branches(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", "a", "b")
    script = Script(runner, tmp_path, b=(0.3, "true"))
    fake_run_component = runner.run_component

    def run_component(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("setup failed")
        return fake_run_component(actor, step, group)

    runner.run_component = run_component

    assert run(runner, tree, group).component == "Deagle"
    assert script.started == ["b"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_parallel_returns_the_verdict_when_another_branch_ends_with_systemexit(make_runner, group):
    """A BaseException in one branch does not matter while another branch
    reports an accepted verdict: run_parallel takes that one at once. (When no
    other branch reports one it hangs; test_known_defects.py, D2.)"""
    runner, tree = make_runner(), conf("parallel", "a", "b")

    def run_step(step, group):
        if step.component == "Goblint":
            raise SystemExit(3)
        return Outcome("true", step.component, [], "true")

    runner.run_step = run_step

    assert run(runner, tree, group).verdict == "true"


# --- nesting ----------------------------------------------------------------

def test_parallel_group_inside_a_sequence_returns_its_first_verdict(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", ["a", "b"], "c")
    script = Script(runner, tmp_path, a=(LONG, "true"), b=(0.2, "false"), c=(0.1, "true"))

    outcome = run(runner, tree, group)

    assert outcome.component == "Deagle"
    assert "c" not in script.started
    assert script.returncodes["a"] in (-15, -9)


def test_sequence_goes_on_when_the_parallel_group_inside_it_has_no_verdict(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("sequential", ["a", "b"], "c")
    script = Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.2, "unknown"), c=(0.1, "true"))

    assert run(runner, tree, group).component == "Dartagnan"
    assert script.started.index("c") == 2


def test_sequence_inside_a_parallel_group_runs_its_steps_in_order(make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", ["a", "b"], "c")
    script = Script(runner, tmp_path, a=(0.1, "unknown"), b=(0.2, "true"), c=(LONG, "true"))

    outcome = run(runner, tree, group)

    assert outcome.component == "Deagle"
    # `started` is in the order the children ended: a, then b, whose verdict
    # ends c.
    assert script.started == ["a", "b", "c"]
    assert script.returncodes["c"] in (-15, -9)


def test_stopped_parallel_group_does_not_start_the_later_steps_of_its_sequence(
        make_runner, group, tmp_path):
    runner, tree = make_runner(), conf("parallel", ["a", "b"], "c")
    script = Script(runner, tmp_path, a=(LONG, "unknown"), b=(0.1, "true"), c=(0.3, "false"))

    outcome = run(runner, tree, group)

    assert outcome.component == "Dartagnan"
    assert sorted(script.started) == ["a", "c"]


def test_parallel_inside_parallel_is_stopped_with_the_outer_group(make_runner, group, tmp_path):
    # Outer parallel: a sequence of [parallel a, b], and c. c wins at once.
    runner, tree = make_runner(), conf("parallel", [["a", "b"]], "c")
    script = Script(runner, tmp_path, a=(LONG, "true"), b=(LONG, "true"), c=(0.3, "false"))

    started = time.monotonic()
    outcome = run(runner, tree, group)

    assert outcome.component == "Dartagnan"
    assert time.monotonic() - started < 10
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] in (-15, -9)
