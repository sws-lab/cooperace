# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component under BenchExec's benchexec.runexecutor.RunExecutor
(execute_run), which is how CoOpeRace runs every component, and the Mode in
which RunExecutor can run here (detect_mode).

RunExecutor starts a command, waits for it, and reports its exit code,
measurements and termination reason. Two of its parts depend on where
CoOpeRace runs:

- Its container (`use_namespaces=True`) puts the run into namespaces of its
  own. Its stop() then kills the container's PID namespace, with every
  process the component started; and the directory modes of the run
  (container_dir_modes) make the component's directory an overlay, so that
  nothing the component writes there reaches the directory, and the files
  matching `result_files_patterns` are moved from the overlay to
  `output_dir` after the run. The container needs user namespaces. Ubuntu
  24.04's AppArmor grants them only to programs whose profile allows them,
  among them BenchExec's runexec and benchexec, so CoOpeRace started by
  those can make the container and CoOpeRace started from a shell cannot;
  Docker's default seccomp profile refuses them too.
- Its cgroups give a memory limit (`memlimit`) and a CPU-time limit
  (`hardtimelimit`) for all processes of the run together, ending the run
  with the termination reason "memory" or "cputime" (a run with a CPU-time
  limit also gets a wall-time limit 30 s higher, "walltime"), measure the
  run's CPU time and peak memory, and, with the freezer, let RunExecutor
  kill every process of the run after it ends or is stopped. They need a
  cgroup in which BenchExec can make child cgroups: inside BenchExec's
  container under cgroups v2 (BenchExec 3.18 and later delegates the run's
  cgroup to the tool), or in a systemd user session, where BenchExec makes a
  scope with pystemd. Docker and podman mount /sys/fs/cgroup read-only by
  default, and so does BenchExec's container under cgroups v1.

RunExecutor itself refuses, with sys.exit, a `memlimit` without a memory
cgroup, a `hardtimelimit` without a cpu cgroup, and a run without its
container and without a freezer cgroup ("Cannot reliably kill
sub-processes"). It has no fallback for either limit. So detect_mode finds
out once which of the container and the cgroups are usable, components.py
passes only the limits the Mode can apply and prints the others as not
applied, and where no cgroup is usable each run gets BenchExec's dummy
cgroups (RunExecutorWithoutCgroups).

BenchExec reports through `logging`; CoOpeRace sets up no handler, so its
warnings and errors (such as "Killing process <pid> forcefully." when a run
is stopped) go to stderr.

Imports BenchExec from the wheel that src/cooperace/__init__.py puts first
on sys.path; components.py, the only other module that imports BenchExec,
imports this module only when a component runs, since
benchexec.runexecutor needs Linux."""
from __future__ import annotations

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

# Seconds between two checks, while a run is running, whether it has to be
# stopped (execute_run).
POLL_SECONDS = 0.1


@dataclass(frozen=True)
class Mode:
    """How RunExecutor runs components here, as detect_mode finds it:
    `container`, whether it runs each in its container; `memory`, `cpu_time`
    and `freezer`, whether it has a usable memory, cpu and freezer cgroup,
    so whether it can apply a memory limit and a CPU-time limit and kill all
    processes of a run without the container. With none of the three
    (`cgroups` False) each run gets RunExecutorWithoutCgroups."""

    container: bool
    memory: bool
    cpu_time: bool
    freezer: bool

    @property
    def cgroups(self) -> bool:
        return self.memory or self.cpu_time or self.freezer

    @property
    def kills_process_group(self) -> bool:
        """Whether ExecutorRun.kill also kills the run's process group: without
        the container and the freezer, RunExecutor's stop() kills only the
        component's own process."""
        return not (self.container or self.freezer)

    def description(self) -> str:
        """The mode as the line "Tool name: <name> Mode: ..." of a component's
        block gives it, for example "RunExecutor with its container, with
        cgroups (memory, cpu, freezer)"."""
        usable = [name for name, used in (("memory", self.memory), ("cpu", self.cpu_time),
                                          ("freezer", self.freezer)) if used]
        cgroups = f"with cgroups ({', '.join(usable)})" if usable else "without cgroups"
        return f"RunExecutor {'with' if self.container else 'without'} its container, {cgroups}"


class RunExecutorWithoutCgroups(runexecutor.RunExecutor):
    """A RunExecutor whose cgroups are Cgroups.dummy(), which BenchExec
    itself uses on a system without a cgroup file system, for a Mode without
    usable cgroups: it does not look for a cgroup for each run, and does not
    warn for each run that it found none. It applies no memory or CPU-time
    limit and measures neither CPU time nor memory; without its container,
    its stop() kills only the component's own process."""

    def _init_cgroups(self):
        self.cgroups = Cgroups.dummy()
        self.cpus = None
        self.memory_nodes = None


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


def container_works(tools_dir: str) -> bool:
    """Whether RunExecutor can run `true` in its container with the
    directory modes of a component in `tools_dir` (container_dir_modes; the
    parent directory if `tools_dir` does not exist), with BenchExec's log
    switched off while it tries, so that a refused container leaves no
    message on stderr."""
    overlay = tools_dir if os.path.isdir(tools_dir) else os.path.dirname(tools_dir)
    descriptor, log_file = tempfile.mkstemp(prefix="cooperace-container-check-", suffix=".log")
    os.close(descriptor)
    logging.disable(logging.CRITICAL)
    try:
        executor = runexecutor.RunExecutor(use_namespaces=True, dir_modes=container_dir_modes(overlay))
        result = executor.execute_run(["true"], log_file, write_header=False)
    except (Exception, SystemExit):
        return False
    finally:
        logging.disable(logging.NOTSET)
        if os.path.exists(log_file):
            os.remove(log_file)
    exit_code = result.get("exitcode")
    return exit_code is not None and exit_code.value == 0


_MODE = None
_MODE_LOCK = threading.Lock()


def detect_mode(tools_dir: str) -> Mode:
    """The Mode of this process, found out on the first call and the same
    for every later one: `container` if container_works(`tools_dir`); the
    cgroups those of benchexec.cgroups.Cgroups.initialize() for which
    require_subsystem holds, as RunExecutor finds them for each run, but
    none at all without the container and the freezer, since RunExecutor
    then refuses to run. Cgroups.initialize() may move this process into a
    cgroup of its own, as every RunExecutor does."""
    global _MODE
    with _MODE_LOCK:
        if _MODE is None:
            container = container_works(tools_dir)
            cgroups = Cgroups.initialize()

            def usable(subsystem):
                return cgroups.require_subsystem(subsystem, log_method=logging.debug)

            freezer = usable(cgroups.FREEZE)
            if container or freezer:
                _MODE = Mode(container, usable(cgroups.MEMORY), usable(cgroups.CPU), freezer)
            else:
                _MODE = Mode(False, False, False, False)
        return _MODE


class RunExecutorError(Exception):
    """Raised by execute_run when BenchExec could not start the run (its
    result has no exit code; BenchExec has logged the reason on stderr) or
    refused it."""


class FinishedRun(subprocess.CompletedProcess):
    """A component's run as execute_run returns it: `returncode` is the exit
    code of the component's process, negative for the signal that ended it,
    and None if it was not started; `stdout` its output and standard error
    in one, `stderr` empty. From BenchExec's result of the run:
    `termination_reason` ("cputime", "walltime", "memory", "killed", or None
    if the component ended by itself), `cpu_time` and `wall_time` in seconds
    and `memory`, the peak memory in bytes, each None if not measured (CPU
    time and memory need cgroups). `mode` is the Mode it ran in."""

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
    it. stop() ends it: it records that the run is to be stopped
    (`stop_requested`) and calls kill. A RunExecutor.stop() before the
    executor has started the run's process does not keep that process from
    starting, so execute_run calls kill again until a call has found the
    process (`killed`)."""

    def __init__(self, executor: runexecutor.RunExecutor, kill_process_group: bool):
        self.executor = executor
        self.kill_process_group = kill_process_group
        self.stop_requested = threading.Event()
        self.killed = False

    def stop(self) -> None:
        self.stop_requested.set()
        self.kill()

    def kill(self) -> None:
        """Calls the executor's stop(), unless an earlier call has found the
        run's process, and sets `killed` if this call finds it. The
        executor's stop() sends SIGKILL to the component's process, and logs
        "Killing process <pid> forcefully."; in its container that kills
        every process of the run, and with the freezer RunExecutor kills the
        others after it. With `kill_process_group` (Mode.kills_process_group)
        this also sends SIGKILL to the process group of the component's
        process, which RunExecutor makes the leader of a group of its own; a
        descendant that started a session of its own is not reached."""
        with self.executor.SUB_PROCESS_PIDS_LOCK:
            if self.killed:
                return
            pids = set(self.executor.SUB_PROCESS_PIDS)
        self.executor.stop()
        if pids:
            self.killed = True
            if self.kill_process_group:
                for pid in pids:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


def execute_run(mode: Mode, command: list[str], cwd: str, environments: Mapping, group: ComponentGroup,
                log_file: str, dir_modes: Mapping[str, str], memory: int | None = None,
                cpu_time: int | None = None, output_dir: str | None = None,
                result_files_patterns: Sequence[str] = ()) -> FinishedRun:
    """Runs `command` in `cwd` under a RunExecutor in the Mode `mode`, in the
    ComponentGroup `group`, and returns its FinishedRun, with `returncode`
    None if `group` was stopped before it could start.

    The run gets the environment that `environments` (the result of a
    tool-info module's environment()) makes of CoOpeRace's environment, as
    BenchExec builds it, with HOME and TMPDIR of its own; a memory limit of
    `memory` bytes and a CPU-time limit of `cpu_time` seconds (None: no
    limit of its own), which `mode` must be able to apply, else BenchExec
    refuses the run. Its output and standard error go to `log_file`, without
    a header, and are returned as `stdout`. In the container (`mode.container`)
    it gets the directory modes `dir_modes` (container_dir_modes), and the
    files matching `result_files_patterns`, relative to `cwd`, that the
    component wrote are moved to the same relative path under `output_dir`;
    without the container both are ignored.

    The run is executed in a thread of its own, which this thread waits for,
    checking every POLL_SECONDS whether the run is to be stopped
    (ExecutorRun). If this thread is interrupted while it waits (by
    strategy.StopSignal in the main thread), the run is stopped and waited
    for, and the exception re-raised. Raises RunExecutorError if BenchExec
    could not start the run or refused it."""
    if group.stopped:
        return FinishedRun(command, None, "", mode)
    executor_class = runexecutor.RunExecutor if mode.cgroups else RunExecutorWithoutCgroups
    if mode.container:
        executor = executor_class(use_namespaces=True, dir_modes=dict(dir_modes))
        files = {"output_dir": output_dir, "result_files_patterns": list(result_files_patterns)}
    else:
        executor = executor_class(use_namespaces=False)
        files = {}
    run = ExecutorRun(executor, mode.kills_process_group)
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
    returncode = -exit_code.signal if exit_code.signal else exit_code.value
    with open(log_file, errors="replace") as log:
        output = log.read()
    cpu = result.get("cputime")
    wall = result.get("walltime")
    return FinishedRun(command, returncode, output, mode,
                       cpu_time=None if cpu is None else float(cpu),
                       termination_reason=result.get("terminationreason"),
                       wall_time=None if wall is None else float(wall),
                       memory=result.get("memory"))
