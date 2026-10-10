# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Per-component resource limits: which limits announce_limits passes on and
prints for a Mode with and without cgroups, component_memory_limit,
component_cpu_time_limit and run_memory_limit. That the limits end a run is
tested with real runs in test_execution.py and test_execute.py."""
import inspect

import pytest

from src.cooperace import components
from src.cooperace.config import Step
from src.cooperace.execution import Mode
from src.cooperace.processes import run_memory_limit

MEBIBYTE = 2**20

WITH_CGROUPS = Mode(container=True, memory=True, cpu_time=True, freezer=True)
WITHOUT_CGROUPS = Mode(container=False, memory=False, cpu_time=False, freezer=False)


def goblint(memory=None, cpu=None):
    """A Step of Goblint with the memory and CPU-time limits a conf gives."""
    return Step("Goblint", "all", memory_limit=memory, cpu_time_limit=cpu)


# --- announce_limits ----------------------------------------------------------

def test_no_limits_print_nothing(capsys):
    assert components.announce_limits(goblint(), WITH_CGROUPS) == (None, None)
    assert capsys.readouterr().out == ""


def test_limits_the_mode_can_apply_are_returned_and_printed_with_their_cgroup(capsys):
    assert components.announce_limits(goblint(memory=512 * MEBIBYTE, cpu=7), WITH_CGROUPS) == (512 * MEBIBYTE, 7)
    assert capsys.readouterr().out.splitlines() == [
        f"Memory limit of Goblint: {512 * MEBIBYTE} bytes (memory cgroup of its run)",
        "CPU-time limit of Goblint: 7 s (cpu cgroup of its run)"]


def test_limits_the_mode_cannot_apply_are_printed_as_not_applied_and_not_returned(capsys):
    assert components.announce_limits(goblint(memory=512 * MEBIBYTE, cpu=7), WITHOUT_CGROUPS) == (None, None)
    assert capsys.readouterr().out.splitlines() == [
        f"Memory limit of Goblint: {512 * MEBIBYTE} bytes, not applied: no usable memory cgroup",
        "CPU-time limit of Goblint: 7 s, not applied: no usable cpu cgroup"]


def test_each_limit_depends_on_its_own_cgroup(capsys):
    memory_only = Mode(container=True, memory=True, cpu_time=False, freezer=False)

    assert components.announce_limits(goblint(memory=1000, cpu=7), memory_only) == (1000, None)
    assert capsys.readouterr().out.splitlines()[1] == (
        "CPU-time limit of Goblint: 7 s, not applied: no usable cpu cgroup")


def test_a_percentage_resolves_against_the_run_limit(monkeypatch, capsys):
    monkeypatch.setattr(components, "run_memory_limit", lambda: 1000 * MEBIBYTE)

    assert components.announce_limits(goblint(memory="70%"), WITH_CGROUPS) == (700 * MEBIBYTE, None)
    assert capsys.readouterr().out.splitlines() == [
        f"Memory limit of Goblint: {700 * MEBIBYTE} bytes (memory cgroup of its run)"]


# --- component_memory_limit and component_cpu_time_limit -------------------------

@pytest.mark.parametrize("value, run_limit, expected", [
    ("70%", 1000, 700),
    ("12.5%", 1000, 125),
    ("100%", 12345, 12345),
    ("70%", None, None),
    (123456, None, 123456),
    ("123456", 999, 123456),
    (None, 1000, None),
])
def test_component_memory_limit(monkeypatch, value, run_limit, expected):
    monkeypatch.setattr(components, "run_memory_limit", lambda: run_limit)

    assert components.component_memory_limit(goblint(memory=value)) == expected


def test_component_memory_limit_is_none_without_the_key():
    assert components.component_memory_limit(Step("Goblint", "all")) is None


def test_component_cpu_time_limit():
    assert components.component_cpu_time_limit(goblint(cpu=30)) == 30
    assert components.component_cpu_time_limit(goblint(memory=1000)) is None
    assert components.component_cpu_time_limit(Step("Goblint", "all")) is None


# --- run_memory_limit -------------------------------------------------------

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def cgroups(tmp_path):
    """The file that names the process's cgroups and the root of a fake
    cgroup file system, both under tmp_path."""
    root = tmp_path / "fs-cgroup"
    root.mkdir()
    return tmp_path / "cgroup", root


def limit(cgroups):
    cgroup_file, root = cgroups
    return run_memory_limit(cgroup_file=str(cgroup_file), cgroup_root=str(root))


def test_run_memory_limit_defaults_are_the_real_paths():
    parameters = inspect.signature(run_memory_limit).parameters
    assert parameters["cgroup_file"].default == "/proc/self/cgroup"
    assert parameters["cgroup_root"].default == "/sys/fs/cgroup"


def test_v2_reads_memory_max_of_the_cgroup(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/run/job\n")
    write(root / "run/job/memory.max", "1073741824\n")

    assert limit(cgroups) == 1073741824


def test_v2_max_means_no_limit(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/run/job\n")
    write(root / "run/job/memory.max", "max\n")
    write(root / "run/memory.max", "max\n")

    assert limit(cgroups) is None


def test_v2_takes_the_minimum_over_the_ancestors(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/a/b/c\n")
    write(root / "a/b/c/memory.max", "3000\n")
    write(root / "a/b/memory.max", "1000\n")
    write(root / "a/memory.max", "max\n")
    write(root / "memory.max", "2000\n")

    assert limit(cgroups) == 1000


def test_v2_takes_a_limit_set_only_on_an_ancestor(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/a/b\n")
    write(root / "a/b/memory.max", "max\n")
    write(root / "a/memory.max", "4096\n")

    assert limit(cgroups) == 4096


def test_v2_in_the_root_cgroup(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/\n")
    write(root / "memory.max", "5000\n")

    assert limit(cgroups) == 5000


def test_v2_ignores_missing_files(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/a/b\n")
    write(root / "a/memory.max", "7000\n")

    assert limit(cgroups) == 7000


def test_v1_reads_memory_limit_in_bytes_of_the_memory_cgroup(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "12:cpu,cpuacct:/other\n4:memory:/job\n1:name=systemd:/job\n")
    write(root / "memory/job/memory.limit_in_bytes", "2048\n")
    write(root / "cpu,cpuacct/other/memory.limit_in_bytes", "1\n")

    assert limit(cgroups) == 2048


def test_v1_finds_memory_among_several_controllers(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "5:cpu,memory:/job\n")
    write(root / "memory/job/memory.limit_in_bytes", "2048\n")

    assert limit(cgroups) == 2048


def test_v1_no_limit_value_means_no_limit(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "4:memory:/job\n")
    write(root / "memory/job/memory.limit_in_bytes", "9223372036854771712\n")
    write(root / "memory/memory.limit_in_bytes", "9223372036854771712\n")

    assert limit(cgroups) is None


def test_v1_takes_the_minimum_over_the_ancestors(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "4:memory:/a/b\n")
    write(root / "memory/a/b/memory.limit_in_bytes", "9223372036854771712\n")
    write(root / "memory/a/memory.limit_in_bytes", "6000\n")
    write(root / "memory/memory.limit_in_bytes", "8000\n")

    assert limit(cgroups) == 6000


def test_the_threshold_between_a_limit_and_no_limit_is_two_to_the_60(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "0::/a\n")
    write(root / "a/memory.max", f"{2**60}\n")
    assert limit(cgroups) is None

    write(root / "a/memory.max", f"{2**60 - 1}\n")
    assert limit(cgroups) == 2**60 - 1


def test_hybrid_hierarchy_takes_the_minimum_of_v1_and_v2(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "4:memory:/job\n0::/job\n")
    write(root / "memory/job/memory.limit_in_bytes", "9000\n")
    write(root / "job/memory.max", "3000\n")

    assert limit(cgroups) == 3000


def test_a_cgroup_without_memory_controller_or_files_gives_none(cgroups):
    cgroup_file, root = cgroups
    write(cgroup_file, "3:cpu:/job\n")
    write(root / "job/memory.max", "1000\n")

    assert limit(cgroups) is None


def test_a_missing_cgroup_file_gives_none(tmp_path):
    assert run_memory_limit(cgroup_file=str(tmp_path / "absent"),
                            cgroup_root=str(tmp_path)) is None
