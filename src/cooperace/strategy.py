# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The executor: runs a config.Strategy on components and returns its result.

execute sets the run up and takes it down: it installs the handlers for the
stop signals, calls the StepRunner's prepare, runs the strategy with
run_strategy, delivers the witness of the Outcome, and calls cleanup.

run_strategy runs each component in a thread of its own, in a ComponentGroup
of its own nested in the run's, and decides with a verdicts.Schedule which
components start and which are stopped (ComponentGroup.stop, which calls
execution.ExecutorRun.stop for a component under RunExecutor). It enforces
each phase's wall-time limit itself; a component's CPU-time and memory limits
are those of its own run (config.Strategy.step), set when it starts. What the
run prints follows from verdicts.Verdicts.result.

It knows nothing about components: it is given a StepRunner, which
components.ComponentRunner is and the unit tests fake.

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
import time
from collections import namedtuple
from typing import Protocol

from .config import Step, Strategy
from .processes import PRINT_LOCK, ComponentGroup
from .verdicts import CONTRADICTION, FALSE, RUNNING, TRUE, UNKNOWN, Actions, Answer, Schedule, Verdicts
from .witnesses import subproperty_of

# What a StepRunner's run_step and run_strategy return: the verdict ("true",
# "false" or "unknown" from run_step; run_strategy also returns
# verdicts.CONTRADICTION), the name of the component that gave it, as its
# tool-info module names it (None for "unknown"), that component's witness
# files from this run (at most one, which witnesses.passing_witness accepted
# for the verdict), and the result that execute returns for it, which the
# command line prints after "CoOpeRace verdict: " (for run_step the
# StepRunner's to choose: components.reported_result, for example
# "false(valid-deref)"; for a contradiction "ERROR (contradiction: ...)").
Outcome = namedtuple("Outcome", "verdict component witness_files result")
NO_OUTCOME = Outcome("unknown", None, [], "unknown")

# Seconds between two checks of the clock while run_strategy waits for a
# component to end, so that a stop signal is not held up by a long wait.
WAIT_SECONDS = 1.0


class StopSignal(BaseException):
    """Raised in the main thread by the handler that execute installs for
    SIGTERM, SIGINT and SIGHUP. A BaseException, so that the handlers for
    Exception on the way do not take it for a component's failure."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum


class StepRunner(Protocol):
    """What execute needs from the components."""

    # The property of the task, a key of properties.FORMULAS
    property_name: str

    def prepare(self) -> None:
        """Called once before the first step: removes what an earlier run
        left where this run delivers its witness, and makes what the steps
        need."""

    def run_step(self, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the component of `step` in `group` and returns its Outcome:
        NO_OUTCOME unless it gave a verdict that `step.accept` accepts. It
        starts nothing in a stopped group, and returns NO_OUTCOME for a
        component whose group was stopped while it ran. It is called in a
        thread of its own, one per component, at the same time as others."""

    def deliver(self, witness_files: list[str]) -> None:
        """Delivers the witness files of the Outcome execute returns."""

    def cleanup(self) -> None:
        """Called once after every component has stopped: removes what
        prepare and the steps made."""


def say(line: str) -> None:
    """Prints `line`, a line of CoOpeRace's own, holding PRINT_LOCK. No such
    line begins with "CoOpeRace verdict: ", which only the command line
    prints, last."""
    with PRINT_LOCK:
        print(line, flush=True)


def answer_of(outcome: Outcome) -> Answer | None:
    """The verdicts.Answer of a component's Outcome, None for one without a
    verdict: its sub-property is witnesses.subproperty_of its result, and it
    has a witness if its witness_files are not empty."""
    if outcome.verdict not in (TRUE, FALSE):
        return None
    return Answer(outcome.verdict, subproperty_of(outcome.result), bool(outcome.witness_files))


def describe_answer(verdicts: Verdicts, tool: str, outcome: Outcome) -> str:
    """The line run_strategy prints for the Outcome with a verdict of the
    component `tool` (its name in the conf): the verdict and the bracket of
    its entry counting from 1, or that it has no entry and is ignored, and,
    where a witness can count for the verdict (verdicts.witness_counts), the
    witness file or that there is none that passes the check."""
    bracket = verdicts.strategy.bracket_of(tool, outcome.verdict)
    if bracket is None:
        return f"CoOpeRace answer: {tool} {outcome.verdict}, which has no entry in answers: ignored"
    line = f"CoOpeRace answer: {tool} {outcome.verdict}, bracket {bracket + 1}"
    if verdicts.witness_counts(outcome.verdict):
        line += (f", witness {outcome.witness_files[0]}" if outcome.witness_files
                 else ", no witness that passes the check")
    return line


def run_strategy(root: Strategy, group: ComponentGroup, runner: StepRunner) -> Outcome:
    """Runs the config.Strategy `root` with `runner` in the ComponentGroup
    `group` and returns the Outcome whose result execute returns. Every
    component it started has ended, and its block has been printed, when it
    returns or raises.

    Each component runs in a thread of its own that calls runner.run_step
    with root.step(component) in a new ComponentGroup nested in `group`; a
    component is stopped by stopping that group. A verdicts.Schedule of
    `root` on runner.property_name decides which components start and stop:
    when the strategy begins, when a component's run_step returns, and when
    the current phase's wall-time limit has passed on time.monotonic. An
    Outcome that run_step returns after its component was stopped is
    ignored, with a line that says so.

    Prints, besides the blocks the runner prints, one line "CoOpeRace answer:
    ..." for each Outcome with a verdict (describe_answer), one line when a
    phase's wall-time limit stops components, and before returning one line
    for each recorded answer that a higher bracket overrides, and the line
    that says why the result is unknown when the verdict needs a witness that
    no component wrote.

    Returns, for a verdicts.Result of TRUE or FALSE, the Outcome of the
    Result's component, with its witness files only if the Result delivers
    its witness; for CONTRADICTION an Outcome with the verdict CONTRADICTION
    and the result "ERROR (contradiction: <name> <verdict>, ...)", naming
    the disagreeing entries; for UNKNOWN NO_OUTCOME. An exception of a
    component's thread (a defect of the runner) or of this thread, and
    StopSignal, stop `group` and every component in it and propagate."""
    verdicts = Verdicts(root, runner.property_name)
    schedule = Schedule(verdicts)
    events = queue.Queue()
    groups = {}
    threads = []
    outcomes = {}

    def run(tool: str, own: ComponentGroup) -> None:
        try:
            events.put((tool, runner.run_step(root.step(tool), own), None))
        except BaseException as error:  # noqa: B036 - re-raised in run_strategy's thread
            events.put((tool, None, error))

    def act(actions: Actions) -> None:
        if actions.timed_out:
            limit = root.phases[actions.phase].wall_time_limit
            say(f"CoOpeRace: the wall-time limit of phase {actions.phase + 1}, {limit:g} s, has passed: "
                f"stopping {', '.join(actions.timed_out)}")
        for tool in actions.stop:
            groups[tool].stop()
        for tool in actions.start:
            groups[tool] = ComponentGroup(group)
            thread = threading.Thread(target=run, args=(tool, groups[tool]), name=f"CoOpeRace run of {tool}")
            threads.append(thread)
            thread.start()

    try:
        act(schedule.begin(time.monotonic()))
        while not schedule.over:
            deadline = schedule.deadline()
            timeout = WAIT_SECONDS if deadline is None else min(WAIT_SECONDS, max(0.0, deadline - time.monotonic()))
            try:
                tool, outcome, error = events.get(timeout=timeout)
            except queue.Empty:
                act(schedule.time_passed(time.monotonic()))
                continue
            if error is not None:
                raise error
            answer = answer_of(outcome)
            if schedule.states[tool] == RUNNING:
                if answer is not None:
                    outcomes[tool] = outcome
                    say(describe_answer(verdicts, tool, outcome))
                act(schedule.ended(tool, answer, time.monotonic()))
            elif answer is not None:
                say(f"CoOpeRace answer: {tool} {outcome.verdict}, after CoOpeRace stopped it: ignored")
    except BaseException:
        group.stop()
        raise
    finally:
        for thread in threads:
            thread.join()
    return outcome_of(verdicts, outcomes)


def outcome_of(verdicts: Verdicts, outcomes: dict[str, Outcome]) -> Outcome:
    """The Outcome run_strategy returns for the answers in `verdicts`, given
    the Outcome of each component with a verdict in `outcomes`, after
    printing the lines about overridden answers and a missing witness."""
    result = verdicts.result()
    decision = result.decision
    if decision is not None and decision.verdict != CONTRADICTION:
        for tool, answer in verdicts.answers.items():
            bracket = verdicts.strategy.bracket_of(tool, answer.verdict)
            if bracket > decision.bracket and answer.verdict != decision.verdict:
                say(f"CoOpeRace: {tool} {answer.verdict} in bracket {bracket + 1} is overridden by "
                    f"{decision.verdict} in bracket {decision.bracket + 1}")
    if result.verdict == CONTRADICTION:
        names = ", ".join(f"{entry.tool} {entry.verdict}" for entry in result.disagreeing)
        return Outcome(CONTRADICTION, None, [], f"ERROR (contradiction: {names})")
    if result.verdict == UNKNOWN:
        if decision is not None:
            say(f"CoOpeRace: the verdict {decision.verdict} of bracket {decision.bracket + 1} needs a witness, "
                f"and no component with an entry for {decision.verdict} wrote one that passes the check: "
                "the result is unknown")
        return NO_OUTCOME
    outcome = outcomes[result.tool]
    return outcome._replace(witness_files=list(outcome.witness_files) if result.witness else [])


def execute(root: Strategy, runner: StepRunner, group: ComponentGroup | None = None) -> str:
    """Runs the config.Strategy `root` with run_strategy and `runner`, in the
    ComponentGroup `group` (a new one if None), and returns the `result` of
    the Outcome run_strategy returns. No component is running when it
    returns. Only the witness files of that Outcome are delivered. For a
    verdict of "true" or "false" it prints "CoOpeRace result from: <name>"
    last, naming the component of the Outcome, so that the launcher's verdict
    line follows it directly. An Exception while the strategy runs (a defect
    of CoOpeRace; a component's failure is a step without a verdict, see
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
