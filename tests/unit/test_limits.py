"""Per-component resource limits: with_resource_limits, with_rlimits,
component_memory_limit and run_memory_limit."""
import inspect
import subprocess
import sys

import pytest

from src.cooperace import components
from src.cooperace.config import Step
from src.cooperace.processes import run_memory_limit, with_rlimits

MEBIBYTE = 2**20

# A command that ends only by a signal from outside. It first makes itself
# non-dumpable (PR_SET_DUMPABLE is 4), because SIGXCPU's default action dumps
# core, and a core_pattern that pipes to a crash reporter makes that take
# seconds.
BUSY_LOOP = [sys.executable, "-c",
             "import ctypes; ctypes.CDLL(None).prctl(4, 0)\nwhile True: pass"]
PRINT_DATA_LIMIT = [sys.executable, "-c",
                    "import resource; print(resource.getrlimit(resource.RLIMIT_DATA))"]


def run_limited(command, cwd, timeout=30):
    """Runs `command` (from with_resource_limits) to its end with its output
    captured."""
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False,
                          timeout=timeout)


def goblint(memory=None, cpu=None):
    """A Step of Goblint with the memory and CPU-time limits a conf gives."""
    return Step("Goblint", "all", memory_limit=memory, cpu_time_limit=cpu)


# --- with_resource_limits -----------------------------------------------------

def test_no_limits_returns_the_command_unchanged(capsys):
    command = ["echo", "hello"]

    assert components.with_resource_limits(goblint(), command) is command
    assert capsys.readouterr().out == ""


def test_cpu_time_limit_ends_a_busy_loop_by_sigxcpu(tmp_path, capsys):
    command = components.with_resource_limits(goblint(cpu=1), BUSY_LOOP)
    result = run_limited(command, tmp_path)

    assert result.returncode == -24
    assert "CPU-time limit of Goblint: 1 s (RLIMIT_CPU)" in capsys.readouterr().out


def test_cpu_time_limit_does_not_touch_a_command_that_ends_by_itself(tmp_path):
    command = components.with_resource_limits(goblint(cpu=5), [sys.executable, "-c", "print('done')"])
    result = run_limited(command, tmp_path)

    assert (result.returncode, result.stdout) == (0, "done\n")


def test_cpu_time_limit_sets_a_hard_limit_one_second_higher(tmp_path):
    code = "import resource; print(resource.getrlimit(resource.RLIMIT_CPU))"

    command = components.with_resource_limits(goblint(cpu=7), [sys.executable, "-c", code])
    result = run_limited(command, tmp_path)

    assert result.stdout.strip() == "(7, 8)"


def test_memory_limit_in_bytes_sets_rlimit_data(tmp_path, capsys):
    limit = 512 * MEBIBYTE

    command = components.with_resource_limits(goblint(memory=limit), PRINT_DATA_LIMIT)
    result = run_limited(command, tmp_path)

    assert result.stdout.strip() == f"({limit}, {limit})"
    assert (f"Memory limit of Goblint: {limit} bytes (RLIMIT_DATA)"
            in capsys.readouterr().out)


def test_memory_limit_makes_a_larger_allocation_fail(tmp_path):
    code = f"bytearray({1024 * MEBIBYTE})"

    command = components.with_resource_limits(goblint(memory=256 * MEBIBYTE), [sys.executable, "-c", code])
    result = run_limited(command, tmp_path)

    assert result.returncode == 1
    assert "MemoryError" in result.stderr


def test_both_limits_apply_and_the_command_keeps_its_arguments(tmp_path):
    code = ("import resource, sys\n"
            "print(resource.getrlimit(resource.RLIMIT_DATA)[0],"
            " resource.getrlimit(resource.RLIMIT_CPU)[0], sys.argv[1:])")

    command = components.with_resource_limits(goblint(memory=512 * MEBIBYTE, cpu=7),
                                      [sys.executable, "-c", code, "a b", "c"])
    result = run_limited(command, tmp_path)

    assert result.stdout.strip() == f"{512 * MEBIBYTE} 7 ['a b', 'c']"


def test_percentage_memory_limit_resolves_against_the_run_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(components, "run_memory_limit", lambda: 1000 * MEBIBYTE)
    expected = 700 * MEBIBYTE

    command = components.with_resource_limits(goblint(memory="70%"), PRINT_DATA_LIMIT)
    result = run_limited(command, tmp_path)

    assert result.stdout.strip() == f"({expected}, {expected})"
    assert (f"Memory limit of Goblint: {expected} bytes (RLIMIT_DATA)"
            in capsys.readouterr().out)


# --- with_rlimits ----------------------------------------------------------

def test_with_rlimits_without_limits_returns_the_command():
    command = ["echo", "hello"]

    assert with_rlimits(command) is command
    assert with_rlimits(command, None, None) is command


def test_with_rlimits_runs_the_command_in_place_of_the_wrapper(tmp_path):
    """The wrapper execs the command, so the command keeps the wrapper's
    process: the session and the process group that run_in_session made."""
    code = "import os; print(os.getpid())"
    command = with_rlimits([sys.executable, "-c", code], cpu=10)

    process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, text=True)
    output, _ = process.communicate(timeout=30)

    assert int(output) == process.pid


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
