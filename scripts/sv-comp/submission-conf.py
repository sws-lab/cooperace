#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Checks that a conf is one CoOpeRace may be submitted with.

    scripts/sv-comp/submission-conf.py ARCHIVE_DIR [CONF]

CONF (default: ARCHIVE_DIR/conf/svcomp27.json, the conf the archive runs) is
loaded with config.load_strategies of the code in ARCHIVE_DIR (an unpacked
archive or a checkout), and it is a submission conf if it has a strategy for
each property of properties.FORMULAS and every strategy is in the
first-verdict mode ("firstVerdict": true) without "vetoWindows": the first
accepted verdict is delivered at once, with the deciding component's own
witness. The bracket mode, in which an answer can wait for other components
and a witness can come from another component, is not submitted. Every
component must also be bounded by a limit of its own: a CPU-time limit, or
a wallTimeLimit of the last phase it is in, so that a component that does
not end by itself is not left to the run's CPU-time budget alone.

Prints nothing and exits 0 for a submission conf; prints one line per
problem on stderr and exits 1 otherwise, and exits 2 for a conf that cannot
be read or that the loader refuses. scripts/svcomp-dist.sh runs it on every
archive it builds."""
import json
import sys


def problems(strategies, formulas):
    """The reasons why `strategies` (config.load_strategies' result) is no
    submission conf; [] if it is one. `formulas` are the property names a
    submission conf must cover."""
    found = [f"no strategy for {name}" for name in formulas if name not in strategies]
    for name, strategy in strategies.items():
        if not strategy.first_verdict:
            found.append(f"the strategy for {name} is not in the first-verdict mode (\"firstVerdict\": true)")
        if strategy.veto_windows:
            found.append(f"the strategy for {name} has vetoWindows")
        for tool in strategy.tools:
            last = strategy.phases[strategy.phases_of(tool)[-1]]
            if tool not in strategy.cpu_time_limits and last.wall_time_limit is None:
                found.append(f"in the strategy for {name}, {tool} has neither a CPU-time limit nor a "
                             "wallTimeLimit of the last phase it is in")
    return found


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    archive = argv[1]
    path = argv[2] if len(argv) == 3 else f"{archive}/conf/svcomp27.json"
    sys.path.insert(0, archive)
    from src.cooperace import config
    from src.cooperace.components import REGISTRY
    from src.cooperace.properties import FORMULAS

    try:
        with open(path) as file:
            strategies = config.load_strategies(json.load(file), REGISTRY)
    except (OSError, ValueError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 2
    found = problems(strategies, FORMULAS)
    for problem in found:
        print(f"{path}: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
