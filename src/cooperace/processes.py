"""Starting and stopping the processes of components: each runs as the
leader of a session of its own (run_in_session), under its rlimits
(with_rlimits), recorded in a ComponentGroup that stops the whole process
group of every component in it; and the memory limit of the run's cgroup
(run_memory_limit). Uses only the standard library."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable

# Seconds a component's process group has to exit after SIGTERM before
# stopProcessGroups sends it SIGKILL.
STOP_GRACE_SECONDS = 1.0


def processExited(pid: int) -> bool:
    """Whether process `pid` has exited, that is, is gone or a zombie, read
    from /proc. A zombie counts as exited because the thread waiting for it in
    run_in_session may not have reaped it yet."""
    try:
        with open(f"/proc/{pid}/stat") as stat_file:
            stat = stat_file.read()
    except OSError:
        return True
    return stat[stat.rindex(")") + 2] == "Z"


def stopProcessGroups(processes: Iterable[subprocess.Popen]) -> None:
    """Ends each of `processes`, which run_in_session started as leaders of new
    sessions, together with every other process of its process group: SIGTERM
    to each group, up to STOP_GRACE_SECONDS for the leaders to exit, then
    SIGKILL to each group. The SIGKILL also ends descendants that are still
    running after their leader has exited, such as the JVMs that
    Dartagnan-SVCOMP.sh and Ultimate.py start. A descendant that starts a
    session of its own is not reached. Does not reap the leaders; the threads
    waiting in run_in_session do."""
    def signalGroup(process, signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    for process in processes:
        signalGroup(process, signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and not all(processExited(p.pid) for p in processes):
        time.sleep(0.05)
    for process in processes:
        signalGroup(process, signal.SIGKILL)


class ComponentGroup:
    """The component processes started under one call of
    strategy.run_parallel, and the groups of the calls nested in it. The
    group that strategy.execute is given holds the components started
    outside any run_parallel. Once stop() is called,
    run_in_session starts no further component in the group or its subgroups."""

    def __init__(self, parent: ComponentGroup | None = None):
        self.lock = threading.Lock()
        self.stopped = False
        self.processes = set()
        self.subgroups = []
        if parent is not None:
            parent.addSubgroup(self)

    def addSubgroup(self, group: ComponentGroup) -> None:
        with self.lock:
            self.subgroups.append(group)
            stopped = self.stopped
        if stopped:
            group.stop()

    def add(self, process: subprocess.Popen) -> bool:
        """Records a running component process. Returns False, and records
        nothing, if the group is already stopped."""
        with self.lock:
            if self.stopped:
                return False
            self.processes.add(process)
            return True

    def remove(self, process: subprocess.Popen) -> None:
        with self.lock:
            self.processes.discard(process)

    def markStopped(self) -> list[subprocess.Popen]:
        """Marks this group and its subgroups stopped and returns the processes
        running in them."""
        with self.lock:
            self.stopped = True
            processes = list(self.processes)
            subgroups = list(self.subgroups)
        for group in subgroups:
            processes += group.markStopped()
        return processes

    def stop(self) -> None:
        """Stops every component running in this group or its subgroups with
        stopProcessGroups and keeps further ones from starting."""
        stopProcessGroups(self.markStopped())


def run_memory_limit(cgroup_file: str = "/proc/self/cgroup",
                     cgroup_root: str = "/sys/fs/cgroup") -> int | None:
    """Memory limit in bytes of the cgroup this process runs in, or None.

    BenchExec puts each run into a cgroup with the run's memory limit. Under
    cgroups v2 this is `memory.max` (inside BenchExec's container, the run's
    cgroup is the root of the cgroup namespace); under cgroups v1 it is
    `memory.limit_in_bytes` of the memory cgroup named in /proc/self/cgroup.
    The smallest limit of that cgroup and its ancestors is returned.

    `cgroup_file` is the file that names the process's cgroups and
    `cgroup_root` the directory the cgroup file systems are mounted under (the
    v1 memory controller is `cgroup_root`/memory). The defaults are the real
    ones; the unit tests pass a fake tree.
    """
    limits = []
    try:
        with open(cgroup_file) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        _, controllers, path = line.split(":", 2)
        if controllers == "":
            base, name = cgroup_root, "memory.max"
        elif "memory" in controllers.split(","):
            base, name = os.path.join(cgroup_root, "memory"), "memory.limit_in_bytes"
        else:
            continue
        parts = [p for p in path.split("/") if p]
        for i in range(len(parts), -1, -1):
            try:
                with open(os.path.join(base, *parts[:i], name)) as f:
                    value = f.read().strip()
            except OSError:
                continue
            if value.isdigit() and int(value) < 2**60:  # "max" and v1's 2**63-4096 mean no limit
                limits.append(int(value))
    return min(limits) if limits else None


class FinishedProcess(subprocess.CompletedProcess):
    """A subprocess.CompletedProcess of a component, with `cpu_time`: the CPU
    time in seconds, user and system, that os.wait4 reports for the
    component's process when it is reaped, or None if it was not started.

    That is the CPU time of the component's process and of every descendant
    whose end it, or a descendant it waited for, waited for: the levels of
    Goblint's portfolio runner, the JVM that Ultimate.py or
    Dartagnan-SVCOMP.sh starts. It leaves out a descendant still running, or
    not waited for, when the process ends. It is not the CPU time of one
    process alone, which RLIMIT_CPU limits, nor of the whole session, which
    the cgroup of a BenchExec run would give. resource.getrusage
    (RUSAGE_CHILDREN) would add up every component CoOpeRace has waited for,
    the ones of a parallel stage together."""

    def __init__(self, args, returncode: int | None, stdout: str, stderr: str, cpu_time: float | None = None):
        super().__init__(args, returncode, stdout, stderr)
        self.cpu_time = cpu_time


def run_in_session(command: list[str], cwd: str, group: ComponentGroup,
                   env: dict[str, str] | None = None) -> FinishedProcess:
    """Runs `command` in `cwd`, with the environment `env` (None: this
    process's environment), as the leader of a new session, so that the
    component and every process it starts form one process group, which
    ComponentGroup.stop can end, and records it in the ComponentGroup
    `group` while it runs. Returns a FinishedProcess whose `stdout` is the
    component's standard output and standard error in one, as BenchExec
    captures them, and whose `stderr` is empty; `returncode` is negative if
    the component was ended by a signal, and None if the group was stopped
    before it could start; `cpu_time` is what os.wait4 reports when the
    process is reaped here.

    `group` can be stopped by another thread at any time. If it is stopped
    after the check of `group.stopped` and before `group.add`, the add is
    refused (ComponentGroup.markStopped sets `stopped` under the group's lock
    before any process is signalled, and add checks it under the same lock),
    and the process is stopped here. A process that add records is in the
    list markStopped returns. Either way no component keeps running in a
    stopped group. If this thread (the main thread) is interrupted while it
    waits, by StopSignal, it stops and reaps the process and re-raises."""
    if group.stopped:
        return FinishedProcess(command, None, "", "")
    process = subprocess.Popen(command,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    start_new_session=True
                    )
    try:
        if not group.add(process):
            stopProcessGroups([process])
        #What communicate() does for a single pipe, with os.wait4 in place of
        #wait() for the CPU time; setting returncode keeps Popen from waiting again
        output = process.stdout.read()
        process.stdout.close()
        _pid, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
    except BaseException:
        #StopSignal while this thread (the main thread) waits
        stopProcessGroups([process])
        process.wait()
        raise
    finally:
        group.remove(process)
    return FinishedProcess(command, process.returncode, output, "", usage.ru_utime + usage.ru_stime)




def with_rlimits(command: list[str], memory: int | None = None, cpu: int | None = None) -> list[str]:
    """`command`, started with RLIMIT_DATA set to `memory` bytes and
    RLIMIT_CPU to `cpu` seconds, where a limit that is None is not set;
    `command` itself if both are None.

    RLIMIT_DATA bounds the private writable memory (heap, anonymous mmap) of
    each process of the component; the component's processes inherit it. A
    JVM that reaches it fails to commit memory and exits, so the component
    ends without a verdict and its memory is free for the components still
    running. RLIMIT_AS is not used: a JVM reserves its whole `-Xmx` as
    address space at start and fails to start under it.

    RLIMIT_CPU is set with the hard limit one second higher: the kernel sends
    SIGXCPU to a process that has used the limit and SIGKILL one second
    later. It counts the CPU time of each process on its own, not of the
    component's processes together. Goblint's portfolio runner
    (goblint_runner.py) runs one goblint process per level; when the limit
    ends one, the runner gives up the remaining levels and exits without a
    verdict. Goblint's stage so uses the CPU time of the levels that end by
    themselves plus at most the limit.

    The limits are set by a small Python process that then execs the
    component, because `preexec_fn` is unsafe with the threads of
    the strategy's parallel steps. The exec keeps the process, so the
    component is still the leader of the session run_in_session starts, and
    stopProcessGroups still ends it with every process it starts.
    """
    limits = []
    if memory is not None:
        limits.append(f"RLIMIT_DATA={memory}:{memory}")
    if cpu is not None:
        limits.append(f"RLIMIT_CPU={cpu}:{cpu + 1}")
    if not limits:
        return command
    setter = ("import os, resource, sys\n"
              "for limit in sys.argv[1].split(','):\n"
              "    name, value = limit.split('=')\n"
              "    soft, hard = value.split(':')\n"
              "    resource.setrlimit(getattr(resource, name), (int(soft), int(hard)))\n"
              "os.execvp(sys.argv[2], sys.argv[2:])")
    return [sys.executable, "-c", setter, ",".join(limits)] + command
