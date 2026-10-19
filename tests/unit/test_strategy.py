# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""strategy.execute with a fake StepRunner. run_strategy, the executor of the
format of phases, answers and witnesses, is not written, so execute ends with
its NotImplementedError after setting the run up and taking it down, and no
component starts."""
import pytest

from src.cooperace import config, strategy
from src.cooperace.processes import ComponentGroup

STRATEGY = {"phases": [["Goblint"], ["Dartagnan"]], "answers": [["Goblint:true"], ["Dartagnan"]],
            "witnesses": ["Dartagnan"]}


class FakeRunner:
    """A StepRunner that records the calls execute makes."""

    def __init__(self):
        self.calls = []

    def prepare(self):
        self.calls.append("prepare")

    def run_step(self, step, group):
        self.calls.append(("run_step", step.component))
        raise AssertionError("no component may start")

    def deliver(self, witness_files):
        self.calls.append("deliver")

    def cleanup(self):
        self.calls.append("cleanup")


def test_execute_raises_not_implemented_error_naming_the_missing_executor(runner, capsys):
    root = config.load(STRATEGY, runner.registry)
    fake = FakeRunner()
    group = ComponentGroup()

    with pytest.raises(NotImplementedError, match=r"strategy\.run_strategy"):
        strategy.execute(root, fake, group)

    assert fake.calls == ["prepare", "cleanup"]
    assert group.stopped
    assert "CoOpeRace result from:" not in capsys.readouterr().out
