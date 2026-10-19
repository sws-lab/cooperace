# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The components that run together and are stopped together
(ComponentGroup), and the memory limit of the run's cgroup
(run_memory_limit), which a percentage in the conf's memoryLimits is a
share of. Uses only the standard library."""
from __future__ import annotations

import os
import threading


class ComponentGroup:
    """Components that are stopped together, and the groups nested in it
    (its subgroups). strategy.execute is given the outermost group. Once
    stop() is called, no further component starts in the group or its
    subgroups.

    A running component is recorded in `processes` as an object with a
    method stop() that ends it with every process it started, the
    execution.ExecutorRun of its RunExecutor."""

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

    def add(self, process) -> bool:
        """Records a running component, an object with a method stop().
        Returns False, and records nothing, if the group is already
        stopped."""
        with self.lock:
            if self.stopped:
                return False
            self.processes.add(process)
            return True

    def remove(self, process) -> None:
        with self.lock:
            self.processes.discard(process)

    def markStopped(self) -> list:
        """Marks this group and its subgroups stopped and returns the
        components running in them, as add recorded them."""
        with self.lock:
            self.stopped = True
            processes = list(self.processes)
            subgroups = list(self.subgroups)
        for group in subgroups:
            processes += group.markStopped()
        return processes

    def stop(self) -> None:
        """Stops every component running in this group or its subgroups, by
        calling its stop(), and keeps further ones from starting."""
        for component in self.markStopped():
            component.stop()


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
