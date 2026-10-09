"""The strategy: runSequential, runParallel and their nesting, with fake
components. Each fake runs a real `sleep` child through actorResult, so that
stopping a loser really ends a process; only runActor is replaced."""
import threading
import time

import pytest

from src.cooperace.components import NO_OUTCOME, Outcome

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
    letter, sleeps `seconds` in a child process and then reports `verdict`
    (with `witness_files` for an accepted one), as runActor does. `started`
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

    def runActor(self, actor):
        letter, seconds, verdict, *witness = self.components[actor.name()]
        result = self.coop.actorResult(["sleep", str(seconds)], str(self.tmp_path))
        if result.returncode is None:
            return "unknown"
        with self.lock:
            self.started.append(letter)
            self.returncodes[letter] = result.returncode
        if self.coop.currentGroup().stopped:
            return "unknown"
        if verdict in ("true", "false"):
            self.coop.local.witness_files = witness[0] if witness else []
        return verdict


def run(coop):
    """What execute does with the conf, without the witness delivery."""
    run_type, tools = coop.parseConf()
    return coop.runSequential(tools) if run_type == "sequential" else coop.runParallel(tools)


# --- runSequential ----------------------------------------------------------

def test_sequence_stops_at_the_first_accepted_verdict(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b", "c"))
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.1, "true"), c=(0.1, "false"))

    outcome = run(coop)

    assert outcome == Outcome("true", "Deagle", [])
    assert script.started == ["a", "b"]


def test_sequence_takes_a_false_verdict_too(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b"))
    Script(coop, tmp_path, a=(0.1, "false"), b=(0.1, "true"))

    assert run(coop).verdict == "false"


def test_sequence_without_an_accepted_verdict_gives_no_outcome(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b"))
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.1, "unknown"))

    assert run(coop) == NO_OUTCOME
    assert script.started == ["a", "b"]


def test_empty_sequence_gives_no_outcome(make_coop):
    assert run(make_coop(conf("sequential"))) == NO_OUTCOME


def test_sequence_in_a_stopped_group_starts_nothing(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b"))
    script = Script(coop, tmp_path, a=(0.1, "true"), b=(0.1, "true"))
    coop.root_group.stop()

    assert run(coop) == NO_OUTCOME
    assert script.started == []


def test_outcome_carries_the_witness_files_of_the_returned_component(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b", "c"))
    Script(coop, tmp_path,
           a=(0.1, "unknown", ["/run/a/witness.yml"]),
           b=(0.1, "true", ["/run/b/witness.graphml"]),
           c=(0.1, "false", ["/run/c/witness.graphml"]))

    outcome = run(coop)

    assert outcome.component == "Deagle"
    assert outcome.witness_files == ["/run/b/witness.graphml"]


def test_runOne_gives_no_witness_files_for_an_unknown_verdict(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a"))
    Script(coop, tmp_path, a=(0.1, "unknown", ["/run/a/witness.yml"]))
    _, (actor,) = coop.parseConf()

    assert coop.runOne(actor) == NO_OUTCOME


def test_runOne_forgets_the_witness_files_of_the_previous_component(make_coop, tmp_path):
    coop = make_coop(conf("sequential", "a", "b"))
    Script(coop, tmp_path, a=(0.1, "true", ["/run/a/witness.yml"]), b=(0.1, "true"))
    _, (first, second) = coop.parseConf()

    assert coop.runOne(first).witness_files == ["/run/a/witness.yml"]
    assert coop.runOne(second).witness_files == []


# --- runParallel ------------------------------------------------------------

def test_parallel_returns_the_first_accepted_verdict_and_stops_the_loser(make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b"))
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.3, "false", ["/run/b/w.yml"]))

    started = time.monotonic()
    outcome = run(coop)
    elapsed = time.monotonic() - started

    assert outcome == Outcome("false", "Deagle", ["/run/b/w.yml"])
    assert elapsed < 10  # the 60 s sleep was ended, not waited for
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] == 0


def test_parallel_returns_the_earlier_verdict_not_the_earlier_listed(make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b", "c"))
    Script(coop, tmp_path, a=(1.5, "true"), b=(LONG, "false"), c=(0.2, "true"))

    outcome = run(coop)

    assert outcome.component == "Dartagnan"


def test_parallel_skips_an_unknown_verdict_and_waits_for_the_next(make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b"))
    Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.6, "true"))

    assert run(coop).component == "Deagle"


def test_parallel_without_an_accepted_verdict_waits_for_all_and_gives_no_outcome(
        make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b"))
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.5, "unknown"))

    assert run(coop) == NO_OUTCOME
    assert script.returncodes == {"a": 0, "b": 0}


def test_parallel_leaves_no_component_running_when_it_returns(make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b", "c"))
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.2, "true"), c=(LONG, "false"))

    run(coop)

    # Every child has been reaped by the thread that waited for it.
    assert sorted(script.returncodes) == ["a", "b", "c"]
    assert coop.root_group.subgroups[0].processes == set()


def test_exception_in_a_parallel_branch_leaves_the_other_branches(make_coop, tmp_path):
    coop = make_coop(conf("parallel", "a", "b"))
    script = Script(coop, tmp_path, b=(0.3, "true"))
    fake_runActor = coop.runActor

    def runActor(actor):
        if actor.name() == "Goblint":
            raise RuntimeError("setup failed")
        return fake_runActor(actor)

    coop.runActor = runActor

    assert run(coop).component == "Deagle"
    assert script.started == ["b"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_parallel_returns_the_verdict_when_another_branch_ends_with_systemexit(make_coop):
    """A BaseException in one branch does not matter while another branch
    reports an accepted verdict: runParallel takes that one at once. (When no
    other branch reports one it hangs; test_known_defects.py, D2.)"""
    coop = make_coop(conf("parallel", "a", "b"))

    def runOne(actor):
        if actor.name() == "Goblint":
            raise SystemExit(3)
        return Outcome("true", actor.name(), [])

    coop.runOne = runOne

    assert run(coop).verdict == "true"


# --- nesting ----------------------------------------------------------------

def test_parallel_group_inside_a_sequence_returns_its_first_verdict(make_coop, tmp_path):
    coop = make_coop(conf("sequential", ["a", "b"], "c"))
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(0.2, "false"), c=(0.1, "true"))

    outcome = run(coop)

    assert outcome.component == "Deagle"
    assert "c" not in script.started
    assert script.returncodes["a"] in (-15, -9)


def test_sequence_goes_on_when_the_parallel_group_inside_it_has_no_verdict(make_coop, tmp_path):
    coop = make_coop(conf("sequential", ["a", "b"], "c"))
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.2, "unknown"), c=(0.1, "true"))

    assert run(coop).component == "Dartagnan"
    assert script.started.index("c") == 2


def test_sequence_inside_a_parallel_group_runs_its_steps_in_order(make_coop, tmp_path):
    coop = make_coop(conf("parallel", ["a", "b"], "c"))
    script = Script(coop, tmp_path, a=(0.1, "unknown"), b=(0.2, "true"), c=(LONG, "true"))

    outcome = run(coop)

    assert outcome.component == "Deagle"
    # `started` is in the order the children ended: a, then b, whose verdict
    # ends c.
    assert script.started == ["a", "b", "c"]
    assert script.returncodes["c"] in (-15, -9)


def test_stopped_parallel_group_does_not_start_the_later_steps_of_its_sequence(
        make_coop, tmp_path):
    coop = make_coop(conf("parallel", ["a", "b"], "c"))
    script = Script(coop, tmp_path, a=(LONG, "unknown"), b=(0.1, "true"), c=(0.3, "false"))

    outcome = run(coop)

    assert outcome.component == "Dartagnan"
    assert sorted(script.started) == ["a", "c"]


def test_parallel_inside_parallel_is_stopped_with_the_outer_group(make_coop, tmp_path):
    # Outer parallel: a sequence of [parallel a, b], and c. c wins at once.
    coop = make_coop(conf("parallel", [["a", "b"]], "c"))
    script = Script(coop, tmp_path, a=(LONG, "true"), b=(LONG, "true"), c=(0.3, "false"))

    started = time.monotonic()
    outcome = run(coop)

    assert outcome.component == "Dartagnan"
    assert time.monotonic() - started < 10
    assert script.returncodes["a"] in (-15, -9)
    assert script.returncodes["b"] in (-15, -9)


@pytest.mark.parametrize("run_type", ["sequential", "parallel"])
def test_parseConf_returns_the_run_type_and_the_nested_tool_objects(make_coop, run_type):
    coop = make_coop(conf(run_type, "a", ["b", "c"]))

    parsed_type, tools = coop.parseConf()

    assert parsed_type == run_type
    assert tools[0] is coop.tools["Goblint"]
    assert tools[1] == [coop.tools["Deagle"], coop.tools["Dartagnan"]]
