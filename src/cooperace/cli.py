"""The command line of CoOpeRace (the launcher `cooperace` calls main): reads
the arguments and the conf, runs it on the task, and prints "CoOpeRace
verdict: <verdict>" as the last line of standard output, which BenchExec's
tool-info module for CoOpeRace reads. The exit status is 0 when a verdict
is printed, 2 for a command line argparse refuses, and 1 for an error that
ends CoOpeRace, such as a conf that config.load refuses; after SIGTERM,
SIGINT or SIGHUP, CoOpeRace ends by that signal (strategy.execute).

The task, the property file and `--conf` are paths relative to the working
directory; without `--conf`, conf/svcomp26.json of TOOL_DIR is read."""
from __future__ import annotations

import argparse
import json
import os
import traceback

from . import TOOL_DIR, config, strategy
from .components import DATA_MODELS, ComponentRunner, remove_old_witness_files
from .processes import ComponentGroup


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument('--arch', required=False, choices=DATA_MODELS,
                        help='data model of the task (default: ILP32, as for SV-COMP tasks without a data_model)')
    parser.add_argument('--prop', required=False)
    parser.add_argument('--conf', required=False)
    parser.add_argument('--version', action='version', version='CoOpeRace 0.2')
    parser.add_argument('filepath')

    args = parser.parse_args()

    abs_path = os.path.abspath(args.filepath) # to run outside benchexec

    if args.conf:
        with open(args.conf) as file:
            conf = json.load(file)
    else:
        with open(os.path.join(TOOL_DIR, "conf", "svcomp26.json")) as file:
            conf = json.load(file)

    runner = ComponentRunner(abs_path, args.prop, args.arch)


    verdict = run(conf, runner)
    print("CoOpeRace verdict: " + verdict)


def run(conf: dict, runner: ComponentRunner, group: ComponentGroup | None = None) -> str:
    """Loads the conf `conf` (a dict) with config.load against the components
    of `runner` (a components.ComponentRunner) and runs it with strategy.execute in
    the ComponentGroup `group` (a new one if None). Returns the verdict.

    An error of config.load propagates, except config.RunTypeError: for that
    it removes the old witness files, prints "Error, something went wrong:
    <error>" and the traceback, and returns "unknown", as strategy.execute
    does for an error of the run."""
    try:
        root = config.load(conf, runner.registry)
    except config.RunTypeError as error:
        remove_old_witness_files()
        print("Error, something went wrong:", error)
        traceback.print_exc()
        return "unknown"
    return strategy.execute(root, runner, group)
