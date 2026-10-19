# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The verdict state of one run of a config.Strategy, and the rules that say
which components run, without any I/O (conf/README.md describes the rules).

Verdicts holds the components' answers and computes from them the deciding
bracket and its verdict (decision), whose witness is delivered (supplier),
what CoOpeRace prints (result), and whether a component that has not ended
can still change that (keeps_running). Schedule holds which components are
pending, running or ended, and which phase runs; it is told when a component
ends and when a phase's wall-time limit passes, and returns the components to
start and to stop. strategy.run_strategy runs the components as a Schedule
says.

The criterion the rules implement: a component is stopped, or not started
when its phase comes, only if no answer it can give changes the verdict, the
contradiction, the component whose witness is delivered or the sub-property,
whatever the components still running or still to start answer. So the
Result of a run equals that of letting every component run to its end. The
one exception is the list of disagreeing entries of a contradiction:
components of the deciding bracket that have not answered are stopped, and
the list names only those that answered.

Uses only the standard library and config, witnesses."""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import NamedTuple

from .config import Entry, Strategy
from .witnesses import needs_witness, witness_format

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"
# The meet of a bracket in which one component answered true and another false.
CONTRADICTION = "contradiction"


def other(verdict: str) -> str:
    """FALSE for TRUE and TRUE for FALSE."""
    return FALSE if verdict == TRUE else TRUE


class Answer(NamedTuple):
    """The verdict of a component that ended by itself: `verdict` is TRUE or
    FALSE, `subproperty` the sub-property a false names (witnesses.subproperty_of
    of its result, such as "valid-deref"; None for a true and for a false
    without one), and `witness` whether the component wrote a witness that
    witnesses.passing_witness accepts for that verdict and sub-property."""

    verdict: str
    subproperty: str | None = None
    witness: bool = False


class Decision(NamedTuple):
    """The meet of the highest bracket of "answers" whose meet is not
    unknown: the index of that bracket in Strategy.answers and its meet,
    TRUE, FALSE or CONTRADICTION."""

    bracket: int
    verdict: str


@dataclass(frozen=True)
class Result:
    """What a run prints. `verdict` is TRUE, FALSE, UNKNOWN or CONTRADICTION.
    `decision` is the Decision it comes from (None if no component gave a
    verdict with an entry); a verdict that needs a witness and has none is
    UNKNOWN with the Decision of that verdict. `tool` is the component the
    verdict is taken from: the supplier of the witness, else the first
    agreeing component (Verdicts.agreeing); None for UNKNOWN and
    CONTRADICTION. `witness` is whether `tool`'s witness is delivered, and
    `subproperty` the sub-property of `tool`'s answer. `disagreeing` holds,
    for CONTRADICTION, the entries of the deciding bracket that were
    answered, those of true first, each sorted by component name."""

    verdict: str
    decision: Decision | None = None
    tool: str | None = None
    witness: bool = False
    subproperty: str | None = None
    disagreeing: tuple[Entry, ...] = field(default=())


class Verdicts:
    """The answers of the components of `strategy` on a task of the property
    `property_name` (a key of properties.FORMULAS), and what follows from
    them. `answers` maps a component to its Answer, for an answer that
    matches an Entry of that component; record ignores any other."""

    def __init__(self, strategy: Strategy, property_name: str):
        self.strategy = strategy
        self.property_name = property_name
        self.answers: dict[str, Answer] = {}
        #(bracket index, verdict) of each Entry, by component
        self.entries = {tool: {(index, entry.verdict) for index, bracket in enumerate(strategy.answers)
                               for entry in bracket if entry.tool == tool}
                        for tool in strategy.tools}

    def has_entry(self, tool: str, verdict: str) -> bool:
        """Whether `tool` has an Entry for `verdict` in some bracket."""
        return any(entry_verdict == verdict for _, entry_verdict in self.entries[tool])

    def record(self, tool: str, answer: Answer) -> bool:
        """Records the Answer of `tool`, a component that has ended, and
        returns True, if `tool` has an Entry for its verdict; else records
        nothing and returns False."""
        if not self.has_entry(tool, answer.verdict):
            return False
        self.answers[tool] = answer
        return True

    def bracket_meet(self, index: int) -> str:
        """The meet of the answers recorded for the entries of the bracket
        with index `index`: UNKNOWN if none, TRUE or FALSE if all agree,
        CONTRADICTION if both are there."""
        verdicts = {answer.verdict for tool, answer in self.answers.items()
                    if Entry(tool, answer.verdict) in self.strategy.answers[index]}
        if not verdicts:
            return UNKNOWN
        return verdicts.pop() if len(verdicts) == 1 else CONTRADICTION

    def decision(self) -> Decision | None:
        """The Decision of the recorded answers, or None if every bracket's
        meet is unknown."""
        for index in range(len(self.strategy.answers)):
            meet = self.bracket_meet(index)
            if meet != UNKNOWN:
                return Decision(index, meet)
        return None

    def witness_counts(self, verdict: str) -> bool:
        """Whether a witness can count for `verdict` on the property
        (witnesses.witness_format has a format for it), whether or not one
        is required: only then is a witness delivered with `verdict`."""
        return witness_format(self.property_name, verdict) is not None

    def supplier_order(self, verdict: str, bracket: int) -> list[str]:
        """The components that can supply the witness of `verdict` decided in
        the bracket with index `bracket`, in the order in which they are
        asked: those of the strategy's `witnesses` with an Entry for `verdict`
        in some bracket, in that order, then those with the Entry (component,
        `verdict`) in that bracket, by name."""
        listed = [tool for tool in self.strategy.witnesses if self.has_entry(tool, verdict)]
        fallback = sorted(entry.tool for entry in self.strategy.answers[bracket]
                          if entry.verdict == verdict and entry.tool not in listed)
        return listed + fallback

    def supplier(self, verdict: str, bracket: int) -> str | None:
        """The first component of supplier_order(`verdict`, `bracket`) that
        answered `verdict` with a witness, or None."""
        for tool in self.supplier_order(verdict, bracket):
            answer = self.answers.get(tool)
            if answer is not None and answer.verdict == verdict and answer.witness:
                return tool
        return None

    def agreeing(self, verdict: str, bracket: int) -> str | None:
        """The component a verdict without a delivered witness is taken from:
        of the components of supplier_order(`verdict`, `bracket`) that
        answered `verdict`, the first whose answer names a sub-property, else
        the first; None if none answered `verdict`."""
        answered = [tool for tool in self.supplier_order(verdict, bracket)
                    if tool in self.answers and self.answers[tool].verdict == verdict]
        with_subproperty = [tool for tool in answered if self.answers[tool].subproperty is not None]
        return (with_subproperty or answered or [None])[0]

    def result(self) -> Result:
        """The Result of the recorded answers. For a Decision of TRUE or
        FALSE, the witness is that of supplier if a witness can count for
        the verdict (witness_counts) and some component supplies one; else,
        if the verdict needs a witness (witnesses.needs_witness), the result
        is UNKNOWN; else the verdict is taken from the component agreeing
        returns, without a witness."""
        decision = self.decision()
        if decision is None:
            return Result(UNKNOWN)
        index, verdict = decision
        if verdict == CONTRADICTION:
            answered = [Entry(tool, answer.verdict) for tool, answer in self.answers.items()
                        if Entry(tool, answer.verdict) in self.strategy.answers[index]]
            return Result(CONTRADICTION, decision,
                          disagreeing=tuple(sorted(answered, key=lambda entry: (entry.verdict != TRUE, entry.tool))))
        supplier = self.supplier(verdict, index) if self.witness_counts(verdict) else None
        if supplier is not None:
            return Result(verdict, decision, supplier, True, self.answers[supplier].subproperty)
        if needs_witness(self.property_name, verdict):
            return Result(UNKNOWN, decision)
        tool = self.agreeing(verdict, index)
        return Result(verdict, decision, tool, False, self.answers[tool].subproperty)

    def keeps_running(self, tool: str, live: Collection[str]) -> bool:
        """Whether the component `tool`, which has not ended, keeps running,
        or is started when its phase comes, given `live`, the components that
        are running or still to start (`tool` among them).

        True while there is no Decision. Otherwise, with the Decision's
        bracket b and verdict v, the candidates are (v, b) and (u, i) for each
        Entry (component, u) of a component of `live` in a bracket i above b:
        each verdict that could still become the result, with the bracket it
        would be decided in. `tool` keeps running if for some candidate (u,
        i):
        1. it has an Entry in a bracket above i (it could override u);
        2. u is not CONTRADICTION and it has the Entry (`tool`, other(u)) in
           bracket i and no recorded answer (it could contradict u);
        3. u is not CONTRADICTION, a witness can count for u
           (witness_counts), `tool` has an Entry for u, and it comes in
           supplier_order(u, i) before the current supplier, or anywhere in
           it if there is none; for a candidate other than (v, b) the current
           supplier is none (it could supply a witness that comes first)."""
        decision = self.decision()
        if decision is None:
            return True
        candidates = {(decision.verdict, decision.bracket)}
        for other_tool in live:
            candidates |= {(verdict, index) for index, verdict in self.entries[other_tool]
                           if index < decision.bracket}
        own = self.entries[tool]
        for verdict, index in candidates:
            if any(own_index < index for own_index, _ in own):
                return True
            if verdict == CONTRADICTION:
                continue
            if (index, other(verdict)) in own and tool not in self.answers:
                return True
            if self.witness_counts(verdict) and self.has_entry(tool, verdict):
                order = self.supplier_order(verdict, index)
                supplier = self.supplier(verdict, index) if (index, verdict) == decision else None
                before = order if supplier is None else order[:order.index(supplier)]
                if tool in before:
                    return True
        return False


# The states of a component in a Schedule.
PENDING = "pending"    # its phase has not come
RUNNING = "running"    # started, and not ended
ENDED = "ended"        # it ended by itself, or was stopped
SKIPPED = "skipped"    # not started when its phase came: keeps_running was False


class Actions(NamedTuple):
    """What a Schedule asks of strategy.run_strategy: the components to stop
    (`stop`, first) and to start (`start`), each in order. `timed_out` names
    the components of `stop` that are stopped because a phase's wall-time
    limit passed, and `phase` is the index of that phase (None otherwise)."""

    stop: tuple[str, ...] = ()
    start: tuple[str, ...] = ()
    timed_out: tuple[str, ...] = ()
    phase: int | None = None


class Schedule:
    """Which components of `verdicts.strategy` run, phase by phase, by the
    rules of Verdicts.keeps_running. `states` maps each component to PENDING,
    RUNNING, ENDED or SKIPPED; `phase` is the index of the current phase (-1
    before begin) and `phase_started` the time it started, in the seconds of
    the clock the caller passes as `now`.

    The current phase is left for the next when every component of it that
    is not in the next phase has ended or was skipped, or when its wall-time
    limit has passed (time_passed); a component in both keeps running. When
    a phase starts, its PENDING components for which keeps_running holds are
    started and the others are SKIPPED. After every event, each running
    component for which keeps_running no longer holds is stopped. The run is
    over when no component is running, which is after the last phase."""

    def __init__(self, verdicts: Verdicts):
        self.verdicts = verdicts
        self.strategy = verdicts.strategy
        self.states = dict.fromkeys(self.strategy.tools, PENDING)
        self.phase = -1
        self.phase_started: float | None = None

    @property
    def over(self) -> bool:
        """Whether no component is running, after begin."""
        return self.phase >= 0 and RUNNING not in self.states.values()

    def live(self) -> set[str]:
        """The components that are running or still to start."""
        return {tool for tool, state in self.states.items() if state in (RUNNING, PENDING)}

    def deadline(self) -> float | None:
        """The time at which the current phase's wall-time limit passes, or
        None if it has none."""
        limit = self.strategy.phases[self.phase].wall_time_limit if self.phase >= 0 else None
        return None if limit is None else self.phase_started + limit

    def leaving(self) -> list[str]:
        """The components of the current phase that are not in the next one:
        all of them in the last phase."""
        return [tool for tool in self.strategy.phases[self.phase].tools
                if not self.strategy.in_phase(tool, self.phase + 1)]

    def begin(self, now: float) -> Actions:
        """Starts the first phase at the time `now`."""
        return self._settle(now, [], [])

    def ended(self, tool: str, answer: Answer | None, now: float) -> Actions:
        """Records that the running component `tool` ended by itself at the
        time `now`, with the Answer `answer`, or None if it gave no verdict.
        A component that is not RUNNING, one that was stopped, is ignored."""
        if self.states.get(tool) != RUNNING:
            return Actions()
        self.states[tool] = ENDED
        if answer is not None:
            self.verdicts.record(tool, answer)
        return self._settle(now, [], [])

    def time_passed(self, now: float) -> Actions:
        """At the time `now`: if the current phase's deadline has passed,
        stops its running components that are not in the next phase (all of
        them in the last) and goes on to the next phase."""
        deadline = self.deadline()
        if deadline is None or now < deadline:
            return Actions()
        phase = self.phase
        timed_out = [tool for tool in self.leaving() if self.states[tool] == RUNNING]
        for tool in timed_out:
            self.states[tool] = ENDED
        actions = self._settle(now, list(timed_out), [])
        return actions._replace(timed_out=tuple(timed_out), phase=phase)

    def _settle(self, now: float, stop: list[str], start: list[str]) -> Actions:
        """Stops the running components that need not keep running and
        starts the phases that can start, until neither changes anything;
        returns the Actions, beginning with `stop` and `start`."""
        while True:
            self._stop_unneeded(stop)
            if self.phase + 1 >= len(self.strategy.phases) or any(
                    self.states[tool] == RUNNING for tool in (self.leaving() if self.phase >= 0 else ())):
                return Actions(tuple(stop), tuple(start))
            self.phase += 1
            self.phase_started = now
            self._start_phase(start)

    def _stop_unneeded(self, stop: list[str]) -> None:
        """Marks ENDED, and appends to `stop`, each running component for
        which keeps_running does not hold, until none is left."""
        changed = True
        while changed:
            changed = False
            for tool, state in self.states.items():
                if state == RUNNING and not self.verdicts.keeps_running(tool, self.live()):
                    self.states[tool] = ENDED
                    stop.append(tool)
                    changed = True

    def _start_phase(self, start: list[str]) -> None:
        """Of the PENDING components of the current phase, marks SKIPPED each
        for which keeps_running does not hold, until none is left, and
        marks the rest RUNNING and appends them to `start`."""
        pending = [tool for tool in self.strategy.phases[self.phase].tools if self.states[tool] == PENDING]
        changed = True
        while changed:
            changed = False
            for tool in pending:
                if self.states[tool] == PENDING and not self.verdicts.keeps_running(tool, self.live()):
                    self.states[tool] = SKIPPED
                    changed = True
        for tool in pending:
            if self.states[tool] == PENDING:
                self.states[tool] = RUNNING
                start.append(tool)
