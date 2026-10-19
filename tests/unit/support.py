# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Helpers shared by the unit tests: polling, running a command as CoOpeRace
runs a component (run_command), a stub component that CoOpeRace can run
without anything under tools/, and small strategies and confs."""
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from benchexec import result as bresult
from benchexec.tools.template import BaseTool2

from src.cooperace.components import DOI_RECORD, ComponentSpec, ComponentVersion

# Upper bound in seconds for every wait on a process or a file.
WAIT_SECONDS = 15

# The DOI a stub component is installed from, as register_stub records it.
STUB_DOI = "10.5281/zenodo.0"

# Two witnesses that pass witnesses.passes for a false of no-data-race, copies
# described in test_witness_check.py.
PASSING_WITNESS = Path(__file__).resolve().parent / "witnesses" / "sv-sanitizers" / "lamport-b.witness.yml"
OTHER_PASSING_WITNESS = Path(__file__).resolve().parent / "witnesses" / "dartagnan" / "lamport-b.witness.yml"


def wait_until(predicate, timeout=WAIT_SECONDS, interval=0.05):
    """Calls `predicate` until it returns a true value, which is returned, or
    until `timeout` seconds have passed, when the last value is returned."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = predicate()
    return value


def run_command(command, cwd, group, environments=None):
    """Runs `command` in `cwd` in the ComponentGroup `group` with
    execution.execute_run, as CoOpeRace runs a component, with the
    environment `environments` makes of CoOpeRace's (None: {}) and no
    limits, and returns its execution.FinishedRun. Needs BenchExec's
    container (the fixture `container`)."""
    from src.cooperace import execution

    descriptor, log_file = tempfile.mkstemp(prefix="run-", suffix=".log", dir=cwd)
    os.close(descriptor)
    return execution.execute_run(command, str(cwd), environments or {}, group, log_file,
                                 execution.container_dir_modes(str(cwd)))


class StubTool(BaseTool2):
    """A tool-info module for a shell script. The script's status is the text
    after "STUB-STATUS: " in a line of its output, or "unknown" if there is no
    such line."""

    def __init__(self, tool_name, script):
        self.tool_name = tool_name
        self.script = str(script)

    def name(self):
        return self.tool_name

    def executable(self, tool_locator):
        return self.script

    def cmdline(self, executable, options, task, rlimits):
        return [executable, *options]

    def determine_result(self, run):
        prefix = "STUB-STATUS: "
        for line in run.output:
            if line.startswith(prefix):
                return line[len(prefix):]
        return bresult.RESULT_UNKNOWN


def make_script(directory, body):
    """Writes the executable shell script `directory`/stub.sh with `body` as
    its commands and returns its path. register_stub makes `directory` the
    stub's tool directory, which CoOpeRace runs it in."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "stub.sh"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@dataclass(frozen=True)
class StubSpec(ComponentSpec):
    """A registry entry whose tool-info object is a StubTool for `script`,
    in the script's directory, with the default WitnessSpec. The tool-info
    object's name() is `tool_name`, or the entry's `name` if that is None."""

    script: str = ""
    tool_name: str | None = None

    def tool(self):
        return StubTool(self.tool_name or self.name, self.script)


def register_stub(runner, tool_name, script, name_of_tool=None, options=()):
    """Makes `runner` run the shell script `script` for the component
    `tool_name`, which the conf can then name, with the options `options`.
    This is the test hook: the ComponentRunner looks components up in its
    `registry`, a copy of components.REGISTRY, and their options in its
    `versions`, which can be added to after construction. The stub is
    installed as download-tools.py installs a component: the script's
    directory records STUB_DOI, and `versions` gets that DOI for it.
    `name_of_tool` is what the tool-info object's name() returns, if that
    is not `tool_name`."""
    directory = str(Path(script).parent)
    (Path(directory) / DOI_RECORD).write_text(STUB_DOI + "\n")
    runner.registry[tool_name] = StubSpec(tool_name, "", directory, script=str(script), tool_name=name_of_tool)
    runner.versions[directory] = ComponentVersion(STUB_DOI, "stub", tuple(options))


def one_after_another(*names, **limits):
    """A strategy that runs the components `names` one per phase, in order,
    trusts each one's answers less than those of the one before, and asks
    them for the witness in the same order. `limits` are further keys of the
    strategy, such as cpuTimeLimits."""
    return {"phases": [[name] for name in names], "answers": [[name] for name in names],
            "witnesses": list(names), **limits}


def side_by_side(*names, **limits):
    """A strategy that runs the components `names` together in one phase,
    trusts each one's answers less than those of the one before, and asks
    them for the witness in the same order."""
    return {"phases": [list(names)], "answers": [[name] for name in names], "witnesses": list(names), **limits}


def data_race_conf(strategy):
    """A conf whose only strategy, for no-data-race, is `strategy`."""
    return {"properties": {"no-data-race": strategy}}


# A test that runs a strategy to its end, and so fails while
# strategy.run_strategy, the executor of the format of phases, answers and
# witnesses, is not written and raises NotImplementedError. Its expectations
# are those of a strategy whose components run as one_after_another and
# side_by_side describe. strict: the test fails once it passes, so that the
# mark is removed with the executor.
NEEDS_EXECUTOR = pytest.mark.xfail(strict=True,
                                   reason="strategy.run_strategy, the executor of the strategy format, is not written")
