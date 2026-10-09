"""The conf of CoOpeRace, checked once and turned into a tree of steps per
property.

A conf is a JSON object (conf/README.md describes the format). It is either
one strategy, which is the strategy for the property no-data-race and for no
other, or an object whose only key is "properties", which maps the name of a
property (a key of properties.FORMULAS) to its strategy. A property the conf
gives no strategy for is not checked. A strategy is a JSON object with:

- "runType", "sequential" or "parallel": how the elements of "tools" run;
- "tools", a list whose elements are objects mapping a component's name to the
  verdicts accepted from it ("true", "false" or "all"), or lists. A list nested
  in a sequential list runs in parallel and one nested in a parallel list runs
  sequentially, so the kind alternates with the depth. An object with several
  keys gives one step per key, in its order;
- "memoryLimits", optional: a component's name to a memory limit, a number of
  bytes or a percentage of the run's memory limit ("70%");
- "cpuTimeLimits", optional: a component's name to a CPU-time limit in seconds.

load_strategies turns a conf into a map from property to strategy, and load
turns one strategy into a Sequence or Parallel of Steps. The limits are kept as
the conf gives them; components.py resolves a percentage when the step starts.
"""
from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass

from .properties import FORMULAS, NO_DATA_RACE

# The values a conf may give for the verdicts accepted from a component.
ACCEPTANCES = ("true", "false", "all")


@dataclass(frozen=True)
class Step:
    """One run of a component: `component` is its name in the conf, `accept`
    the verdicts accepted from it ("true", "false" or "all"), and
    `memory_limit` and `cpu_time_limit` its entries in the conf's
    `memoryLimits` and `cpuTimeLimits`, as the conf gives them, or None."""

    component: str
    accept: str
    memory_limit: int | float | str | None = None
    cpu_time_limit: int | float | str | None = None


@dataclass(frozen=True)
class Sequence:
    """Steps and Parallels run one after another until one is accepted."""

    steps: tuple[Node, ...]


@dataclass(frozen=True)
class Parallel:
    """Steps and Sequences run at the same time until one is accepted."""

    steps: tuple[Node, ...]


Node = Step | Sequence | Parallel


class ConfError(ValueError):
    """Raised by load for a conf that CoOpeRace refuses; its message says what
    is wrong with the conf."""


def _entries(tools: object, where: str = "tools") -> list[tuple[str, object]]:
    """The (component name, acceptance) pairs of the conf's `tools` list
    `tools`, in order, nested lists included. `where` names the list in the
    message. Raises ConfError for a `tools` that is not a list or an element
    that is neither an object nor a list."""
    if not isinstance(tools, list):
        raise ConfError(f"the conf's {where} must be a list, not {tools!r}")
    entries = []
    for tool in tools:
        if isinstance(tool, list):
            entries.extend(_entries(tool, "a list nested in tools"))
        elif isinstance(tool, dict):
            entries.extend(tool.items())
        else:
            raise ConfError(f"the conf's tools has an element that is neither an object nor a list: {tool!r}")
    return entries


def load(conf: dict, known: Container[str]) -> Sequence | Parallel:
    """The tree of `conf`, one strategy (load_strategies gives a conf's
    strategies), a Sequence for runType "sequential" and a Parallel
    for "parallel", with every component's acceptance and limits in its Step.

    Raises ConfError (a ValueError), with a message that names the component
    or the key, if the conf is not an object, lacks "runType" or "tools", has
    a `tools` that is not a list of objects and lists, names a component more
    than once anywhere in its `tools` tree, has a key in `memoryLimits` or
    `cpuTimeLimits` that names no component of the tree, names a component
    that is not in `known` (the names of the components CoOpeRace can run),
    gives an acceptance that is not one of ACCEPTANCES, or has a runType that
    is neither "sequential" nor "parallel".

    A repeated component would share one tool-info object between its
    occurrences, and, in a Parallel, one output directory of the component
    between concurrent runs. A limit for a component that is not run would be
    ignored."""
    if not isinstance(conf, dict):
        raise ConfError("the conf must be a JSON object")
    for key in ("runType", "tools"):
        if key not in conf:
            raise ConfError(f"the conf has no {key!r}")
    entries = _entries(conf["tools"])
    names = [name for name, _ in entries]
    for name in names:
        if names.count(name) > 1:
            raise ConfError(f"component {name!r} is named more than once in the conf's tools")
    for limits in ("memoryLimits", "cpuTimeLimits"):
        if not isinstance(conf.get(limits, {}), dict):
            raise ConfError(f"the conf's {limits} must be an object")
        for name in conf.get(limits, {}):
            if name not in names:
                raise ConfError(f"{limits} has a limit for {name!r}, "
                                "which is not a component of the conf's tools")
    run_type = conf["runType"]
    for name, accept in entries:
        if name not in known:
            raise ConfError(f"component {name!r} in the conf's tools is not a component "
                            "CoOpeRace can run")
        if accept not in ACCEPTANCES:
            raise ConfError(f"component {name!r} has acceptance {accept!r}, "
                            f"expected one of {', '.join(ACCEPTANCES)}")
    if run_type == "sequential":
        kind = Sequence
    elif run_type == "parallel":
        kind = Parallel
    else:
        raise ConfError(f"runType is {run_type!r}, expected 'sequential' or 'parallel'")
    return _node(kind, conf["tools"], conf.get("memoryLimits", {}), conf.get("cpuTimeLimits", {}))


def load_strategies(conf: dict, known: Container[str]) -> dict[str, Sequence | Parallel]:
    """The strategies of `conf`, by the name of their property, each loaded
    with load against `known`. A conf with the key "properties" gives the
    strategies its value maps the properties to; any other conf is one
    strategy, which gives {properties.NO_DATA_RACE: load(conf, known)}.

    Raises ConfError if `conf` is not an object, if it has "properties"
    beside another key, if the value of "properties" is not an object, if
    that object has a key that is not a key of properties.FORMULAS, and for
    any strategy that load refuses; the message of the last names the
    property. Every strategy is checked, not only the one a run uses, so that
    whether a conf is refused does not depend on the task's property."""
    if not isinstance(conf, dict):
        raise ConfError("the conf must be a JSON object")
    if "properties" not in conf:
        return {NO_DATA_RACE: load(conf, known)}
    others = [key for key in conf if key != "properties"]
    if others:
        raise ConfError("a conf with 'properties' has no other key, but this one has "
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


def component_names(node: Node) -> list[str]:
    """The names of the components of the Steps under `node`, in the order of
    the conf."""
    if isinstance(node, Step):
        return [node.component]
    return [name for child in node.steps for name in component_names(child)]


def _node(kind, tools, memory_limits, cpu_time_limits):
    """The `kind` (Sequence or Parallel) of the list `tools`, its nested lists
    of the other kind."""
    inner = Parallel if kind is Sequence else Sequence
    steps = []
    for tool in tools:
        if isinstance(tool, list):
            steps.append(_node(inner, tool, memory_limits, cpu_time_limits))
        else:
            for name, accept in tool.items():
                steps.append(Step(name, accept, memory_limits.get(name), cpu_time_limits.get(name)))
    return kind(tuple(steps))
