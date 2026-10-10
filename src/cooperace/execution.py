# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component under BenchExec's benchexec.runexecutor.RunExecutor
in its container (execute_run), which is how CoOpeRace runs every component.

The container needs user namespaces and its limits need cgroups, which a
process has inside BenchExec's own container, under runexec or benchexec
with cgroups v2, as in the competition. From a shell on Ubuntu 24.04,
AppArmor gives user namespaces only to runexec and benchexec, not to
python3, so CoOpeRace is run there through the wrapper `cooperace-run`, which
calls runexec. container_works says whether the container can be made;
cli.run refuses to start any component where it cannot.

In the container, stop() kills the container's PID namespace with every
process in it, the component's directory is an overlay
(container_dir_modes), and the files matching `result_files_patterns` are
moved from the overlay to `output_dir` after the run. The memory limit
(`memlimit`) and the CPU-time limit (`hardtimelimit`) are limits of the
run's cgroup, for all its processes together. BenchExec logs to stderr
through `logging`.

components.py imports this module only when a component runs, since
benchexec.runexecutor needs Linux."""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence

from benchexec import runexecutor
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY

from .processes import ComponentGroup

# Seconds between two checks whether a running run has to be stopped.
POLL_SECONDS = 0.1


def container_dir_modes(tool_directory: str, writable_dirs: Sequence[str] = ()) -> dict[str, str]:
    """The directory modes of a component's container: `/` read-only and
    `/home` hidden, as the competition gives a tool, `/run` and `/tmp`
    hidden, as BenchExec does by default, `tool_directory` an overlay, and
    `writable_dirs` with full access, so that what the component writes there
    is visible outside."""
    modes = {"/": DIR_READ_ONLY, "/run": DIR_HIDDEN, "/tmp": DIR_HIDDEN, "/home": DIR_HIDDEN,
             tool_directory: DIR_OVERLAY}
    modes.update(dict.fromkeys(writable_dirs, DIR_FULL_ACCESS))
    return modes


def container_works(tools_dir: str) -> bool:
    """Whether RunExecutor runs `true` in its container with `tools_dir` (its
    parent if it does not exist) as overlay and working directory. Where it
    cannot, BenchExec says why on stderr."""
    overlay = tools_dir if os.path.isdir(tools_dir) else os.path.dirname(tools_dir)
    descriptor, log_file = tempfile.mkstemp(prefix="cooperace-container-check-")
    os.close(descriptor)
    try:
        result = runexecutor.RunExecutor(use_namespaces=True, dir_modes=container_dir_modes(overlay)).execute_run(
            ["true"], log_file, write_header=False, workingDir=overlay)
    except (Exception, SystemExit):
        return False
    finally:
        os.remove(log_file)
    return result.get("exitcode") is not None and result["exitcode"].value == 0


class RunExecutorError(Exception):
    """BenchExec could not start a run (its result has no exit code), or
    refused it."""


class FinishedRun(subprocess.CompletedProcess):
    """A component's run: `returncode` is negative for the signal that ended
    it and None if it was not started, `stdout` holds its output and standard
    error. From BenchExec's result: `termination_reason` (such as "cputime",
    "memory" or "killed"; None if it ended by itself), `cpu_time`,
    `wall_time` and the peak `memory`, each None if not measured."""

    def __init__(self, args, returncode: int | None, stdout: str, cpu_time: float | None = None,
                 termination_reason: str | None = None, wall_time: float | None = None,
                 memory: int | None = None):
        super().__init__(args, returncode, stdout, "")
        self.cpu_time = cpu_time
        self.termination_reason = termination_reason
        self.wall_time = wall_time
        self.memory = memory


class ExecutorRun:
    """A component running under a RunExecutor, as a ComponentGroup records
    it. stop() sets `stop_requested` and calls kill."""

    def __init__(self, executor: runexecutor.RunExecutor):
        self.executor = executor
        self.stop_requested = threading.Event()
        self.killed = False

    def stop(self) -> None:
        self.stop_requested.set()
        self.kill()

    def kill(self) -> None:
        """Calls the executor's stop(), which sends SIGKILL to the component's
        process, unless an earlier call has found that process; sets `killed`
        if this call finds it. A stop() before the process has started does
        not keep it from starting, so execute_run calls kill until `killed`.
        The SIGKILL ends the container's PID namespace with every process of
        the run."""
        with self.executor.SUB_PROCESS_PIDS_LOCK:
            if self.killed:
                return
            pids = set(self.executor.SUB_PROCESS_PIDS)
        self.executor.stop()
        self.killed = bool(pids)


def execute_run(command: list[str], cwd: str, environments: Mapping, group: ComponentGroup, log_file: str,
                dir_modes: Mapping[str, str], memory: int | None = None, cpu_time: int | None = None,
                output_dir: str | None = None, result_files_patterns: Sequence[str] = ()) -> FinishedRun:
    """Runs `command` in `cwd` under a RunExecutor in its container, in the
    ComponentGroup `group`, and returns its FinishedRun, not started if
    `group` was stopped first.

    The run's environment is what `environments` (a tool-info module's
    environment()) makes of CoOpeRace's, with HOME and TMPDIR of its own;
    its directory modes are `dir_modes`, its limits `memory` bytes and
    `cpu_time` seconds (None: none). Its output goes to `log_file`, and the
    files matching `result_files_patterns` under `cwd` are moved to
    `output_dir`.

    The run executes in a thread of its own, which this thread waits for,
    calling ExecutorRun.kill every POLL_SECONDS once a stop is requested. An
    exception in this thread while it waits (strategy.StopSignal) stops the
    run, waits for it and is re-raised. Raises RunExecutorError if
    BenchExec could not start the run or refused it."""
    if group.stopped:
        return FinishedRun(command, None, "")
    executor = runexecutor.RunExecutor(use_namespaces=True, dir_modes=dict(dir_modes))
    run = ExecutorRun(executor)
    if not group.add(run):
        return FinishedRun(command, None, "")

    outcome = {}

    def execute():
        try:
            outcome["result"] = executor.execute_run(
                list(command), log_file, write_header=False, workingDir=cwd,
                environments=dict(environments), memlimit=memory, hardtimelimit=cpu_time,
                output_dir=output_dir, result_files_patterns=list(result_files_patterns))
        except SystemExit as error:
            outcome["error"] = RunExecutorError(f"BenchExec refused to run {command[0]}: {error}")
        except BaseException as error:  # noqa: B036 - re-raised in the waiting thread
            outcome["error"] = error

    worker = threading.Thread(target=execute, name=f"RunExecutor run of {command[0]}")
    try:
        worker.start()
        while worker.is_alive():
            worker.join(POLL_SECONDS)
            if run.stop_requested.is_set():
                run.kill()
    except BaseException:
        run.stop_requested.set()
        while worker.is_alive():
            run.kill()
            worker.join(POLL_SECONDS)
        raise
    finally:
        group.remove(run)

    if "error" in outcome:
        raise outcome["error"]
    result = outcome["result"]
    exit_code = result.get("exitcode")
    if exit_code is None:
        raise RunExecutorError(f"BenchExec could not run {command[0]} (termination reason "
                               f"{result.get('terminationreason')})")
    with open(log_file, errors="replace") as log:
        output = log.read()
    return FinishedRun(command, -exit_code.signal if exit_code.signal else exit_code.value, output,
                       cpu_time=None if result.get("cputime") is None else float(result["cputime"]),
                       termination_reason=result.get("terminationreason"),
                       wall_time=None if result.get("walltime") is None else float(result["walltime"]),
                       memory=result.get("memory"))
