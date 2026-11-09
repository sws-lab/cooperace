# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component under BenchExec's benchexec.runexecutor.RunExecutor
in its container (execute_run), which is how CoOpeRace runs every component:
stop() kills the container with every process in it, the component's
directory is an overlay (container_dir_modes), and the memory, CPU-time and
task limits are limits of the run's cgroup, for all its processes together.

The container needs user namespaces and the limits cgroups v2, which a
process has inside the container of runexec or benchexec, as in the
competition; on Ubuntu 24.04, AppArmor refuses user namespaces to python3
from a shell. container_works says whether the container can be made, and
cli.run starts no component where it cannot. BenchExec logs to stderr
through `logging`. components.py imports this module only when a component
runs, since benchexec.runexecutor needs Linux. container_works and
execute_run first call guard_clone, which keeps the start of a container from
deadlocking CoOpeRace's process."""
from __future__ import annotations

import functools
import gc
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence

from benchexec import container, runexecutor
from benchexec.cgroups import Cgroups
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY

from .processes import ComponentGroup

# Seconds between two checks whether a running run has to be stopped.
POLL_SECONDS = 0.1

# Wall-time seconds after which execute_run gives a run up (ComponentHang):
# a run whose RunExecutor has not started the component's process this long
# after execute_run began, and a run that is still alive this long after a
# stop was requested. BenchExec's RunExecutor can hang while it sets up or
# takes down its container, with no process of the component to kill; a
# component given up is a step without a verdict, and the run goes on.
START_TIMEOUT_SECONDS = 60.0
STOP_GRACE_SECONDS = 30.0

# The cgroup controller whose file pids.max limits the number of tasks
# (processes and threads) of a run.
PIDS = "pids"

# The switch interval in seconds that guard_clone gives CoOpeRace's process:
# a thread asks the thread that holds the GIL to give it up only after it has
# waited this long. BenchExec's own benchexec sets the same while its runs
# start containers in parallel threads without its native clone callback
# (benchexec.localexecution, BenchExec issue 435).
SWITCH_INTERVAL_SECONDS = 1000.0

# Held by the execute_in_namespace of guard_clone while it makes a container.
_CLONE_LOCK = threading.Lock()


def guard_clone() -> None:
    """Keeps the start of a BenchExec container from deadlocking CoOpeRace's
    process, as it did in two test runs of E7 in the competition image
    (Python 3.14), which then ended by the wall-time limit without a verdict
    and without one more line of output.

    RunExecutor makes the container with clone(), which
    benchexec.container.execute_in_namespace calls through ctypes between
    PyOS_BeforeFork() and PyOS_AfterFork_Parent(), with Python code in
    between (clone's errcheck). Since Python 3.13, PyOS_BeforeFork takes the
    lock of the interpreter's list of threads until PyOS_AfterFork_Parent
    releases it (Python 3.12 and earlier do not), and a thread that makes,
    starts or ends a threading.Thread, or frees a finished one, waits for
    that lock holding the GIL. So the process deadlocks if, in between, the
    thread that clones gives the GIL to such a thread, because a thread has
    waited for the GIL longer than the switch interval (5 ms by default;
    more likely where the OS preempts the cloning thread, as on a busy
    core), or if a garbage collection frees a finished Thread there, in the
    thread that holds the lock. No thread of the process runs again, so
    neither execute_run's hang guard nor a phase's wall-time limit ends
    anything.

    This sets the process's switch interval to SWITCH_INTERVAL_SECONDS, for
    good, and replaces execute_in_namespace with one that makes one container
    at a time, with automatic garbage collection off. A thread that waits for
    the GIL with the old interval can still ask for it once, so container_works
    calls this first, in the main thread, before CoOpeRace starts any other
    thread; later calls change nothing. CoOpeRace's threads, and
    RunExecutor's, mostly wait, which gives the GIL up, so the long interval
    delays them only while another thread runs Python code without waiting."""
    sys.setswitchinterval(max(sys.getswitchinterval(), SWITCH_INTERVAL_SECONDS))
    unguarded = container.execute_in_namespace
    if getattr(unguarded, "guarded", False):
        return

    def execute_in_namespace(func, use_network_ns=True):
        with _CLONE_LOCK:
            collecting = gc.isenabled()
            gc.disable()
            try:
                return unguarded(func, use_network_ns=use_network_ns)
            finally:
                if collecting:
                    gc.enable()

    execute_in_namespace.guarded = True
    container.execute_in_namespace = execute_in_namespace


def task_limit_supported() -> bool:
    """Whether a RunExecutor can limit the tasks of a run here: whether the
    cgroup in which BenchExec makes the runs' cgroups has the pids
    controller, usable as RunExecutor requires it (Cgroups.require_subsystem,
    which under cgroups v1 makes a cgroup of it to find out, and logs at
    debug level why not), with its file pids.max. RunExecutor finds that
    cgroup with Cgroups.initialize, and refuses to be made, with SystemExit,
    when a controller it is asked for is not usable, so execute_run limits
    the tasks only where this is true (and runs the component once more
    without the limit where BenchExec refuses it all the same). False where
    the cgroups cannot be read."""
    try:
        cgroups = Cgroups.initialize()
        return cgroups.require_subsystem(PIDS, log_method=logging.debug) and cgroups.has_value(PIDS, "max")
    except Exception:  # noqa: BLE001 - no limit rather than no run
        return False


def container_dir_modes(tool_directory: str, writable_dirs: Sequence[str] = (),
                        input_files: Sequence[str] = ()) -> dict[str, str]:
    """The directory modes of a component's container: `/` read-only and
    `/home` hidden, as the competition gives a tool, `/run` and `/tmp`
    hidden, as BenchExec does by default, `tool_directory` an overlay,
    the directory of each of `input_files` (the task and the property file)
    read-only, and `writable_dirs` with full access, so that what the
    component writes there is visible outside.

    The input directories must be visible: in the competition the run's
    directory, which holds both CoOpeRace and the tasks, is under `/home`
    (`/home/cwd`, with the tasks in `/home/cwd/sv-benchmarks`), so hiding
    `/home` alone hides the task and the property file from every component.
    A task's directory, not only its file, is made visible, because a task
    may include files beside it. A directory that already has a mode keeps
    it."""
    modes = {"/": DIR_READ_ONLY, "/run": DIR_HIDDEN, "/tmp": DIR_HIDDEN, "/home": DIR_HIDDEN}
    for path in input_files:
        directory = os.path.dirname(os.path.abspath(path))
        if directory != "/":
            modes[directory] = DIR_READ_ONLY
    modes[tool_directory] = DIR_OVERLAY
    modes.update(dict.fromkeys(writable_dirs, DIR_FULL_ACCESS))
    return modes


def container_works(tools_dir: str) -> bool:
    """Whether RunExecutor runs `true` in its container with `tools_dir` (its
    parent if it does not exist) as overlay and working directory. Where it
    cannot, BenchExec says why on stderr. Calls guard_clone first."""
    guard_clone()
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
    refused it. `refused` is true where BenchExec refused the run before the
    component's process started: a SystemExit of RunExecutor, when it is
    made or when it sets the run up, as for a cgroup value it cannot set."""

    def __init__(self, message: str, refused: bool = False):
        super().__init__(message)
        self.refused = refused


class ComponentHang(RunExecutorError):
    """execute_run gave a run up: its RunExecutor did not start the
    component's process within START_TIMEOUT_SECONDS ("start timed out"), or
    the run did not end within STOP_GRACE_SECONDS after a stop ("did not
    stop"). `status` is the component's status, "ERROR (<reason>)"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.status = f"ERROR ({reason})"


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
        self.has_started = False

    def started(self) -> bool:
        """Whether the executor has started the component's process, now or
        at an earlier call."""
        with self.executor.SUB_PROCESS_PIDS_LOCK:
            if self.executor.SUB_PROCESS_PIDS:
                self.has_started = True
        return self.has_started

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
                output_dir: str | None = None, result_files_patterns: Sequence[str] = (),
                tasks: int | None = None) -> FinishedRun:
    """Runs `command` in `cwd` under a RunExecutor in its container, in the
    ComponentGroup `group`, and returns its FinishedRun, not started if
    `group` was stopped first (_execute_once).

    The run's environment is what `environments` (a tool-info module's
    environment()) makes of CoOpeRace's, with HOME and TMPDIR of its own;
    its directory modes are `dir_modes`, its limits `memory` bytes,
    `cpu_time` seconds and `tasks` tasks, the pids.max of its cgroup, which
    counts every thread of every process of the run (None: none; `tasks` is
    ignored where task_limit_supported is false). A process of the run that
    would exceed `tasks` cannot fork or start a thread (EAGAIN); nothing is
    killed. If BenchExec refuses the run with the task limit before the
    component's process starts (RunExecutorError.refused: a pids controller
    it cannot use, or a pids.max it cannot write), the component runs once
    more without the limit, after a line "CoOpeRace: ..." on stderr that
    says why, as where the limit is not supported: one component without a
    limit rather than every component without a verdict. Its output goes to
    `log_file`, and the
    files matching `result_files_patterns` under `cwd` are moved to
    `output_dir`.

    The run executes in a daemon thread of its own, which this thread waits
    for, calling ExecutorRun.kill every POLL_SECONDS once a stop is
    requested. It gives the run up, raising ComponentHang, if the executor has
    not started the component's process START_TIMEOUT_SECONDS after this call
    began, or if the run is still alive STOP_GRACE_SECONDS after the stop was
    first seen; a daemon thread then goes on calling ExecutorRun.kill until
    the run's thread ends, so that a process that starts late is still
    killed. An exception in this thread while it waits (strategy.StopSignal)
    stops the run, waits for it at most STOP_GRACE_SECONDS and is re-raised.
    Raises RunExecutorError if BenchExec could not start the run or refused
    it (also when RunExecutor is made). Calls guard_clone first: the hang
    guard cannot end a deadlock of the whole process."""
    guard_clone()
    cgroup_values = {}
    if tasks is not None and task_limit_supported():
        cgroup_values[(PIDS, "max")] = str(tasks)
    attempt = functools.partial(_execute_once, command, cwd, environments, group, log_file, dir_modes, memory,
                                cpu_time, output_dir, result_files_patterns)
    if cgroup_values:
        try:
            return attempt(cgroup_values)
        except RunExecutorError as error:
            if not error.refused:
                raise
            print(f"CoOpeRace: running {command[0]} once more without its limit of {tasks} tasks: "
                  f"{' '.join(str(error).split())}", file=sys.stderr, flush=True)
    return attempt({})


def _execute_once(command: list[str], cwd: str, environments: Mapping, group: ComponentGroup, log_file: str,
                  dir_modes: Mapping[str, str], memory: int | None, cpu_time: int | None, output_dir: str | None,
                  result_files_patterns: Sequence[str], cgroup_values: Mapping) -> FinishedRun:
    """execute_run's run of `command` with the cgroup values `cgroup_values`
    (a task limit or none), not started if `group` is stopped. A SystemExit
    of RunExecutor, when it is made or while it sets the run up, raises
    RunExecutorError with `refused` true, unless the component's process had
    started."""
    if group.stopped:
        return FinishedRun(command, None, "")
    try:
        executor = runexecutor.RunExecutor(use_namespaces=True, dir_modes=dict(dir_modes),
                                           additional_cgroup_subsystems=[PIDS] if cgroup_values else [])
    except SystemExit as error:
        raise RunExecutorError(f"BenchExec refused to run {command[0]}: {error}", refused=True) from None
    run = ExecutorRun(executor)
    if not group.add(run):
        return FinishedRun(command, None, "")

    outcome = {}

    def execute():
        try:
            outcome["result"] = executor.execute_run(
                list(command), log_file, write_header=False, workingDir=cwd,
                environments=dict(environments), memlimit=memory, hardtimelimit=cpu_time,
                output_dir=output_dir, result_files_patterns=list(result_files_patterns),
                cgroupValues=dict(cgroup_values))
        except SystemExit as error:
            outcome["error"] = RunExecutorError(f"BenchExec refused to run {command[0]}: {error}",
                                                refused=not run.started())
        except BaseException as error:  # noqa: B036 - re-raised in the waiting thread
            outcome["error"] = error

    worker = threading.Thread(target=execute, name=f"RunExecutor run of {command[0]}", daemon=True)
    began = time.monotonic()
    stop_seen = None
    try:
        worker.start()
        while True:
            worker.join(POLL_SECONDS)
            if not worker.is_alive():
                break
            now = time.monotonic()
            if run.stop_requested.is_set():
                run.kill()
                if stop_seen is None:
                    stop_seen = now
                elif now - stop_seen >= STOP_GRACE_SECONDS:
                    raise ComponentHang("did not stop")
            elif not run.started() and now - began >= START_TIMEOUT_SECONDS:
                run.stop()
                raise ComponentHang("start timed out")
    except ComponentHang:
        _keep_killing(run, worker)
        raise
    except BaseException:
        run.stop_requested.set()
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while worker.is_alive() and time.monotonic() < deadline:
            run.kill()
            worker.join(POLL_SECONDS)
        if worker.is_alive():
            _keep_killing(run, worker)
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


def _keep_killing(run: ExecutorRun, worker: threading.Thread) -> None:
    """Calls run.kill every POLL_SECONDS in a daemon thread until `worker`,
    the thread of a run that execute_run gave up, ends."""

    def loop():
        while worker.is_alive():
            run.kill()
            worker.join(POLL_SECONDS)

    threading.Thread(target=loop, name=f"{worker.name}, given up", daemon=True).start()
