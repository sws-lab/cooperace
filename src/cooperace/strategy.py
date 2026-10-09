"""The executor: runs a tree of config.py on components and returns the first
accepted verdict.

It knows nothing about components: run_node and its helpers are given the
function that runs one Step (`run_step`), and execute a StepRunner, which
components.ComponentRunner is and the unit tests fake. The ComponentGroup the
components of a step are started in is passed down the tree as a parameter;
run_parallel nests a new one for each Parallel.

A failure of a component is the runner's to report (it returns NO_OUTCOME); an
Exception that reaches execute is a defect of CoOpeRace itself, and execute
propagates it after it has stopped every component and cleaned up, so that
the command line ends without a verdict line.
"""
from __future__ import annotations

import os
import queue
import signal
import threading
import traceback
from collections import namedtuple
from collections.abc import Callable
from typing import Protocol

from .config import Node, Parallel, Sequence, Step
from .processes import ComponentGroup

# What run_node, run_sequence, run_parallel and a StepRunner's run_step
# return: the verdict ("true", "false" or "unknown"), the name of the
# component that gave it (None for "unknown") and that component's witness
# files from this run.
Outcome = namedtuple("Outcome", "verdict component witness_files")
NO_OUTCOME = Outcome("unknown", None, [])

RunStep = Callable[[Step, ComponentGroup], Outcome]


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
        NO_OUTCOME unless its verdict is accepted. It starts nothing in a
        stopped group, and returns NO_OUTCOME for a component whose group was
        stopped while it ran."""

    def deliver(self, witness_files: list[str]) -> None:
        """Delivers the witness files of the Outcome execute returns."""

    def cleanup(self) -> None:
        """Called once after every component has stopped: removes what
        prepare and the steps made."""


def run_node(node: Node, group: ComponentGroup, run_step: RunStep) -> Outcome:
    """Runs the config.Node `node` in the ComponentGroup `group`: a Step with
    `run_step`, a Sequence with run_sequence, a Parallel with run_parallel.
    Returns its Outcome."""
    if isinstance(node, Step):
        return run_step(node, group)
    if isinstance(node, Sequence):
        return run_sequence(node, group, run_step)
    return run_parallel(node, group, run_step)


def run_sequence(sequence: Sequence, group: ComponentGroup, run_step: RunStep) -> Outcome:
    """Runs the steps of the config.Sequence `sequence` one after another in
    the ComponentGroup `group`, and returns the Outcome of the first accepted
    verdict, or NO_OUTCOME if there is none or `group` is stopped first."""
    for node in sequence.steps:
        if group.stopped:
            break
        outcome = run_node(node, group, run_step)

        if outcome.verdict == "true" or outcome.verdict == "false":
            return outcome

    return NO_OUTCOME


def run_parallel(parallel: Parallel, parent: ComponentGroup, run_step: RunStep) -> Outcome:
    """Runs the steps of the config.Parallel `parallel` at the same time,
    each in a thread of its own (a Sequence runs there with run_sequence), in
    a new ComponentGroup nested in the group `parent`. Returns the Outcome of
    the first accepted verdict that a thread reports, or NO_OUTCOME once
    every thread has reported none. Before returning it stops the group,
    which ends the components still running, and joins every thread, so that
    no component of the group runs or prints afterwards."""
    group = ComponentGroup(parent)
    outcomes = queue.Queue()

    def runBranch(node):
        #Every branch puts exactly one outcome, also when it ends with a
        #BaseException (such as SystemExit from a module), which is
        #printed and counts as no verdict; otherwise the wait below would
        #never end. StopSignal is raised in the main thread only.
        outcome = NO_OUTCOME
        try:
            outcome = run_node(node, group, run_step)
        except BaseException:
            traceback.print_exc()
        finally:
            outcomes.put(outcome)

    threads = [threading.Thread(target=runBranch, args=(node,)) for node in parallel.steps]
    for thread in threads:
        thread.start()

    outcome = NO_OUTCOME
    try:
        for _ in threads:
            reported = outcomes.get()
            if reported.verdict == "true" or reported.verdict == "false":
                outcome = reported
                break
    finally:
        group.stop()
        #Also when a StopSignal ends the wait, so that the stopped
        #components have printed their lines before CoOpeRace ends
        for thread in threads:
            thread.join()

    return outcome


def execute(root: Node, runner: StepRunner, group: ComponentGroup | None = None) -> str:
    """Runs the tree `root` with `runner`, in the ComponentGroup `group` (a
    new one if None), and returns its verdict. No component is running when it
    returns. Only the witness files of the Outcome it returns are delivered.
    For a verdict of "true" or "false" it prints "CoOpeRace result from:
    <name>" last, naming the component whose verdict it returns, so that the
    launcher's verdict line follows it directly. An Exception while the tree
    runs (a defect of CoOpeRace; a component's failure is a step without a
    verdict, see components.ComponentRunner.run_step) stops every component,
    removes the work directory and then propagates, so that the caller ends
    CoOpeRace without a verdict.

    If SIGTERM, SIGINT or SIGHUP arrives while it runs (in the main thread),
    it stops every component, prints that it was stopped, and ends CoOpeRace
    with that signal, without a verdict. The handlers are set to SIG_IGN
    before StopSignal is raised, so a second signal cannot interrupt the
    stopping; `queue.get` and `thread.join` in the main thread are
    interrupted by the handler, and every `finally` on the way stops the
    groups it owns."""
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

    verdict = "unknown"
    component = None
    stopped_by = None
    runner.prepare()
    try:
        outcome = run_node(root, group, runner.run_step)
        #Only the witness of the component whose verdict is returned
        runner.deliver(outcome.witness_files)
        verdict = outcome.verdict
        component = outcome.component
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
    if verdict == "true" or verdict == "false":
        print(f"CoOpeRace result from: {component}", flush=True)
    return verdict
