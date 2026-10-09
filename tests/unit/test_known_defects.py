"""Tests for defects of CoOpeRace found in review (D1, D2 and D4 of the
review's table), each stating the behaviour the fixed code has. D2 runs
run_parallel in a daemon thread so that a hang is a failure and not a stuck
test run."""
import threading
import time

import pytest

from src.cooperace import components, config, strategy
from src.cooperace.config import Step
from src.cooperace.strategy import NO_OUTCOME, Outcome, StopSignal


def test_D1_a_failing_step_does_not_stop_the_later_steps_of_a_sequence(make_runner, group):
    """Expected: run_sequence([failing, true]) treats the failing step as
    having no verdict and returns the second step's `true`."""
    runner = make_runner()
    tree = {"runType": "sequential",
            "tools": [{"Goblint": "all"}, {"Deagle": "all"}]}

    def run_component(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("Could not find executable")
        return Outcome("true", actor.name(), [], "true")

    runner.run_component = run_component
    root = config.load(tree, runner.registry)

    assert strategy.run_sequence(root, group, runner.run_step) == Outcome("true", "Deagle", [], "true")


def test_D2_a_branch_ending_with_systemexit_does_not_hang_run_parallel(make_runner, group):
    """Expected: run_parallel returns once the other branch has reported, here
    NO_OUTCOME because that branch has no verdict. It is run in a daemon
    thread so that the hang is a failure and not a stuck test run."""
    runner = make_runner()
    tree = {"runType": "parallel",
            "tools": [{"Goblint": "all"}, {"Deagle": "all"}]}

    def run_step(step, group):
        if step.component == "Goblint":
            raise SystemExit(3)
        time.sleep(0.3)
        return NO_OUTCOME

    runner.run_step = run_step
    root = config.load(tree, runner.registry)
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(outcome=strategy.run_parallel(root, group, runner.run_step)),
        daemon=True)
    thread.start()
    thread.join(2)

    assert not thread.is_alive(), "run_parallel is still waiting after 2 s"
    assert result["outcome"] == NO_OUTCOME


def test_D4_a_percentage_limit_without_a_cgroup_limit_says_that_no_limit_applies(
        monkeypatch, capsys):
    """Expected: with_resource_limits prints a line starting with
    "Memory limit of Goblint: none" naming the percentage; the command is
    still returned unchanged."""
    monkeypatch.setattr(components, "run_memory_limit", lambda: None)
    command = ["echo", "hello"]

    assert components.with_resource_limits(Step("Goblint", "all", memory_limit="70%"), command) is command
    lines = capsys.readouterr().out.splitlines()
    assert lines == ['Memory limit of Goblint: none (no cgroup memory limit found for "70%")']


def test_D1_a_failing_step_prints_its_block_with_an_error_status(make_runner, group, capsys):
    """A step whose run_component raises prints the component's block in the usual
    protocol, with the exception in the status, and its traceback goes to
    stderr."""
    runner = make_runner()
    tree = {"runType": "sequential", "tools": [{"Goblint": "all"}]}

    def run_component(actor, step, group):
        raise RuntimeError("Could not find executable")

    runner.run_component = run_component
    root = config.load(tree, runner.registry)
    capsys.readouterr()

    assert strategy.run_sequence(root, group, runner.run_step) == NO_OUTCOME
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


def test_D1_a_failing_branch_of_a_parallel_node_does_not_stop_its_sibling(make_runner, group):
    runner = make_runner()
    tree = {"runType": "parallel",
            "tools": [{"Goblint": "all"}, {"Deagle": "all"}]}

    def run_component(actor, step, group):
        if actor.name() == "Goblint":
            raise RuntimeError("Could not find executable")
        return Outcome("false", actor.name(), [], "false")

    runner.run_component = run_component
    root = config.load(tree, runner.registry)

    assert strategy.run_parallel(root, group, runner.run_step) == Outcome("false", "Deagle", [], "false")


def test_D1_a_stop_signal_in_a_step_propagates(make_runner, group):
    runner = make_runner()
    tree = {"runType": "sequential", "tools": [{"Goblint": "all"}, {"Deagle": "all"}]}
    ran = []

    def run_component(actor, step, group):
        ran.append(actor.name())
        raise StopSignal(15)

    runner.run_component = run_component
    root = config.load(tree, runner.registry)

    with pytest.raises(StopSignal):
        strategy.run_sequence(root, group, runner.run_step)
    assert ran == ["Goblint"]
