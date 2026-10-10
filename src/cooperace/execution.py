# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component under BenchExec's benchexec.runexecutor.RunExecutor
(execute_run), which is how CoOpeRace runs every component, in the Mode
that detect_mode finds once per CoOpeRace run.

Two parts of RunExecutor depend on where it runs:

- Its container (`use_namespaces=True`): stop() kills the container's PID
  namespace with every process in it, and the component's directory is an
  overlay (container_dir_modes), from which the files matching
  `result_files_patterns` are moved to `output_dir` after the run. It needs
  user namespaces, which Ubuntu 24.04's AppArmor grants to runexec and
  benchexec and not to python3, and which Docker and podman refuse.
- Its cgroups: a memory limit (`memlimit`) and a CPU-time limit
  (`hardtimelimit`) for all processes of the run together, the run's CPU
  time and peak memory, and, with the freezer, killing every process of the
  run after it ends. They need a cgroup in which BenchExec can make
  children: inside BenchExec's container under cgroups v2, or, from a shell,
  a systemd user scope that BenchExec makes with pystemd. Docker and podman
  mount /sys/fs/cgroup read-only.

RunExecutor refuses with sys.exit a `memlimit` without a memory cgroup, a
`hardtimelimit` without a cpu cgroup, and a run without the container and
without the freezer; it has no fallback for either limit. So components.py
passes only the limits the Mode has a cgroup for, and without any usable
cgroup each run gets the dummy cgroups (RunExecutorWithoutCgroups).
BenchExec logs to stderr through `logging`.

components.py imports this module only when a component runs, since
benchexec.runexecutor needs Linux."""
from __future__ import annotations

import functools
import logging
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from benchexec import runexecutor
from benchexec.cgroups import Cgroups
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY

from .processes import ComponentGroup

# Seconds between two checks whether a running run has to be stopped.
POLL_SECONDS = 0.1


@dataclass(frozen=True)
class Mode:
    """How RunExecutor runs components here: in its container or not, and
    whether it has a usable memory, cpu and freezer cgroup."""

    container: bool
    memory: bool
    cpu_time: bool
    freezer: bool

    @property
    def cgroups(self) -> bool:
        return self.memory or self.cpu_time or self.freezer

    def description(self) -> str:
        """For example "RunExecutor with its container, with cgroups
        (memory, cpu, freezer)", as a component's block prints it."""
        usable = [name for name, used in (("memory", self.memory), ("cpu", self.cpu_time),
                                          ("freezer", self.freezer)) if used]
        cgroups = f"with cgroups ({', '.join(usable)})" if usable else "without cgroups"
        return f"RunExecutor {'with' if self.container else 'without'} its container, {cgroups}"


class RunExecutorWithoutCgroups(runexecutor.RunExecutor):
    """A RunExecutor with Cgroups.dummy(), what BenchExec uses on a system
    without a cgroup file system, so that it neither looks for cgroups nor
    warns about their absence for each run. It applies no limit and measures
    neither CPU time nor memory."""

    def _init_cgroups(self):
        self.cgroups = Cgroups.dummy()
        self.cpus = None
        self.memory_nodes = None


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
    parent if it does not exist) as overlay, with BenchExec's log off."""
    overlay = tools_dir if os.path.isdir(tools_dir) else os.path.dirname(tools_dir)
    descriptor, log_file = tempfile.mkstemp(prefix="cooperace-container-check-")
    os.close(descriptor)
    logging.disable(logging.CRITICAL)
    try:
        result = runexecutor.RunExecutor(use_namespaces=True, dir_modes=container_dir_modes(overlay)).execute_run(
            ["true"], log_file, write_header=False)
    except (Exception, SystemExit):
        return False
    finally:
        logging.disable(logging.NOTSET)
        os.remove(log_file)
    return result.get("exitcode") is not None and result["exitcode"].value == 0


@functools.cache
def detect_mode(tools_dir: str) -> Mode:
    """The Mode of this process: `container` if container_works(`tools_dir`),
    and the cgroups of Cgroups.initialize() that pass require_subsystem, as
    RunExecutor finds them, but none without the container and the freezer,
    with which RunExecutor refuses to run. Cgroups.initialize() may move
    this process into a cgroup of its own, as every RunExecutor does."""
    container = container_works(tools_dir)
    cgroups = Cgroups.initialize()

    def usable(subsystem):
        return cgroups.require_subsystem(subsystem, log_method=logging.debug)

    if not (container or usable(cgroups.FREEZE)):
        return Mode(False, False, False, False)
    return Mode(container, usable(cgroups.MEMORY), usable(cgroups.CPU), usable(cgroups.FREEZE))


class RunExecutorError(Exception):
    """BenchExec could not start a run (its result has no exit code), or
    refused it."""


class FinishedRun(subprocess.CompletedProcess):
    """A component's run: `returncode` is negative for the signal that ended
    it and None if it was not started, `stdout` holds its output and standard
    error. From BenchExec's result: `termination_reason` (such as "cputime",
    "memory" or "killed"; None if it ended by itself), `cpu_time`,
    `wall_time` and the peak `memory`, each None if not measured. `mode` is
    the Mode it ran in."""

    def __init__(self, args, returncode: int | None, stdout: str, mode: Mode | None = None,
                 cpu_time: float | None = None, termination_reason: str | None = None,
                 wall_time: float | None = None, memory: int | None = None):
        super().__init__(args, returncode, stdout, "")
        self.mode = mode
        self.cpu_time = cpu_time
        self.termination_reason = termination_reason
        self.wall_time = wall_time
        self.memory = memory


class ExecutorRun:
    """A component running under a RunExecutor, as a ComponentGroup records
    it. stop() sets `stop_requested` and calls kill."""

    def __init__(self, executor: runexecutor.RunExecutor, kill_process_group: bool):
        self.executor = executor
        self.kill_process_group = kill_process_group
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
        In the container the SIGKILL ends every process of the run, and with
        the freezer RunExecutor kills the others after it. With
        `kill_process_group` it also kills the process group RunExecutor
        made the component's process the leader of; a descendant in a
        session of its own is not reached."""
        with self.executor.SUB_PROCESS_PIDS_LOCK:
            if self.killed:
                return
            pids = set(self.executor.SUB_PROCESS_PIDS)
        self.executor.stop()
        self.killed = bool(pids)
        for pid in pids if self.kill_process_group else ():
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def execute_run(mode: Mode, command: list[str], cwd: str, environments: Mapping, group: ComponentGroup,
                log_file: str, dir_modes: Mapping[str, str], memory: int | None = None,
                cpu_time: int | None = None, output_dir: str | None = None,
                result_files_patterns: Sequence[str] = ()) -> FinishedRun:
    """Runs `command` in `cwd` under a RunExecutor in the Mode `mode` and the
    ComponentGroup `group`, and returns its FinishedRun, not started if
    `group` was stopped first.

    The run's environment is what `environments` (a tool-info module's
    environment()) makes of CoOpeRace's, with HOME and TMPDIR of its own;
    its limits are `memory` bytes and `cpu_time` seconds (None: none), which
    `mode` must have the cgroup for. Its output goes to `log_file`. In the
    container the run gets `dir_modes`, and the files matching
    `result_files_patterns` under `cwd` are moved to `output_dir`.

    The run executes in a thread of its own, which this thread waits for,
    calling ExecutorRun.kill every POLL_SECONDS once a stop is requested. An
    exception in this thread while it waits (strategy.StopSignal) stops the
    run, waits for it and is re-raised. Raises RunExecutorError if
    BenchExec could not start the run or refused it."""
    if group.stopped:
        return FinishedRun(command, None, "", mode)
    executor_class = runexecutor.RunExecutor if mode.cgroups else RunExecutorWithoutCgroups
    if mode.container:
        executor = executor_class(use_namespaces=True, dir_modes=dict(dir_modes))
        files = {"output_dir": output_dir, "result_files_patterns": list(result_files_patterns)}
    else:
        executor, files = executor_class(use_namespaces=False), {}
    run = ExecutorRun(executor, kill_process_group=not (mode.container or mode.freezer))
    if not group.add(run):
        return FinishedRun(command, None, "", mode)

    outcome = {}

    def execute():
        try:
            outcome["result"] = executor.execute_run(
                list(command), log_file, write_header=False, workingDir=cwd,
                environments=dict(environments), memlimit=memory, hardtimelimit=cpu_time, **files)
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
    return FinishedRun(command, -exit_code.signal if exit_code.signal else exit_code.value, output, mode,
                       cpu_time=None if result.get("cputime") is None else float(result["cputime"]),
                       termination_reason=result.get("terminationreason"),
                       wall_time=None if result.get("walltime") is None else float(result["walltime"]),
                       memory=result.get("memory"))
