# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The conf of CoOpeRace, checked once and turned into a Strategy per
property.

A conf is a JSON object whose only key is "properties", which maps the name
of a property (a key of properties.FORMULAS) to its strategy; conf/README.md
describes the format. A property the conf gives no strategy for is not
checked. A strategy is a JSON object with:

- "phases", a list of phases, run in order. A phase is a list of component
  names or an object {"tools": [...], "wallTimeLimit": seconds}. A component
  listed in consecutive phases keeps running across their boundary;
- "answers", a list of brackets ranked by trust, highest first. A bracket is
  a list of entries "Name:true", "Name:false" or "Name" (both verdicts), in no
  particular order;
- "witnesses", a list of component names, the order in which a component is
  asked for the witness of the verdict;
- "cpuTimeLimits", optional: a component's name to a CPU-time limit in seconds;
- "memoryLimits", optional: a component's name to a memory limit, a number of
  bytes or a percentage of the run's memory limit ("70%").

load_strategies turns a conf into a map from property to Strategy, and load
turns one strategy into a Strategy, refusing with ConfError whatever the
README's "What CoOpeRace refuses" lists. The limits are kept as the conf gives
them; components.py resolves a percentage when the component starts.
"""
from __future__ import annotations

import math
import re
from collections.abc import Container, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from .properties import FORMULAS

# The verdicts an entry of "answers" can name.
VERDICTS = ("true", "false")

# The keys of a strategy, and the keys of a phase given as an object.
STRATEGY_KEYS = ("phases", "answers", "witnesses", "cpuTimeLimits", "memoryLimits")
REQUIRED_KEYS = ("phases", "answers", "witnesses")
PHASE_KEYS = ("tools", "wallTimeLimit")

# The keys of the tree format, which load refuses with a message naming the
# format of STRATEGY_KEYS.
TREE_KEYS = ("runType", "tools")

# A memory limit given as a share of the run's memory limit, "70%" or "12.5%".
_PERCENTAGE = re.compile(r"(\d+(\.\d+)?)%")


@dataclass(frozen=True)
class Step:
    """One run of a component, as components.ComponentRunner.run_step takes
    it: `component` is its name in the conf, `accept` the verdicts the runner
    returns as an Outcome ("true", "false" or "all"; Strategy.step gives
    "all"), and `memory_limit` and `cpu_time_limit` its entries in the
    strategy's `memoryLimits` and `cpuTimeLimits`, as the conf gives them, or
    None."""

    component: str
    accept: str
    memory_limit: int | float | str | None = None
    cpu_time_limit: int | float | str | None = None


@dataclass(frozen=True)
class Phase:
    """One element of a strategy's "phases": the names of the components in
    it, in the conf's order, and its wall-time limit in seconds, counted from
    the start of the phase, or None if it has none."""

    tools: tuple[str, ...]
    wall_time_limit: int | float | None = None


class Entry(NamedTuple):
    """One verdict of one component in a bracket of "answers". The conf's
    "Name" is two entries, (Name, "true") and (Name, "false")."""

    tool: str
    verdict: str


@dataclass(frozen=True)
class Strategy:
    """A strategy for one property, as load returns it after its checks.

    `phases` are the phases in order. `answers` are the brackets in order of
    trust, highest first, each the frozenset of its Entries; a (tool,
    verdict) pair is in at most one bracket. `witnesses` are the component
    names of "witnesses" in order. `cpu_time_limits` and `memory_limits` map a
    component's name to its limit as the conf gives it, and name only
    components of `phases`.

    load guarantees that every component of `phases` has at least one Entry,
    that every Entry and every name in `witnesses` names a component of
    `phases`, and that the phases a component is in are consecutive."""

    phases: tuple[Phase, ...]
    answers: tuple[frozenset[Entry], ...]
    witnesses: tuple[str, ...]
    cpu_time_limits: Mapping[str, int | float]
    memory_limits: Mapping[str, int | float | str]

    @property
    def tools(self) -> tuple[str, ...]:
        """The names of the components of the strategy, each once, in the
        order in which they first appear in `phases`."""
        return tuple(dict.fromkeys(name for phase in self.phases for name in phase.tools))

    def phases_of(self, tool: str) -> range:
        """The indexes in `phases` of the phases that `tool` is in, which are
        consecutive; an empty range for a component of no phase."""
        indexes = [index for index, phase in enumerate(self.phases) if tool in phase.tools]
        return range(indexes[0], indexes[-1] + 1) if indexes else range(0)

    def in_phase(self, tool: str, index: int) -> bool:
        """Whether `tool` is in the phase with index `index`; False for an
        index past the last phase or below 0."""
        return 0 <= index < len(self.phases) and tool in self.phases[index].tools

    def bracket_of(self, tool: str, verdict: str) -> int | None:
        """The index in `answers` of the bracket with the Entry (`tool`,
        `verdict`), or None if no bracket has it."""
        for index, bracket in enumerate(self.answers):
            if Entry(tool, verdict) in bracket:
                return index
        return None

    def witness_rank(self, tool: str) -> int | None:
        """The index of `tool` in `witnesses`, or None if it is not there."""
        return self.witnesses.index(tool) if tool in self.witnesses else None

    def step(self, tool: str) -> Step:
        """The Step that runs `tool` with its limits from `cpu_time_limits`
        and `memory_limits`, accepting both verdicts: which verdict counts is
        the executor's to decide from `answers`."""
        return Step(tool, "all", self.memory_limits.get(tool), self.cpu_time_limits.get(tool))


class ConfError(ValueError):
    """Raised by load and load_strategies for a conf that CoOpeRace refuses;
    its message says what is wrong with the conf."""


def _is_positive_number(value: object) -> bool:
    """Whether `value` is a finite JSON number above 0 (an int or float, not
    a bool)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _is_memory_limit(value: object) -> bool:
    """Whether `value` is a memory limit: a positive number of bytes, or a
    percentage above 0 and at most 100 of the run's memory limit."""
    if isinstance(value, str):
        match = _PERCENTAGE.fullmatch(value)
        return match is not None and 0 < float(match.group(1)) <= 100
    return _is_positive_number(value)


def _names(value: object, where: str) -> tuple[str, ...]:
    """`value` as a tuple of component names, checked to be a list of strings
    with no name twice. `where` names the list in the message."""
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ConfError(f"{where} must be a list of component names, not {value!r}")
    for name in value:
        if value.count(name) > 1:
            raise ConfError(f"{where} names {name!r} more than once")
    return tuple(value)


def _phase(value: object, number: int) -> Phase:
    """Phase `number` (counting from 1) of "phases", given as `value`."""
    where = f"phase {number}"
    if isinstance(value, dict):
        others = [key for key in value if key not in PHASE_KEYS]
        if others:
            raise ConfError(f"{where} has the key {others[0]!r}; a phase object has only "
                            + " and ".join(repr(key) for key in PHASE_KEYS))
        if "tools" not in value:
            raise ConfError(f"{where} has no 'tools'")
        limit = value.get("wallTimeLimit")
        if "wallTimeLimit" in value and not _is_positive_number(limit):
            raise ConfError(f"{where} has the wallTimeLimit {limit!r}, which is not a positive number of seconds")
        tools = _names(value["tools"], f"the tools of {where}")
    elif isinstance(value, list):
        limit = None
        tools = _names(value, where)
    else:
        raise ConfError(f"{where} must be a list of component names or an object with 'tools', not {value!r}")
    if not tools:
        raise ConfError(f"{where} has no component")
    return Phase(tools, limit)


def _entries(text: object, number: int) -> list[Entry]:
    """The Entries of the entry `text` in bracket `number` (counting from 1)
    of "answers": one for "Name:true" and "Name:false", two for "Name"."""
    if not isinstance(text, str) or not text:
        raise ConfError(f"bracket {number} of answers has the entry {text!r}, "
                        "expected 'Name:true', 'Name:false' or 'Name'")
    name, colon, verdict = text.rpartition(":")
    if not colon:
        return [Entry(text, verdict) for verdict in VERDICTS]
    if verdict not in VERDICTS or not name:
        raise ConfError(f"bracket {number} of answers has the entry {text!r}, "
                        "expected 'Name:true', 'Name:false' or 'Name'")
    return [Entry(name, verdict)]


def _answers(value: object) -> tuple[frozenset[Entry], ...]:
    """The brackets of "answers", given as `value`, refusing an empty bracket
    and a (component, verdict) pair that is in more than one entry."""
    if not isinstance(value, list):
        raise ConfError(f"answers must be a list of brackets, not {value!r}")
    brackets = []
    seen = {}
    for number, bracket in enumerate(value, start=1):
        if not isinstance(bracket, list) or not bracket:
            raise ConfError(f"bracket {number} of answers must be a non-empty list of entries, not {bracket!r}")
        entries = []
        for text in bracket:
            for entry in _entries(text, number):
                if entry in seen:
                    where = "again in bracket" if seen[entry] == number else f"in bracket {seen[entry]} and in bracket"
                    raise ConfError(f"answers has an entry for {entry.tool}:{entry.verdict} {where} {number}; "
                                    "a verdict of a component is in one bracket")
                seen[entry] = number
                entries.append(entry)
        brackets.append(frozenset(entries))
    return tuple(brackets)


def _limits(strategy: dict, key: str, tools: tuple[str, ...]) -> dict:
    """The map `key` ("cpuTimeLimits" or "memoryLimits") of `strategy`, {} if
    it has none, refusing a key that is not a component of `tools` and a
    value that is not a limit."""
    limits = strategy.get(key, {})
    if not isinstance(limits, dict):
        raise ConfError(f"the strategy's {key} must be an object")
    for name, value in limits.items():
        if name not in tools:
            raise ConfError(f"{key} has a limit for {name!r}, which is in no phase")
        if key == "memoryLimits" and not _is_memory_limit(value):
            raise ConfError(f"memoryLimits gives {name!r} the limit {value!r}, which is neither a positive "
                            "number of bytes nor a percentage above 0 and at most 100, such as \"70%\"")
        if key == "cpuTimeLimits" and not _is_positive_number(value):
            raise ConfError(f"cpuTimeLimits gives {name!r} the limit {value!r}, "
                            "which is not a positive number of seconds")
    return dict(limits)


def _refuse_tree_format(conf: dict, what: str) -> None:
    """Raises ConfError naming the format of STRATEGY_KEYS if `conf` (`what`:
    "the conf" or "the strategy") has a key of the tree format."""
    for key in TREE_KEYS:
        if key in conf:
            raise ConfError(f"{what} has {key!r}, a key of the tree format, which CoOpeRace does not read; "
                            "a strategy has 'phases', 'answers' and 'witnesses', as conf/README.md says")


def load(strategy: dict, known: Container[str]) -> Strategy:
    """The Strategy of `strategy`, one strategy of a conf (load_strategies
    gives a conf's strategies), checked against `known`, the names of the
    components CoOpeRace can run.

    Raises ConfError (a ValueError), with a message that names the phase, the
    bracket, the component or the key, if `strategy` is not an object; has
    "runType" or "tools" (the tree format); has a key that is not one of
    STRATEGY_KEYS, or lacks one of REQUIRED_KEYS; has a phase that is neither
    a list of names nor an object, a phase object with a key other than
    "tools" and "wallTimeLimit" or without "tools", an empty phase, a name
    twice in one phase, or a wallTimeLimit that is not a positive number;
    names in a phase a component that is not in `known`; has a component in
    two phases that are not consecutive; has an empty bracket, an entry that
    is not "Name:true", "Name:false" or "Name", or one (component, verdict)
    pair in two entries; has an entry naming a component of no phase, or a
    component of a phase without an entry; has a name in "witnesses" twice or
    without an entry; or has a key of "cpuTimeLimits" or "memoryLimits" that
    is in no phase, or a value there that is not a positive number (or, in
    "memoryLimits", a percentage above 0 and at most 100)."""
    if not isinstance(strategy, dict):
        raise ConfError("the strategy must be a JSON object")
    _refuse_tree_format(strategy, "the strategy")
    for key in strategy:
        if key not in STRATEGY_KEYS:
            raise ConfError(f"the strategy has the key {key!r}, expected only "
                            + ", ".join(repr(key) for key in STRATEGY_KEYS))
    for key in REQUIRED_KEYS:
        if key not in strategy:
            raise ConfError(f"the strategy has no {key!r}")

    if not isinstance(strategy["phases"], list) or not strategy["phases"]:
        raise ConfError(f"phases must be a non-empty list of phases, not {strategy['phases']!r}")
    phases = tuple(_phase(value, number) for number, value in enumerate(strategy["phases"], start=1))
    tools = tuple(dict.fromkeys(name for phase in phases for name in phase.tools))
    for name in tools:
        if name not in known:
            raise ConfError(f"component {name!r} in phases is not a component CoOpeRace can run")
        numbers = [number for number, phase in enumerate(phases, start=1) if name in phase.tools]
        if numbers[-1] - numbers[0] + 1 != len(numbers):
            raise ConfError(f"component {name!r} is in phases {', '.join(map(str, numbers))}, which are not "
                            "consecutive; a component runs in one stretch of consecutive phases")

    answers = _answers(strategy["answers"])
    with_entry = {entry.tool for bracket in answers for entry in bracket}
    for name in sorted(with_entry):
        if name not in tools:
            raise ConfError(f"answers has an entry for {name!r}, which is in no phase")
    for name in tools:
        if name not in with_entry:
            raise ConfError(f"component {name!r} is in a phase but has no entry in answers")

    witnesses = _names(strategy["witnesses"], "witnesses")
    for name in witnesses:
        if name not in with_entry:
            raise ConfError(f"witnesses names {name!r}, which has no entry in answers")

    return Strategy(phases, answers, witnesses,
                    _limits(strategy, "cpuTimeLimits", tools), _limits(strategy, "memoryLimits", tools))


def load_strategies(conf: dict, known: Container[str]) -> dict[str, Strategy]:
    """The strategies of `conf`, by the name of their property, each loaded
    with load against `known`.

    Raises ConfError if `conf` is not an object, has a key of the tree format,
    has no "properties" or another key beside it, if the value of
    "properties" is not an object, if that object has a key that is not a key
    of properties.FORMULAS, and for any strategy that load refuses; the
    message of the last names the property. Every strategy is checked, not
    only the one a run uses, so that whether a conf is refused does not depend
    on the task's property."""
    if not isinstance(conf, dict):
        raise ConfError("the conf must be a JSON object")
    _refuse_tree_format(conf, "the conf")
    if "properties" not in conf:
        raise ConfError("the conf has no 'properties', the object that maps each property to its strategy")
    others = [key for key in conf if key != "properties"]
    if others:
        raise ConfError("a conf has no key but 'properties', but this one has "
                        + ", ".join(repr(key) for key in others))
    strategies = conf["properties"]
    if not isinstance(strategies, dict):
        raise ConfError("the conf's properties must be an object")
    loaded = {}
    for name, strategy in strategies.items():
        if name not in FORMULAS:
            raise ConfError(f"the conf's properties has a strategy for {name!r}, "
                            f"expected one of {', '.join(FORMULAS)}")
        try:
            loaded[name] = load(strategy, known)
        except ConfError as error:
            raise ConfError(f"in the strategy for {name}: {error}") from error
    return loaded
