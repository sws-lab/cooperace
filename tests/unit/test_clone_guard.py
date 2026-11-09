# SPDX-FileCopyrightText: 2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""execution.guard_clone, which keeps the start of a BenchExec container from
deadlocking CoOpeRace's process: BenchExec's own
benchexec.container.execute_in_namespace runs, with clone() replaced by a
function that holds the GIL for 50 ms, as a clone() does that the OS
preempts, and makes no process. Without the guard, a thread that starts a
thread meanwhile deadlocks the process (Python 3.13 and later), which is why
that scenario runs in a process of its own (SCENARIO)."""
import gc
import subprocess
import sys
import threading

import pytest
from benchexec import container, libc
from benchexec import util as butil

from src.cooperace import TOOL_DIR, execution

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="BenchExec's container needs Linux")

# Seconds after which the process of SCENARIO counts as deadlocked; it ends
# in about one second.
DEADLOCK_SECONDS = 30

# argv: the checkout and a directory. As cli.run does, calls container_works
# in the main thread first, then starts a thread that starts threads, and
# runs a component with execute_run whose RunExecutor makes 10 containers.
SCENARIO = """
import ctypes, os, sys, threading, time
sys.path.insert(0, sys.argv[1])
from src.cooperace import execution
from src.cooperace.processes import ComponentGroup
from benchexec import container, libc, util

hold_gil = ctypes.PyDLL("libc.so.6").usleep

def clone(callback, stack, flags, func_p):
    hold_gil(50000)
    return 0

class Executor:
    # RunExecutor: its run makes 10 containers; its process counts as started
    def __init__(self, **kwargs):
        self.SUB_PROCESS_PIDS_LOCK = threading.Lock()
        self.SUB_PROCESS_PIDS = {0}

    def stop(self):
        pass

    def execute_run(self, args, output_filename, **kwargs):
        for _ in range(10):
            container.execute_in_namespace(lambda: 0)
        open(output_filename, "w").close()
        return {"exitcode": util.ProcessExitCode.create(value=0)}

def start_threads():
    while True:
        time.sleep(0.001)
        thread = threading.Thread(target=int)
        thread.start()
        thread.join()

libc.clone = clone
execution.runexecutor.RunExecutor = Executor
assert execution.container_works(sys.argv[2])
threading.Thread(target=start_threads, daemon=True).start()
run = execution.execute_run(["true"], sys.argv[2], {}, ComponentGroup(), os.path.join(sys.argv[2], "log"), {})
print("returncode", run.returncode)
"""


def test_a_thread_that_starts_threads_while_a_container_starts_does_not_deadlock_the_process(tmp_path):
    try:
        done = subprocess.run([sys.executable, "-c", SCENARIO, TOOL_DIR, str(tmp_path)], capture_output=True,
                              text=True, timeout=DEADLOCK_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the process deadlocked: it did not end within {DEADLOCK_SECONDS} s")
    assert (done.returncode, done.stdout) == (0, "returncode 0\n"), done.stderr


class Executor:
    """Stands in for RunExecutor: its run makes one container and records
    whether garbage collection was on while clone() ran."""

    def __init__(self, **kwargs):
        self.SUB_PROCESS_PIDS_LOCK = threading.Lock()
        self.SUB_PROCESS_PIDS = {0}
        self.collecting_in_clone = None

    def stop(self):
        pass

    def execute_run(self, args, output_filename, **kwargs):
        def clone(callback, stack, flags, func_p):
            self.collecting_in_clone = gc.isenabled()
            return 0

        libc.clone, unpatched = clone, libc.clone
        try:
            container.execute_in_namespace(lambda: 0)
        finally:
            libc.clone = unpatched
        open(output_filename, "w").close()
        return {"exitcode": butil.ProcessExitCode.create(value=0)}


def test_a_container_starts_without_garbage_collection(tmp_path, monkeypatch):
    executors = []

    def make(**kwargs):
        executors.append(Executor(**kwargs))
        return executors[-1]

    monkeypatch.setattr(execution.runexecutor, "RunExecutor", make)
    #guard_clone replaces execute_in_namespace for good; the test leaves it as it found it
    monkeypatch.setattr(container, "execute_in_namespace", container.execute_in_namespace)
    assert gc.isenabled()

    assert execution.container_works(str(tmp_path))

    assert executors[0].collecting_in_clone is False
    assert gc.isenabled()
    assert sys.getswitchinterval() >= execution.SWITCH_INTERVAL_SECONDS
