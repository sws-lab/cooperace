"""The command line of CoOpeRace (the launcher `cooperace` calls main): reads
the arguments, the property file and the conf, checks them and the
installation, runs the conf on the task, and prints "CoOpeRace verdict:
<verdict>" as the last line of standard output, which BenchExec's tool-info
module for CoOpeRace reads.

The task, the property file and `--conf` are paths relative to the working
directory; without `--conf`, conf/svcomp26.json of TOOL_DIR is read.

Exit status and output. A verdict line is printed only for a run that was
carried out, and then the status is 0; the verdict is "unknown" when no
component gave an accepted verdict, also when a component crashed (its status
is printed in its block and CoOpeRace goes on with the next step). A defect of
the command line, the property, the conf or the installation (a component
the conf names whose executable is not under tools/) ends CoOpeRace without
a verdict line, before any component starts: each problem is one line
"CoOpeRace: error: <problem>" on stderr and the status is 1; argparse
refuses a command line with status 2. BenchExec's tool-info module reads a
run without a verdict line as ERROR, not UNKNOWN. After SIGTERM, SIGINT or SIGHUP, CoOpeRace ends by that
signal (strategy.execute)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import NoReturn

from . import TOOL_DIR, config, strategy
from .components import DATA_MODELS, ComponentRunner
from .processes import ComponentGroup

#The only property CoOpeRace checks: the content of sv-benchmarks' no-data-race.prp
DATA_RACE_PROPERTY = "CHECK( init(main()), LTL(G ! data-race) )"


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


def is_data_race_property(text: str) -> bool:
    """Whether `text`, the content of a property file, is the formula
    DATA_RACE_PROPERTY and nothing else, ignoring white space."""
    return "".join(text.split()) == "".join(DATA_RACE_PROPERTY.split())


def check_property(path: str) -> None:
    """Raises SetupError unless the file `path` can be read and holds the
    no-data-race property. CoOpeRace's strategy answers that property only;
    given another one, the components would be run for the wrong question."""
    try:
        with open(path) as file:
            text = file.read()
    except (OSError, UnicodeDecodeError) as error:
        raise SetupError(f"cannot read the property file {path}: {error}") from error
    if not is_data_race_property(text):
        raise SetupError(f"unsupported property in {path}: CoOpeRace checks only "
                         f"{DATA_RACE_PROPERTY}")


def read_conf(path: str) -> dict:
    """The JSON object in the file `path`. Raises SetupError if the file
    cannot be read or is not JSON."""
    try:
        with open(path) as file:
            return json.load(file)
    except (OSError, ValueError) as error:
        raise SetupError(f"cannot read the conf {path}: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument('--arch', required=False, choices=DATA_MODELS,
                        help='data model of the task (default: ILP32, as for SV-COMP tasks without a data_model)')
    parser.add_argument('--prop', required=True,
                        help='property file; only the no-data-race property is supported')
    parser.add_argument('--conf', required=False,
                        help='conf file (default: conf/svcomp26.json of the tool directory)')
    parser.add_argument('--version', action='version', version='CoOpeRace 0.2')
    parser.add_argument('filepath')

    args = parser.parse_args()

    abs_path = os.path.abspath(args.filepath) # to run outside benchexec

    try:
        check_property(args.prop)
        if not os.path.isfile(abs_path):
            raise SetupError(f"the task {args.filepath} is not a file")
        conf = read_conf(args.conf or os.path.join(TOOL_DIR, "conf", "svcomp26.json"))
        runner = ComponentRunner(abs_path, args.prop, args.arch)
        verdict = run(conf, runner)
    except SetupError as error:
        error_exit(*error.problems)
    print("CoOpeRace verdict: " + verdict)


def run(conf: dict, runner: ComponentRunner, group: ComponentGroup | None = None) -> str:
    """Loads the conf `conf` (a dict) with config.load against the components
    of `runner` (a components.ComponentRunner), checks that the executable of
    each of its components is there (ComponentRunner.missing_executables),
    and runs it with strategy.execute in the ComponentGroup `group` (a new
    one if None). Returns the verdict.

    Raises SetupError, before any component starts, for a conf that
    config.load refuses and for components that are not installed."""
    try:
        root = config.load(conf, runner.registry)
    except config.ConfError as error:
        raise SetupError(str(error)) from error
    problems = runner.missing_executables(config.component_names(root))
    if problems:
        raise SetupError(*problems)
    return strategy.execute(root, runner, group)
