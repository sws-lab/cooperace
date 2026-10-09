"""Tests for defects of CoOpeRace found in review (D1, D2 and D4 of the
review's table), each stating the behaviour the fixed code has. D2 runs
runParallel in a daemon thread so that a hang is a failure and not a stuck
test run."""
import threading
import time

import pytest

from src.cooperace import components, config
from src.cooperace.config import Step
from src.cooperace.strategy import NO_OUTCOME, Outcome, StopSignal


def test_D1_a_failing_step_does_not_stop_the_later_steps_of_a_sequence(make_coop):
    """Expected: runSequential([failing, true]) treats the failing step as
    having no verdict and returns the second step's `true`."""
    coop = make_coop({"runType": "sequential",
                      "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})

    def runActor(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("Could not find executable")
        return Outcome("true", actor.name(), [])

    coop.runActor = runActor
    root = config.load(coop.conf, coop.registry)

    assert coop.runSequential(root, coop.root_group) == Outcome("true", "Deagle", [])


def test_D2_a_branch_ending_with_systemexit_does_not_hang_runParallel(make_coop):
    """Expected: runParallel returns once the other branch has reported, here
    NO_OUTCOME because that branch has no verdict. It is run in a daemon
    thread so that the hang is a failure and not a stuck test run."""
    coop = make_coop({"runType": "parallel",
                      "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})

    def runOne(step, group):
        if step.component == "Goblint":
            raise SystemExit(3)
        time.sleep(0.3)
        return NO_OUTCOME

    coop.runOne = runOne
    root = config.load(coop.conf, coop.registry)
    result = {}
    thread = threading.Thread(target=lambda: result.update(outcome=coop.runParallel(root, coop.root_group)),
                              daemon=True)
    thread.start()
    thread.join(2)

    assert not thread.is_alive(), "runParallel is still waiting after 2 s"
    assert result["outcome"] == NO_OUTCOME


def test_D4_a_percentage_limit_without_a_cgroup_limit_says_that_no_limit_applies(
        make_coop, monkeypatch, capsys):
    """Expected: withResourceLimits prints a line starting with
    "Memory limit of Goblint: none" naming the percentage; the command is
    still returned unchanged."""
    monkeypatch.setattr(components, "run_memory_limit", lambda: None)
    coop = make_coop()
    command = ["echo", "hello"]

    assert coop.withResourceLimits(Step("Goblint", "all", memory_limit="70%"), command) is command
    lines = capsys.readouterr().out.splitlines()
    assert lines == ['Memory limit of Goblint: none (no cgroup memory limit found for "70%")']


def test_D1_a_failing_step_prints_its_block_with_an_error_status(make_coop, capsys):
    """A step whose runActor raises prints the component's block in the usual
    protocol, with the exception in the status, and its traceback goes to
    stderr."""
    coop = make_coop({"runType": "sequential", "tools": [{"Goblint": "all"}]})

    def runActor(actor, step, group):
        raise RuntimeError("Could not find executable")

    coop.runActor = runActor
    root = config.load(coop.conf, coop.registry)
    capsys.readouterr()

    assert coop.runSequential(root, coop.root_group) == NO_OUTCOME
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "---Goblint logs---",
        "",
        "---end of Goblint logs---",
        ("Tool name: Goblint Status: ERROR (RuntimeError: Could not find executable)"
         " Exit code: none, not started"),
        "Tool name: Goblint Result: unknown",
    ]
    assert "RuntimeError: Could not find executable" in captured.err


def test_D1_a_failing_branch_of_a_parallel_node_does_not_stop_its_sibling(make_coop):
    coop = make_coop({"runType": "parallel",
                      "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})

    def runActor(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("Could not find executable")
        return Outcome("false", actor.name(), [])

    coop.runActor = runActor
    root = config.load(coop.conf, coop.registry)

    assert coop.runParallel(root, coop.root_group) == Outcome("false", "Deagle", [])


def test_D1_a_stop_signal_in_a_step_propagates(make_coop):
    coop = make_coop({"runType": "sequential", "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})
    ran = []

    def runActor(actor, step, group):
        ran.append(actor.name())
        raise StopSignal(15)

    coop.runActor = runActor
    root = config.load(coop.conf, coop.registry)

    with pytest.raises(StopSignal):
        coop.runSequential(root, coop.root_group)
    assert ran == ["Goblint"]
