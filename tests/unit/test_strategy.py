"""The strategy: run_sequence, run_parallel and their nesting, with fake
components. Each fake runs a real `sleep` child through run_in_session, so that
stopping a loser really ends a process; only runActor is replaced."""
import threading
import time

import pytest

from src.cooperace import config, strategy
from src.cooperace.processes import run_in_session
from src.cooperace.strategy import NO_OUTCOME, Outcome

# Letters for the components, so that a conf reads as a tree. All are names
# that Cooperace registers.
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
    """Replaces coop.runActor with a fake that, for the component named by
    letter, sleeps `seconds` in a child process and then returns the Outcome
    of `verdict` (with `witness_files` for an accepted one), as runActor does. `started`
    holds, in the order their children ended, the letters whose child was
    started; `returncodes` the exit status of each child."""

    def __init__(self, coop, tmp_path, **components):
        self.coop = coop
        self.tmp_path = tmp_path
        self.components = {NAMES[letter]: (letter, *spec)
                           for letter, spec in components.items()}
        self.lock = threading.Lock()
        self.started = []
        self.returncodes = {}
        coop.runActor = self.runActor

    def runActor(self, actor, step, group):
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
            return Outcome(verdict, actor.name(), witness[0] if witness else [])
        return NO_OUTCOME


def load(coop, tree):
    return config.load(tree, coop.registry)


def run(coop, tree, group):
    """What execute does with the conf `tree`, without the witness delivery."""
    return strategy.run_node(load(coop, tree), group, coop.run_step)


# --- run_sequence ----------------------------------------------------------

def test_sequence_stops_at_the_first_accepted_verdict(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b", "c")
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.1, "true"), c=(0.1, "false"))

    outcome = run(coop, tree, group)

    assert outcome == Outcome("true", "Deagle", [])
    assert script.started == ["a", "b"]


def test_sequence_takes_a_false_verdict_too(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b")
    Script(coop, tmp_path, a=(0.1, "false"), b=(0.1, "true"))

    assert run(coop, tree, group).verdict == "false"


def test_sequence_without_an_accepted_verdict_gives_no_outcome(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b")
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.1, "unknown"))

    assert run(coop, tree, group) == NO_OUTCOME
    assert script.started == ["a", "b"]


def test_empty_sequence_gives_no_outcome(make_coop, group):
    assert run(make_coop(), conf("sequential"), group) == NO_OUTCOME


def test_sequence_in_a_stopped_group_starts_nothing(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b")
    script = Script(coop, tmp_path, a=(0.1, "true"), b=(0.1, "true"))
    group.stop()

    assert run(coop, tree, group) == NO_OUTCOME
    assert script.started == []


def test_outcome_carries_the_witness_files_of_the_returned_component(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b", "c")
    Script(coop, tmp_path,
           a=(0.1, "unknown", ["/run/a/witness.yml"]),
           b=(0.1, "true", ["/run/b/witness.graphml"]),
           c=(0.1, "false", ["/run/c/witness.graphml"]))

    outcome = run(coop, tree, group)

    assert outcome.component == "Deagle"
    assert outcome.witness_files == ["/run/b/witness.graphml"]


def test_run_step_gives_no_witness_files_for_an_unknown_verdict(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a")
    Script(coop, tmp_path, a=(0.1, "unknown", ["/run/a/witness.yml"]))
    (step,) = load(coop, tree).steps

    assert coop.run_step(step, group) == NO_OUTCOME


def test_run_step_forgets_the_witness_files_of_the_previous_component(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", "a", "b")
    Script(coop, tmp_path, a=(0.1, "true", ["/run/a/witness.yml"]), b=(0.1, "true"))
    first, second = load(coop, tree).steps

    assert coop.run_step(first, group).witness_files == ["/run/a/witness.yml"]
    assert coop.run_step(second, group).witness_files == []


# --- run_parallel ------------------------------------------------------------

def test_parallel_returns_the_first_accepted_verdict_and_stops_the_loser(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b")
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.3, "false", ["/run/b/w.yml"]))

    started = time.monotonic()
    outcome = run(coop, tree, group)
    elapsed = time.monotonic() - started

    assert outcome == Outcome("false", "Deagle", ["/run/b/w.yml"])
    assert elapsed < 10  # the 60 s sleep was ended, not waited for
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] == 0


def test_parallel_returns_the_earlier_verdict_not_the_earlier_listed(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b", "c")
    Script(coop, tmp_path, a=(1.5, "true"), b=(LONG, "false"), c=(0.2, "true"))

    outcome = run(coop, tree, group)

    assert outcome.component == "Dartagnan"


def test_parallel_skips_an_unknown_verdict_and_waits_for_the_next(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b")
    Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.6, "true"))

    assert run(coop, tree, group).component == "Deagle"


def test_parallel_without_an_accepted_verdict_waits_for_all_and_gives_no_outcome(
        make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b")
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.5, "unknown"))

    assert run(coop, tree, group) == NO_OUTCOME
    assert script.returncodes == {"a": 0, "b": 0}


def test_parallel_leaves_no_component_running_when_it_returns(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b", "c")
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.2, "true"), c=(LONG, "false"))

    run(coop, tree, group)

    # Every child has been reaped by the thread that waited for it.
    assert sorted(script.returncodes) == ["a", "b", "c"]
    assert group.subgroups[0].processes == set()


def test_exception_in_a_parallel_branch_leaves_the_other_branches(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", "a", "b")
    script = Script(coop, tmp_path, b=(0.3, "true"))
    fake_runActor = coop.runActor

    def runActor(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("setup failed")
        return fake_runActor(actor, step, group)

    coop.runActor = runActor

    assert run(coop, tree, group).component == "Deagle"
    assert script.started == ["b"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_parallel_returns_the_verdict_when_another_branch_ends_with_systemexit(make_coop, group):
    """A BaseException in one branch does not matter while another branch
    reports an accepted verdict: run_parallel takes that one at once. (When no
    other branch reports one it hangs; test_known_defects.py, D2.)"""
    coop, tree = make_coop(), conf("parallel", "a", "b")

    def run_step(step, group):
        if step.component == "Goblint":
            raise SystemExit(3)
        return Outcome("true", step.component, [])

    coop.run_step = run_step

    assert run(coop, tree, group).verdict == "true"


# --- nesting ----------------------------------------------------------------

def test_parallel_group_inside_a_sequence_returns_its_first_verdict(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", ["a", "b"], "c")
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.2, "false"), c=(0.1, "true"))

    outcome = run(coop, tree, group)

    assert outcome.component == "Deagle"
    assert "c" not in script.started
    assert script.returncodes["a"] in (-15, -9)


def test_sequence_goes_on_when_the_parallel_group_inside_it_has_no_verdict(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("sequential", ["a", "b"], "c")
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.2, "unknown"), c=(0.1, "true"))

    assert run(coop, tree, group).component == "Dartagnan"
    assert script.started.index("c") == 2


def test_sequence_inside_a_parallel_group_runs_its_steps_in_order(make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", ["a", "b"], "c")
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.2, "true"), c=(LONG, "true"))

    outcome = run(coop, tree, group)

    assert outcome.component == "Deagle"
    # `started` is in the order the children ended: a, then b, whose verdict
    # ends c.
    assert script.started == ["a", "b", "c"]
    assert script.returncodes["c"] in (-15, -9)


def test_stopped_parallel_group_does_not_start_the_later_steps_of_its_sequence(
        make_coop, group, tmp_path):
    coop, tree = make_coop(), conf("parallel", ["a", "b"], "c")
    script = Script(coop, tmp_path, a=(LONG, "unknown"), b=(0.1, "true"), c=(0.3, "false"))

    outcome = run(coop, tree, group)

    assert outcome.component == "Dartagnan"
    assert sorted(script.started) == ["a", "c"]


def test_parallel_inside_parallel_is_stopped_with_the_outer_group(make_coop, group, tmp_path):
    # Outer parallel: a sequence of [parallel a, b], and c. c wins at once.
    coop, tree = make_coop(), conf("parallel", [["a", "b"]], "c")
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(LONG, "true"), c=(0.3, "false"))

    started = time.monotonic()
    outcome = run(coop, tree, group)

    assert outcome.component == "Dartagnan"
    assert time.monotonic() - started < 10
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] in (-15, -9)
