# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Tests for defects of CoOpeRace found in review (D1 and D4 of the review's
table), each stating the behaviour the fixed code has. D1 is checked here on
components.ComponentRunner.run_step, which runs one component."""
import pytest

from src.cooperace import components
from src.cooperace.config import Step
from src.cooperace.strategy import NO_OUTCOME, StopSignal


def test_D4_a_percentage_limit_without_a_cgroup_limit_says_that_no_limit_applies(
        monkeypatch, capsys):
    """Expected: announce_limits prints a line starting with "Memory limit
    of Goblint: none" naming the percentage, and returns no limit."""
    monkeypatch.setattr(components, "run_memory_limit", lambda: None)

    assert components.announce_limits(Step("Goblint", "all", memory_limit="70%")) == (None, None)
    lines = capsys.readouterr().out.splitlines()
    assert lines == ['Memory limit of Goblint: none (no cgroup memory limit found for "70%")']


def test_D1_a_failing_step_prints_its_block_with_an_error_status(make_runner, group, capsys):
    """A step whose run_component raises prints the component's block in the usual
    protocol, with the exception in the status, gives no verdict, and its
    traceback goes to stderr."""
    runner = make_runner()

    def run_component(actor, step, group):
        raise RuntimeError("Could not find executable")

    runner.run_component = run_component
    capsys.readouterr()

    assert runner.run_step(Step("Goblint", "all"), group) == NO_OUTCOME
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


def test_D1_a_stop_signal_in_a_step_propagates(make_runner, group):
    runner = make_runner()

    def run_component(actor, step, group):
        raise StopSignal(15)

    runner.run_component = run_component

    with pytest.raises(StopSignal):
        runner.run_step(Step("Goblint", "all"), group)
