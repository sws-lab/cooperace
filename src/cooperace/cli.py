import argparse
import json
import os
import traceback

from . import config, strategy
from .components import DATA_MODELS, ComponentRunner, remove_old_witness_files


def main():
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
        with open("conf/svcomp26.json") as file:
            conf = json.load(file)

    runner = ComponentRunner(abs_path, args.prop, args.arch)


    verdict = run(conf, runner)
    print("CoOpeRace verdict: " + verdict)


def run(conf, runner, group=None):
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
