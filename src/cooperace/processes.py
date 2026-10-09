import os
import signal
import threading
import time


# Seconds a component's process group has to exit after SIGTERM before
# stopProcessGroups sends it SIGKILL.
STOP_GRACE_SECONDS = 1.0


def processExited(pid):
    """Whether process `pid` has exited, that is, is gone or a zombie, read
    from /proc. A zombie counts as exited because the thread waiting for it in
    actorResult may not have reaped it yet."""
    try:
        with open(f"/proc/{pid}/stat") as stat_file:
            stat = stat_file.read()
    except OSError:
        return True
    return stat[stat.rindex(")") + 2] == "Z"


def stopProcessGroups(processes):
    """Ends each of `processes`, which actorResult started as leaders of new
    sessions, together with every other process of its process group: SIGTERM
    to each group, up to STOP_GRACE_SECONDS for the leaders to exit, then
    SIGKILL to each group. The SIGKILL also ends descendants that are still
    running after their leader has exited, such as the JVMs that
    Dartagnan-SVCOMP.sh and Ultimate.py start. A descendant that starts a
    session of its own is not reached. Does not reap the leaders; the threads
    waiting in actorResult do."""
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
    """The component processes started under one runParallel call, and the
    groups of the runParallel calls nested in it. Cooperace.root_group holds
    the components started outside any runParallel. Once stop() is called,
    actorResult starts no further component in the group or its subgroups."""

    def __init__(self, parent=None):
        self.lock = threading.Lock()
        self.stopped = False
        self.processes = set()
        self.subgroups = []
        if parent is not None:
            parent.addSubgroup(self)

    def addSubgroup(self, group):
        with self.lock:
            self.subgroups.append(group)
            stopped = self.stopped
        if stopped:
            group.stop()

    def add(self, process):
        """Records a running component process. Returns False, and records
        nothing, if the group is already stopped."""
        with self.lock:
            if self.stopped:
                return False
            self.processes.add(process)
            return True

    def remove(self, process):
        with self.lock:
            self.processes.discard(process)

    def markStopped(self):
        """Marks this group and its subgroups stopped and returns the processes
        running in them."""
        with self.lock:
            self.stopped = True
            processes = list(self.processes)
            subgroups = list(self.subgroups)
        for group in subgroups:
            processes += group.markStopped()
        return processes

    def stop(self):
        """Stops every component running in this group or its subgroups with
        stopProcessGroups and keeps further ones from starting."""
        stopProcessGroups(self.markStopped())


def run_memory_limit(cgroup_file="/proc/self/cgroup", cgroup_root="/sys/fs/cgroup"):
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
