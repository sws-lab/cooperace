# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The executor: runs a config.Strategy on components and returns its verdict.

execute sets the run up and takes it down: it installs the handlers for the
stop signals, calls the StepRunner's prepare, runs the strategy with
run_strategy, delivers the witness of the Outcome, and calls cleanup.
run_strategy, which decides from the strategy's phases, answers and witnesses
which components run and whose verdict and witness count, is not written: it
raises NotImplementedError, so every run ends without a verdict.

It knows nothing about components: it is given a StepRunner, which
components.ComponentRunner is and the unit tests fake.

A failure of a component is the runner's to report (it returns NO_OUTCOME); an
Exception that reaches execute is a defect of CoOpeRace itself, and execute
propagates it after it has stopped every component and cleaned up, so that
the command line ends without a verdict line.
"""
from __future__ import annotations

import os
import signal
import threading
from collections import namedtuple
from typing import Protocol

from .config import Step, Strategy
from .processes import ComponentGroup

# What run_strategy and a StepRunner's run_step return: the verdict ("true",
# "false" or "unknown"), the name of the component that gave it (None for
# "unknown"), that component's witness files from this run, and the result
# that execute returns for it, which the command line prints after
# "CoOpeRace verdict: " (the StepRunner's to choose:
# components.reported_result, for example "false(valid-deref)").
Outcome = namedtuple("Outcome", "verdict component witness_files result")
NO_OUTCOME = Outcome("unknown", None, [], "unknown")

class StopSignal(BaseException):
    """Raised in the main thread by the handler that execute installs for
    SIGTERM, SIGINT and SIGHUP. A BaseException, so that the handlers for
    Exception on the way do not take it for a component's failure."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum


class StepRunner(Protocol):
    """What execute needs from the components."""

    def prepare(self) -> None:
        """Called once before the first step: removes what an earlier run
        left where this run delivers its witness, and makes what the steps
        need."""

    def run_step(self, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the component of `step` in `group` and returns its Outcome:
        NO_OUTCOME unless `step.accept` accepts its verdict. It starts nothing
        in a stopped group, and returns NO_OUTCOME for a component whose group
        was stopped while it ran."""

    def deliver(self, witness_files: list[str]) -> None:
        """Delivers the witness files of the Outcome execute returns."""

    def cleanup(self) -> None:
        """Called once after every component has stopped: removes what
        prepare and the steps made."""


def run_strategy(root: Strategy, group: ComponentGroup, runner: StepRunner) -> Outcome:
    """Runs the config.Strategy `root` with `runner` in the ComponentGroup
    `group` and returns the Outcome whose result execute returns.

    Not written: it raises NotImplementedError before any component starts.
    The executor of the format of phases, answers and witnesses
    (conf/README.md) goes here."""
    raise NotImplementedError("the executor of the strategy format of phases, answers and witnesses "
                              "(strategy.run_strategy) is not written")


def execute(root: Strategy, runner: StepRunner, group: ComponentGroup | None = None) -> str:
    """Runs the config.Strategy `root` with run_strategy and `runner`, in the
    ComponentGroup `group` (a new one if None), and returns the `result` of
    the Outcome run_strategy returns. No component is running when it
    returns. Only the witness files of that Outcome are delivered. For a
    verdict of "true" or "false" it prints "CoOpeRace result from: <name>"
    last, naming the component of the Outcome, so that the launcher's verdict
    line follows it directly. An Exception while the strategy runs (a defect
    of CoOpeRace, and the NotImplementedError of run_strategy; a component's
    failure is a step without a verdict, see
    components.ComponentRunner.run_step) stops every component, removes the
    work directory and then propagates, so that the caller ends CoOpeRace
    without a verdict.

    If SIGTERM, SIGINT or SIGHUP arrives while it runs (in the main thread),
    it stops every component, prints that it was stopped, and ends CoOpeRace
    with that signal, without a verdict. The handlers are set to SIG_IGN
    before StopSignal is raised, so a second signal cannot interrupt the
    stopping; a wait in the main thread is interrupted by the handler, and
    every `finally` on the way stops the groups it owns."""
    if group is None:
        group = ComponentGroup()
    handlers = {}

    def ignoreStopSignals():
        for signum in handlers:
            signal.signal(signum, signal.SIG_IGN)

    def raiseStopSignal(signum, frame):
        #A second signal must not interrupt the stopping of the components
        ignoreStopSignals()
        raise StopSignal(signum)

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            if signal.getsignal(signum) != signal.SIG_IGN:
                handlers[signum] = signal.signal(signum, raiseStopSignal)

    outcome = NO_OUTCOME
    stopped_by = None
    runner.prepare()
    try:
        outcome = run_strategy(root, group, runner)
        #Only the witness of the component whose verdict is returned
        runner.deliver(outcome.witness_files)
    except StopSignal as stop:
        stopped_by = stop.signum
    finally:
        ignoreStopSignals()
        group.stop()
        runner.cleanup()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    if stopped_by is not None:
        print(f"CoOpeRace stopped by signal {stopped_by}", flush=True)
        signal.signal(stopped_by, signal.SIG_DFL)
        os.kill(os.getpid(), stopped_by)
    if outcome.verdict == "true" or outcome.verdict == "false":
        print(f"CoOpeRace result from: {outcome.component}", flush=True)
    return outcome.result
