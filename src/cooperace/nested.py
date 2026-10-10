# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component as a nested BenchExec run (run_nested): in a
container and a cgroup of its own, started by BenchExec's
benchexec.runexecutor.RunExecutor, as CoVeriTeam runs its actors. It is the
alternative to processes.run_in_session and processes.with_rlimits that
components.NestedComponentRunner uses.

What the nested run gives that run_in_session does not:

- The memory limit (`memlimit`) and the CPU-time limit (`hardtimelimit`) are
  limits of the component's cgroup, so they count every process of the
  component together, where an rlimit counts each process on its own.
  BenchExec reports a run its limit ended with the termination reason
  "memory" or "cputime" (also "walltime": a run with a CPU-time limit gets a
  wall-time limit 30 s higher).
- Stopping a run (NestedRun.stop) kills the run's container, whose PID
  namespace holds every process the component started, also one that
  started a session of its own, which processes.stopProcessGroups does not
  reach.
- The component's directory is an overlay in the run's container
  (container_dir_modes), so nothing the component writes there reaches the
  directory itself; the files that match `result_files_patterns` are moved
  from the overlay to `output_dir` after the run. A file left by an earlier
  run is in the directory, not in the overlay, so it is never collected,
  without comparing modification times.

What it needs: cgroups that BenchExec can use, which inside BenchExec's own
container means cgroups v2 (BenchExec 3.18 and later delegates the run's
cgroup to the tool; under v1 the cgroup file system is read-only there), and
user namespaces for the container. Inside BenchExec's container both hold
under cgroups v2. Outside it, Ubuntu 24.04's AppArmor forbids user
namespaces to every program but those with a profile that allows them
(BenchExec's own commands have one), so there BenchExec reports that it
cannot run the container and run_nested raises NestedRunError. The limits of
the outer run's cgroup still bound all components together.

BenchExec reports through `logging`; CoOpeRace sets up no handler, so its
warnings and errors (such as "Killing process <pid> forcefully." when a run
is stopped) go to stderr.

Uses BenchExec from the wheel that src/cooperace/__init__.py puts first on
sys.path; components.py is the only other module that imports BenchExec."""
from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence

from benchexec import runexecutor
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY

from .processes import ComponentGroup, FinishedProcess

# Seconds between two checks, while a nested run is running, whether it has
# to be stopped (run_nested).
POLL_SECONDS = 0.1


class NestedRunError(Exception):
    """Raised by run_nested when BenchExec could not start the run (its
    result has the termination reason "failed" and no exit code); BenchExec
    has logged the reason on stderr."""


class NestedFinishedProcess(FinishedProcess):
    """The processes.FinishedProcess of a nested run: `returncode`
    is the exit code of the component's process, negative for the signal
    that ended it, and None if it was not started; `stdout` its output and
    standard error in one; `cpu_time` the CPU time in seconds of all its
    processes together, as the run's cgroup measured it, or None if it was
    not started. Further, from BenchExec's result of the run:
    `termination_reason` ("cputime", "walltime", "memory", "killed" or None
    if the component ended by itself), `wall_time` in seconds and `memory`,
    the peak memory of the run's cgroup in bytes (None if not measured)."""

    def __init__(self, args, returncode, stdout, stderr, cpu_time=None, termination_reason=None,
                 wall_time=None, memory=None):
        super().__init__(args, returncode, stdout, stderr, cpu_time)
        self.termination_reason = termination_reason
        self.wall_time = wall_time
        self.memory = memory


class NestedRun:
    """A component running under a RunExecutor, as a ComponentGroup records
    it. stop() ends it: it records that the run is to be stopped and calls
    the executor's stop(), which kills the run's container and so every
    process in it. run_nested calls the executor's stop() again until a call
    has found the run's process, since a stop() before the executor has
    started the process does not keep it from starting."""

    def __init__(self, executor: runexecutor.RunExecutor):
        self.executor = executor
        self.stop_requested = threading.Event()

    def stop(self) -> None:
        self.stop_requested.set()
        self.executor.stop()

    def stop_found_process(self) -> bool:
        """Calls the executor's stop() and returns whether the run's process
        was running then, so that the call killed it."""
        with self.executor.SUB_PROCESS_PIDS_LOCK:
            running = bool(self.executor.SUB_PROCESS_PIDS)
        self.executor.stop()
        return running


def container_dir_modes(tool_directory: str, writable_dirs: Sequence[str] = ()) -> dict[str, str]:
    """The directory modes of a component's container: what the competition
    gives a tool (`/` read-only, `/home` hidden) and what BenchExec hides by
    default (`/run`, `/tmp`, so that each run has empty ones of its own), the
    component's directory `tool_directory` as an overlay, and each of
    `writable_dirs` with full access, so that what the component writes
    there is visible outside its container (a witness directory of its
    run)."""
    modes = {"/": DIR_READ_ONLY, "/run": DIR_HIDDEN, "/tmp": DIR_HIDDEN, "/home": DIR_HIDDEN,
             tool_directory: DIR_OVERLAY}
    for directory in writable_dirs:
        modes[directory] = DIR_FULL_ACCESS
    return modes


def run_nested(command: list[str], cwd: str, environments: Mapping, group: ComponentGroup,
               log_file: str, dir_modes: Mapping[str, str], memory: int | None = None,
               cpu_time: int | None = None, output_dir: str | None = None,
               result_files_patterns: Sequence[str] = ()) -> NestedFinishedProcess:
    """Runs `command` in `cwd` as a nested BenchExec run in the
    ComponentGroup `group` and returns its NestedFinishedProcess, with
    `returncode` None if `group` was stopped before it could start.

    The run gets a container with the directory modes `dir_modes`
    (container_dir_modes), the environment that `environments` (the result
    of a tool-info module's environment(), {} for CoOpeRace's own
    environment) makes of CoOpeRace's environment, a memory limit of
    `memory` bytes and a CPU-time limit of `cpu_time` seconds (None: no
    limit of its own, only the outer run's). Its output and standard error
    go to `log_file`, without a header, and are returned as `stdout`. Files
    matching `result_files_patterns`, relative to `cwd`, that the component
    wrote in its container are moved to the same relative path under
    `output_dir`.

    The run is executed in a thread of its own, which this thread waits for,
    checking every POLL_SECONDS whether the run is to be stopped
    (NestedRun). If this thread is interrupted while it waits (by
    strategy.StopSignal in the main thread), the run is stopped and waited
    for, and the exception re-raised. Raises NestedRunError if BenchExec
    could not start the run."""
    if group.stopped:
        return NestedFinishedProcess(command, None, "", "")
    executor = runexecutor.RunExecutor(use_namespaces=True, dir_modes=dict(dir_modes))
    run = NestedRun(executor)
    if not group.add(run):
        return NestedFinishedProcess(command, None, "", "")

    outcome = {}

    def execute():
        try:
            outcome["result"] = executor.execute_run(
                list(command), log_file, write_header=False, workingDir=cwd,
                environments=dict(environments), memlimit=memory, hardtimelimit=cpu_time,
                output_dir=output_dir, result_files_patterns=list(result_files_patterns))
        except BaseException as error:  # noqa: B036 - re-raised in the waiting thread
            outcome["error"] = error

    worker = threading.Thread(target=execute, name=f"nested run of {command[0]}")
    killed = False
    try:
        worker.start()
        while worker.is_alive():
            worker.join(POLL_SECONDS)
            if run.stop_requested.is_set() and not killed:
                killed = run.stop_found_process()
    except BaseException:
        run.stop_requested.set()
        while worker.is_alive():
            if not killed:
                killed = run.stop_found_process()
            worker.join(POLL_SECONDS)
        raise
    finally:
        group.remove(run)

    if "error" in outcome:
        raise outcome["error"]
    result = outcome["result"]
    exit_code = result.get("exitcode")
    if exit_code is None:
        raise NestedRunError(f"BenchExec could not run {command[0]} (termination reason "
                             f"{result.get('terminationreason')})")
    returncode = -exit_code.signal if exit_code.signal else exit_code.value
    with open(log_file, errors="replace") as log:
        output = log.read()
    cpu = result.get("cputime")
    wall = result.get("walltime")
    return NestedFinishedProcess(command, returncode, output, "",
                                 cpu_time=None if cpu is None else float(cpu),
                                 termination_reason=result.get("terminationreason"),
                                 wall_time=None if wall is None else float(wall),
                                 memory=result.get("memory"))
