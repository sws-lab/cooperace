"""Helpers shared by the unit tests: polling, process-group inspection and a
stub component that CoOpeRace can run without anything under tools/."""
import os
import stat
import time
from pathlib import Path

from benchexec import result as bresult
from benchexec.tools.template import BaseTool2

# Upper bound in seconds for every wait on a process or a file.
WAIT_SECONDS = 15


def wait_until(predicate, timeout=WAIT_SECONDS, interval=0.05):
    """Calls `predicate` until it returns a true value, which is returned, or
    until `timeout` seconds have passed, when the last value is returned."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = predicate()
    return value


def group_members(pgid):
    """The pids of the live processes (zombies excluded) in process group
    `pgid`, read from /proc."""
    members = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as stat_file:
                text = stat_file.read()
        except OSError:
            continue
        fields = text[text.rindex(")") + 2:].split()
        if fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(entry))
    return members


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
    its commands and returns its path. CoOpeRace runs a component with the
    directory of its executable as the working directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "stub.sh"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def register_stub(coop, tool_name, script):
    """Makes `coop` run the shell script `script` for the component
    `tool_name`, which the conf can then name. This is the test hook: the
    Cooperace instance looks components up in its `tools` dictionary (tool-info
    objects) and `tool_locations` dictionary (their directories), and both can
    be assigned to after construction."""
    coop.tools[tool_name] = StubTool(tool_name, script)
    coop.tool_locations[tool_name] = str(Path(script).parent)
