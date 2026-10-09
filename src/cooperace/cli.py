"""The command line of CoOpeRace (the launcher `cooperace` calls main): reads
the arguments, the property file and the conf, checks them and the
installation, runs the conf's strategy for the property on the task, and
prints "CoOpeRace verdict: <verdict>" as the last line of standard output,
which BenchExec's tool-info module for CoOpeRace reads.

The task, the property file and `--conf` are paths relative to the working
directory; without `--conf`, conf/svcomp26.json of TOOL_DIR is read. The
property is recognized from the formulas in the property file
(properties.recognize), never from a file name or path. A property file with
formulas of no property in properties.FORMULAS, and a property the conf gives
no strategy for (config.load_strategies: a conf that is one strategy has one
for no-data-race only), are refused as a defect of the property below.

Exit status and output. A verdict line is printed only for a run that was
carried out, and then the status is 0; the verdict is "unknown" when no
component gave an accepted verdict, also when a component crashed (its status
is printed in its block and CoOpeRace goes on with the next step). A defect of
the command line, the property, the conf or the installation, and an
exception of CoOpeRace's own while the components run, end CoOpeRace without
a verdict line: each problem is one line "CoOpeRace: error: <problem>" on
stderr (an unexpected exception adds its traceback) and the status is 1;
argparse refuses a command line with status 2. BenchExec's tool-info module
reads a run without a verdict line as ERROR, not UNKNOWN. No component is
started in these cases, except that an exception while they run first stops
every component. After SIGTERM, SIGINT or SIGHUP, CoOpeRace ends by that
signal (strategy.execute)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Container
from pathlib import Path
from typing import NoReturn

from . import TOOL_DIR, config, properties, strategy
from .components import DATA_MODELS, REGISTRY, ComponentRunner, OptionsFileError
from .config import Node
from .processes import ComponentGroup


class SetupError(Exception):
    """A defect of the command line, the property, the conf or the
    installation that ends CoOpeRace without a verdict. `problems` are its
    messages, each printed as one line."""

    def __init__(self, *problems: str):
        super().__init__("; ".join(problems))
        self.problems = problems


def error_exit(*problems: str) -> NoReturn:
    """Prints each of `problems` on stderr as one line "CoOpeRace: error:
    <problem>" and ends CoOpeRace with status 1."""
    for problem in problems:
        print(f"CoOpeRace: error: {problem}", file=sys.stderr)
    sys.exit(1)


def read_property(path: str) -> str:
    """The name of the property (a key of properties.FORMULAS) that the file
    `path` holds, recognized from its formulas by properties.recognize.
    Raises SetupError if the file cannot be read or its formulas are those of
    no property CoOpeRace knows."""
    try:
        with open(path) as file:
            text = file.read()
    except (OSError, UnicodeDecodeError) as error:
        raise SetupError(f"cannot read the property file {path}: {error}") from error
    name = properties.recognize(text)
    if name is None:
        raise SetupError(f"unsupported property in {path}: its formulas are not those of "
                         f"{', '.join(properties.FORMULAS)}")
    return name


def read_conf(path: str) -> dict:
    """The JSON object in the file `path`. Raises SetupError if the file
    cannot be read or is not JSON."""
    try:
        with open(path) as file:
            return json.load(file)
    except (OSError, ValueError) as error:
        raise SetupError(f"cannot read the conf {path}: {error}") from error


def version_string(root: Path) -> str:
    """The version that `--version` prints after "CoOpeRace ": the first line
    of the file VERSION in `root`, the directory of the launcher, which
    scripts/svcomp-dist.sh writes into an archive; else the output of `git
    describe --always --dirty` if `root` is itself a git checkout; else
    "unknown". It reads `root`, not the working directory."""
    try:
        lines = (root / "VERSION").read_text().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    except OSError:
        pass
    if (root / ".git").exists():
        try:
            described = subprocess.run(["git", "-C", str(root), "describe", "--always", "--dirty"],
                                       capture_output=True, text=True, timeout=10)
            if described.returncode == 0 and described.stdout.strip():
                return described.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument('--arch', required=False, choices=DATA_MODELS,
                        help='data model of the task (default: ILP32, as for SV-COMP tasks without a data_model)')
    parser.add_argument('--prop', required=True,
                        help='property file; its property must have a strategy in the conf')
    parser.add_argument('--conf', required=False,
                        help='conf file (default: conf/svcomp26.json of the tool directory)')
    parser.add_argument('--version', action='version', version='CoOpeRace ' + version_string(Path(TOOL_DIR)))
    parser.add_argument('filepath')

    args = parser.parse_args()

    abs_path = os.path.abspath(args.filepath) # to run outside benchexec

    try:
        property_name = read_property(args.prop)
        if not os.path.isfile(abs_path):
            raise SetupError(f"the task {args.filepath} is not a file")
        conf = read_conf(args.conf or os.path.join(TOOL_DIR, "conf", "svcomp26.json"))
        #Before the runner is made, which reports a missing --arch on stderr,
        #so that a refused conf or property gives one line there
        strategy_for(load_strategies(conf, REGISTRY), property_name)
        try:
            runner = ComponentRunner(abs_path, args.prop, args.arch, property_name=property_name)
        except OptionsFileError as error:
            raise SetupError(str(error)) from error
        verdict = run(conf, runner)
    except SetupError as error:
        error_exit(*error.problems)
    except Exception as error:
        #A defect of CoOpeRace; strategy.execute has stopped every component
        print(f"CoOpeRace: error: {type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    print("CoOpeRace verdict: " + verdict)


def load_strategies(conf: dict, known: Container[str]) -> dict[str, Node]:
    """The strategies of the conf `conf` by property, from
    config.load_strategies against the component names `known`. Raises
    SetupError for a conf that config.load_strategies refuses."""
    try:
        return config.load_strategies(conf, known)
    except config.ConfError as error:
        raise SetupError(str(error)) from error


def strategy_for(strategies: dict[str, Node], property_name: str) -> Node:
    """The strategy for the property `property_name` in `strategies` (from
    load_strategies). Raises SetupError if there is none: CoOpeRace then
    checks no other property's strategy on the task, since that strategy's
    components would be asked another question than the task's."""
    if property_name not in strategies:
        raise SetupError(f"unsupported property {property_name}: the conf has no strategy for it")
    return strategies[property_name]


def run(conf: dict, runner: ComponentRunner, group: ComponentGroup | None = None) -> str:
    """Loads the conf `conf` (a dict) with load_strategies against the
    components of `runner` (a components.ComponentRunner), takes its strategy
    for `runner.property_name` (strategy_for), checks that each component of
    every strategy of the conf is installed in the version whose options
    `runner` has (ComponentRunner.installation_problems), and runs the
    strategy taken with strategy.execute in the ComponentGroup `group` (a new
    one if None). Returns the verdict.

    Raises SetupError, before any component starts, for a conf that
    config.load_strategies refuses, for a property the conf has no strategy
    for, and for components that are not installed or not in that version,
    also those of another property's strategy, so that a conf with such a
    component is refused whatever the task's property. An Exception of
    strategy.execute propagates."""
    strategies = load_strategies(conf, runner.registry)
    root = strategy_for(strategies, runner.property_name)
    names = [name for tree in strategies.values() for name in config.component_names(tree)]
    problems = runner.installation_problems(list(dict.fromkeys(names)))
    if problems:
        raise SetupError(*problems)
    return strategy.execute(root, runner, group)
