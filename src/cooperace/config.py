"""The conf of CoOpeRace, checked once and turned into a tree of steps.

A conf is a JSON object (conf/README.md describes the format):

- "runType", "sequential" or "parallel": how the elements of "tools" run;
- "tools", a list whose elements are objects mapping a component's name to the
  verdicts accepted from it ("true", "false" or "all"), or lists. A list nested
  in a sequential list runs in parallel and one nested in a parallel list runs
  sequentially, so the kind alternates with the depth. An object with several
  keys gives one step per key, in its order;
- "memoryLimits", optional: a component's name to a memory limit, a number of
  bytes or a percentage of the run's memory limit ("70%");
- "cpuTimeLimits", optional: a component's name to a CPU-time limit in seconds.

load turns a conf into a Sequence or Parallel of Steps. The limits are kept as
the conf gives them; components.py resolves a percentage when the step starts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Container, Union

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


Node = Union[Step, Sequence, Parallel]


class RunTypeError(ValueError):
    """Raised by load for a conf whose runType is neither "sequential" nor
    "parallel". A ValueError like the other errors of load, but a class of its
    own, because CoOpeRace reports it differently: as a run that went wrong,
    with verdict unknown, and not as an error that ends CoOpeRace."""


def load(conf: dict, known: Container[str]) -> Sequence | Parallel:
    """The tree of `conf`, a Sequence for runType "sequential" and a Parallel
    for "parallel", with every component's acceptance and limits in its Step.

    Raises ValueError, naming the component or the key, if the conf names a
    component more than once anywhere in its `tools` tree, has a key in
    `memoryLimits` or `cpuTimeLimits` that names no component of the tree,
    names a component that is not in `known` (the names of the components
    CoOpeRace can run), or gives an acceptance that is not one of ACCEPTANCES;
    then RunTypeError for a runType that is neither "sequential" nor
    "parallel". A missing "tools" or "runType" raises KeyError.

    A repeated component would share one tool-info object between its
    occurrences, and, in a Parallel, one output directory of the component
    between concurrent runs. A limit for a component that is not run would be
    ignored."""
    entries = []

    def collect(tools):
        for tool in tools:
            if isinstance(tool, list):
                collect(tool)
            else:
                entries.extend(tool.items())

    collect(conf["tools"])
    names = [name for name, _ in entries]
    for name in names:
        if names.count(name) > 1:
            raise ValueError(f"component {name!r} is named more than once in the conf's tools")
    for limits in ("memoryLimits", "cpuTimeLimits"):
        for name in conf.get(limits, {}):
            if name not in names:
                raise ValueError(f"{limits} has a limit for {name!r}, "
                                 "which is not a component of the conf's tools")
    run_type = conf["runType"]
    for name, accept in entries:
        if name not in known:
            raise ValueError(f"component {name!r} in the conf's tools is not a component "
                             "CoOpeRace can run")
        if accept not in ACCEPTANCES:
            raise ValueError(f"component {name!r} has acceptance {accept!r}, "
                             f"expected one of {', '.join(ACCEPTANCES)}")
    if run_type == "sequential":
        kind = Sequence
    elif run_type == "parallel":
        kind = Parallel
    else:
        raise RunTypeError("execution type in conf file is incorrect. Must be 'parallel' or 'sequential'")
    return _node(kind, conf["tools"], conf.get("memoryLimits", {}), conf.get("cpuTimeLimits", {}))


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
