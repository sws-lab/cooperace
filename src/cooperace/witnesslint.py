# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""WitnessLint as the gate of a witness, run the way SV-COMP runs it.

The competition lints every witness of a verdict whose validation counts
before any validator: bench-defs' witnesslint-validate-violation-witnesses-v2.xml
and witnesslint-validate-correctness-witnesses-v2.xml run witnesslinter.py
with --expectViolationWitness or --expectCorrectnessWitness, --ignoreSelfLoops,
--expectedWitnessVersion 2, --witness <file> and the task's program file, and
competition-scripts' prepare_tables/export_adjusted_csvs.py turns a correct
verdict whose linter status is "ERROR (invalid witness syntax)", "ERROR
(witness does not exist)", "ERROR (unexpected witness type)" or "ERROR
(unexpected witness version)" into an error of the verifier (0 points).
BenchExec's tool-info module benchexec.tools.witnesslint gives the status;
lint uses its determine_result on the linter's output, so that the statuses
are the competition's.

lint runs the linter of LINTER_DIR (tools/witnesslint, the archive that
download-tools.py installs from tools.txt) in a Python process of its own,
one at a time with the other checks of witnesses.py, its address space
limited to lint_memory_bytes, its CPU time and wall time to lint_seconds (at
most LINT_SECONDS, and no more than the run's CPU-time budget has left). It
returns VALID for "done", INVALID for one of INVALID_STATUSES, and FAILED for
anything else: a linter that cannot start, raises, runs out of time or
memory, or ends in a way the module does not map to one of those (a technical
failure, which the caller answers with witnesses.file_passes instead, so that
a broken linter cannot turn every verdict into unknown).

self_test lints the two witnesses of tests/witnesslint (one valid, one
invalid), within the run's CPU-time budget like any lint, and says whether
the linter gives VALID and INVALID for them."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from typing import NamedTuple

from benchexec import util as butil
from benchexec.tools.template import BaseTool2

from . import TOOL_DIR
from .witnesses import _APART_LOCK, CHECK_MARGIN_SECONDS, DELIVERED_NAME, run_cpu_seconds, run_memory_limit

# The directory of the installed linter, which holds witnesslinter.py.
LINTER_DIR = os.path.join(TOOL_DIR, "tools", "witnesslint")

# The options of the competition's benchmark definitions (bench-defs 96985ac,
# witnesslint-validate-*-witnesses-v2.xml), by the verdict of the witness.
OPTIONS = {"true": ("--expectCorrectnessWitness", "--ignoreSelfLoops", "--expectedWitnessVersion", "2"),
           "false": ("--expectViolationWitness", "--ignoreSelfLoops", "--expectedWitnessVersion", "2")}

# The statuses of benchexec.tools.witnesslint that the competition counts as
# a witness that does not count (PossibleWitnessLintStatus of
# competition-scripts' export_adjusted_csvs.py).
INVALID_STATUSES = ("ERROR (invalid witness syntax)", "ERROR (witness does not exist)",
                    "ERROR (unexpected witness type)", "ERROR (unexpected witness version)")
DONE = "done"

VALID = "valid"
INVALID = "invalid"
FAILED = "failed"

# Seconds of CPU time and of wall time a lint may take at most. The
# competition gives the linter 90 s (violation) and 5 min (correctness);
# in the competition image on one core the components' witnesses take about
# a second (work/cooperace/2026-11-16/first-verdict-mode.md).
LINT_SECONDS = 30.0

# The address space in bytes a lint may take at most, and the share of the
# run's memory limit it may take at most.
LINT_MEMORY_BYTES = 2 << 30
LINT_MEMORY_SHARE = 0.2

# The program that runs the linter: argv is the limit of its address space
# in bytes, its CPU-time limit in seconds, the linter's directory, and the
# linter's own arguments.
_RUN = """
import resource, runpy, sys
limit, seconds, directory = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
for kind, value in ((resource.RLIMIT_AS, limit), (resource.RLIMIT_CPU, seconds)):
    _, hard = resource.getrlimit(kind)
    resource.setrlimit(kind, (value if hard == resource.RLIM_INFINITY else min(value, hard), hard))
sys.argv = [directory + "/witnesslinter.py"] + sys.argv[4:]
sys.path.insert(0, directory)
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class Lint(NamedTuple):
    """The outcome of a lint: VALID, INVALID or FAILED, and the status of
    benchexec.tools.witnesslint or, for FAILED, why."""

    outcome: str
    status: str


def lint_seconds(cpu_time_budget: int | float | None) -> float:
    """LINT_SECONDS, or what the run's budget `cpu_time_budget` has left less
    CHECK_MARGIN_SECONDS if that is less (as witnesses.check_seconds)."""
    used = None if cpu_time_budget is None else run_cpu_seconds()
    if used is None:
        return LINT_SECONDS
    return min(LINT_SECONDS, cpu_time_budget - used - CHECK_MARGIN_SECONDS)


def lint_memory_bytes() -> int:
    """LINT_MEMORY_BYTES, or LINT_MEMORY_SHARE of the run's memory limit if
    that is less."""
    run_limit = run_memory_limit()
    if run_limit is None:
        return LINT_MEMORY_BYTES
    return min(LINT_MEMORY_BYTES, int(run_limit * LINT_MEMORY_SHARE))


def status_of(cmdline: list[str], returncode: int, output: str) -> str:
    """The status benchexec.tools.witnesslint gives a linter run that ended
    with `returncode` (negative: by that signal) and printed `output`."""
    from benchexec.tools.witnesslint import Tool

    exit_code = (butil.ProcessExitCode.create(signal=-returncode) if returncode < 0
                 else butil.ProcessExitCode.create(value=returncode))
    lines = output.split("\n")
    lines = [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    run = BaseTool2.Run(cmdline=cmdline, exit_code=exit_code, output=BaseTool2.RunOutput(lines),
                        termination_reason=None)
    return Tool().determine_result(run)


def lint(path: str, program: str, verdict: str, cpu_time_budget: int | float | None = None) -> Lint:
    """The Lint of the witness file `path` of the verdict `verdict` ("true"
    or "false") on the program file `program`, within the run's CPU-time
    budget `cpu_time_budget`. Never raises."""
    if verdict not in OPTIONS:
        return Lint(FAILED, f"no linter options for the verdict {verdict!r}")
    script = os.path.join(LINTER_DIR, "witnesslinter.py")
    if not os.path.isfile(script):
        return Lint(FAILED, f"{script} does not exist")
    arguments = [*OPTIONS[verdict], "--witness", os.path.abspath(path), os.path.abspath(program)]
    with _APART_LOCK:
        seconds = lint_seconds(cpu_time_budget)
        if seconds <= 0:
            return Lint(FAILED, f"the run's CPU-time budget of {cpu_time_budget:g} s leaves it no time")
        command = [sys.executable, "-c", _RUN, str(lint_memory_bytes()), str(max(1, int(seconds))), LINTER_DIR,
                   *arguments]
        try:
            done = subprocess.run(command, cwd=LINTER_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors="replace", timeout=seconds + 1)
        except subprocess.TimeoutExpired:
            return Lint(FAILED, f"took longer than {seconds:g} s")
        except (OSError, subprocess.SubprocessError) as error:
            return Lint(FAILED, f"could not run ({type(error).__name__}: {error})")
    try:
        status = status_of([script, *arguments], done.returncode, done.stdout)
    except Exception as error:  # noqa: BLE001 - a technical failure, never CoOpeRace's crash
        return Lint(FAILED, f"its status could not be determined ({type(error).__name__}: {error})")
    if status == DONE:
        return Lint(VALID, status)
    if status in INVALID_STATUSES:
        return Lint(INVALID, status)
    return Lint(FAILED, f"status {status}, exit code {done.returncode}")


def lint_delivered(path: str, program: str, verdict: str, cpu_time_budget: int | float | None = None,
                   paths: Mapping[str, str] | None = None, directory: str | None = None) -> Lint:
    """lint of the witness file `path` as CoOpeRace delivers it and the
    competition lints it: a copy under the name DELIVERED_NAME, in a new
    directory of its own under `directory` (the system's temporary directory
    if None), with every occurrence of a key of `paths` replaced by its value
    (as components.witness_files_to_file_root replaces the task's path). The
    copy is removed afterwards; `path` is not changed. Never raises."""
    paths = {old: new for old, new in (paths or {}).items() if old and old != new}
    try:
        own = tempfile.mkdtemp(prefix="cooperace-lint-", dir=directory)
    except OSError as error:
        return Lint(FAILED, f"no directory for its copy ({error})")
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as file:
            text = file.read()
        for old, new in paths.items():
            text = text.replace(old, new)
        copy = os.path.join(own, DELIVERED_NAME)
        with open(copy, "w", encoding="utf-8", errors="surrogateescape") as out:
            out.write(text)
        return lint(copy, program, verdict, cpu_time_budget)
    except OSError as error:
        return Lint(FAILED, f"its copy could not be made ({error})")
    finally:
        shutil.rmtree(own, ignore_errors=True)


# The witnesses of the self-test, relative to TOOL_DIR, and the program they are of.
SELF_TEST_DIR = os.path.join("tests", "witnesslint")
SELF_TEST = (("valid.witness.yml", "false", VALID), ("invalid.witness.yml", "false", INVALID))
SELF_TEST_PROGRAM = "program.c"


def self_test(cpu_time_budget: int | float | None = None) -> list[str]:
    """Lints the witnesses of SELF_TEST through lint_delivered, the path a
    component's witness takes, within the run's CPU-time budget
    `cpu_time_budget` (None: each lint within LINT_SECONDS), and returns one
    problem for each that does not get its expected outcome; [] if the
    linter works. A lint the budget leaves no time is FAILED, so a self-test
    without time left fails."""
    directory = os.path.join(TOOL_DIR, SELF_TEST_DIR)
    problems = []
    for name, verdict, expected in SELF_TEST:
        got = lint_delivered(os.path.join(directory, name), os.path.join(directory, SELF_TEST_PROGRAM), verdict,
                             cpu_time_budget)
        if got.outcome != expected:
            problems.append(f"WitnessLint gives {got.outcome} ({got.status}) for {SELF_TEST_DIR}/{name}, "
                            f"expected {expected}")
    return problems


_USABLE_LOCK = threading.Lock()
_usable: list[str] | None = None


def self_test_problems(cpu_time_budget: int | float | None = None) -> list[str]:
    """self_test within the run's CPU-time budget `cpu_time_budget`, run once
    per process (the first call; later calls return its result, whatever
    their budget): [] if the linter gives the expected outcomes. A caller lints
    only where this is []; otherwise every witness is judged by
    witnesses.file_passes, so that a linter that says invalid to everything
    in the competition's environment cannot reject every witness."""
    global _usable
    with _USABLE_LOCK:
        if _usable is None:
            try:
                _usable = self_test(cpu_time_budget)
            except Exception as error:  # noqa: BLE001 - a technical failure of the linter
                _usable = [f"the WitnessLint self-test failed ({type(error).__name__}: {error})"]
        return _usable


if __name__ == "__main__":
    found = self_test()
    for problem in found:
        print(f"CoOpeRace: {problem}", file=sys.stderr)
    print("WitnessLint self-test: " + ("failed" if found else "passed"))
    sys.exit(1 if found else 0)
