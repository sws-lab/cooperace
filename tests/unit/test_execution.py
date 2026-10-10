# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Running a component under RunExecutor (execution.py): execute_run with a
fake RunExecutor (the stopping and the result), the Mode and its detection,
real runs without cgroups and without the container (RunExecutorWithoutCgroups,
which works on any Linux), and cli.run end to end with stub components in the
Mode found here, whose limit tests are skipped where it has no cgroup for
them. On Ubuntu 24.04 the Mode found from a shell has cgroups if pystemd can
be imported, and has the container only inside BenchExec's own, for example

    runexec --read-only-dir / --overlay-dir ./ --hidden-dir /home/ -- python3 -m pytest tests/unit"""
import os
import shlex
import signal
import sys
import threading
import time
from pathlib import Path

import pytest
from benchexec import util as butil
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY
from support import make_script, register_stub, run_command, wait_until
from test_execute import Run, mode_here

from src.cooperace import execution
from src.cooperace.components import ComponentRunner, run_lines
from src.cooperace.execution import Mode
from src.cooperace.processes import ComponentGroup

WITH_CGROUPS = Mode(container=True, memory=True, cpu_time=True, freezer=True)
WITHOUT_ANYTHING = Mode(container=False, memory=False, cpu_time=False, freezer=False)


# --- execute_run with a fake RunExecutor -------------------------------------


class FakeExecutor:
    """Stands in for benchexec.runexecutor.RunExecutor. execute_run writes
    `output` to the log file and returns `result`; with `block`, it first
    waits, until stop() is called after the run's process was registered,
    as the real one waits for its process. The process is registered after
    `start_delay` seconds, so that a stop() before that does not end it."""

    instances = []

    def __init__(self, use_namespaces=True, dir_modes=None, output="out\n", result=None, block=False,
                 start_delay=0.0):
        self.use_namespaces = use_namespaces
        self.dir_modes = dir_modes
        self.output = output
        self.result = result if result is not None else {
            "exitcode": butil.ProcessExitCode.create(value=0), "cputime": 1.5, "walltime": 2.0, "memory": 1000}
        self.block = block
        self.start_delay = start_delay
        self.SUB_PROCESS_PIDS_LOCK = threading.Lock()
        self.SUB_PROCESS_PIDS = set()
        self.killed = threading.Event()
        self.stops = 0
        self.stops_before_start = 0
        self.kills = 0
        self.kwargs = None
        FakeExecutor.instances.append(self)

    def stop(self):
        with self.SUB_PROCESS_PIDS_LOCK:
            self.stops += 1
            if self.SUB_PROCESS_PIDS:
                self.kills += 1
                self.killed.set()
            else:
                self.stops_before_start += 1

    def execute_run(self, args, output_filename, **kwargs):
        self.kwargs = kwargs
        Path(output_filename).write_text(self.output)
        time.sleep(self.start_delay)
        with self.SUB_PROCESS_PIDS_LOCK:
            self.SUB_PROCESS_PIDS.add(4711)
        if self.block:
            assert self.killed.wait(15)
            return {"exitcode": butil.ProcessExitCode.create(signal=9), "terminationreason": "killed",
                    "cputime": 0.1, "walltime": 0.2}
        return self.result


@pytest.fixture
def fake_executor(monkeypatch):
    """Makes execute_run use FakeExecutor in place of RunExecutor and
    RunExecutorWithoutCgroups, with the keyword arguments given to the
    returned function, and returns that function."""
    FakeExecutor.instances = []
    settings = {}

    def configure(**kwargs):
        settings.update(kwargs)

    def make(**kwargs):
        return FakeExecutor(**kwargs, **settings)

    monkeypatch.setattr(execution.runexecutor, "RunExecutor", make)
    monkeypatch.setattr(execution, "RunExecutorWithoutCgroups", make)
    return configure


def run_fake(tmp_path, group, mode=WITH_CGROUPS, **kwargs):
    return execution.execute_run(mode, ["tool", "--option"], str(tmp_path), {}, group,
                                 str(tmp_path / "run.log"), {"/": DIR_READ_ONLY}, **kwargs)


def test_execute_run_returns_the_output_and_the_measurements(fake_executor, tmp_path):
    fake_executor(output="line 1\nline 2\n")
    group = ComponentGroup()

    result = run_fake(tmp_path, group, memory=100, cpu_time=5, output_dir="/o", result_files_patterns=["w.yml"])

    assert (result.returncode, result.stdout, result.cpu_time, result.wall_time, result.memory) == (
        0, "line 1\nline 2\n", 1.5, 2.0, 1000)
    assert (result.termination_reason, result.mode) == (None, WITH_CGROUPS)
    kwargs = FakeExecutor.instances[0].kwargs
    assert (kwargs["memlimit"], kwargs["hardtimelimit"], kwargs["workingDir"]) == (100, 5, str(tmp_path))
    assert (kwargs["output_dir"], kwargs["result_files_patterns"], kwargs["write_header"]) == (
        "/o", ["w.yml"], False)
    assert FakeExecutor.instances[0].dir_modes == {"/": DIR_READ_ONLY}
    assert group.processes == set()


def test_without_the_container_execute_run_passes_no_directory_modes_and_no_result_files(
        fake_executor, tmp_path):
    run_fake(tmp_path, ComponentGroup(), mode=Mode(False, True, True, True), output_dir="/o",
             result_files_patterns=["w.yml"])

    executor = FakeExecutor.instances[0]
    assert (executor.use_namespaces, executor.dir_modes) == (False, None)
    assert "output_dir" not in executor.kwargs and "result_files_patterns" not in executor.kwargs


def test_execute_run_gives_a_signal_as_a_negative_returncode(fake_executor, tmp_path):
    fake_executor(result={"exitcode": butil.ProcessExitCode.create(signal=9), "terminationreason": "memory",
                          "cputime": 0.4, "walltime": 0.4, "memory": 512})

    result = run_fake(tmp_path, ComponentGroup())

    assert (result.returncode, result.termination_reason) == (-9, "memory")


def test_execute_run_raises_when_benchexec_could_not_start_the_run(fake_executor, tmp_path):
    fake_executor(result={"terminationreason": "failed"})

    with pytest.raises(execution.RunExecutorError, match="failed"):
        run_fake(tmp_path, ComponentGroup())


def test_execute_run_raises_when_benchexec_refuses_the_run(fake_executor, tmp_path, monkeypatch):
    def refuse(self, args, output_filename, **kwargs):
        sys.exit("Memory limit specified, but cannot be implemented without cgroup support.")

    monkeypatch.setattr(FakeExecutor, "execute_run", refuse)

    with pytest.raises(execution.RunExecutorError, match="refused to run tool: Memory limit specified"):
        run_fake(tmp_path, ComponentGroup())


def test_execute_run_starts_nothing_in_a_stopped_group(fake_executor, tmp_path):
    group = ComponentGroup()
    group.stop()

    result = run_fake(tmp_path, group)

    assert result.returncode is None and result.stdout == ""
    assert FakeExecutor.instances == []


def test_stopping_the_group_kills_the_run(fake_executor, tmp_path):
    fake_executor(block=True)
    group = ComponentGroup()
    results = []
    thread = threading.Thread(target=lambda: results.append(run_fake(tmp_path, group)))
    thread.start()
    while not FakeExecutor.instances or not FakeExecutor.instances[0].SUB_PROCESS_PIDS:
        time.sleep(0.01)

    group.stop()
    thread.join(15)

    assert results[0].termination_reason == "killed"
    assert FakeExecutor.instances[0].killed.is_set()
    # One stop() that finds the process: RunExecutor logs one "Killing process"
    assert FakeExecutor.instances[0].kills == 1
    assert group.processes == set()


def test_a_stop_before_the_process_starts_is_repeated_until_it_kills_it(fake_executor, tmp_path):
    # RunExecutor.stop() before execute_run has registered the process does
    # not keep it from running; execute_run calls stop() again.
    fake_executor(block=True, start_delay=0.5)
    group = ComponentGroup()
    results = []
    thread = threading.Thread(target=lambda: results.append(run_fake(tmp_path, group)))
    thread.start()
    while not FakeExecutor.instances:
        time.sleep(0.01)

    group.stop()
    thread.join(15)

    executor = FakeExecutor.instances[0]
    assert executor.stops_before_start >= 1 and executor.killed.is_set() and executor.kills == 1
    assert results[0].termination_reason == "killed"


# --- Mode, detect_mode, container_dir_modes and run_lines ----------------------

def test_container_dir_modes():
    modes = execution.container_dir_modes("/tools/x", ["/work/run"])

    assert modes == {"/": DIR_READ_ONLY, "/run": DIR_HIDDEN, "/tmp": DIR_HIDDEN, "/home": DIR_HIDDEN,
                     "/tools/x": DIR_OVERLAY, "/work/run": DIR_FULL_ACCESS}


def test_mode_description_and_cgroups():
    assert WITH_CGROUPS.description() == "RunExecutor with its container, with cgroups (memory, cpu, freezer)"
    assert WITHOUT_ANYTHING.description() == "RunExecutor without its container, without cgroups"
    assert Mode(False, False, True, True).description() == (
        "RunExecutor without its container, with cgroups (cpu, freezer)")
    assert (WITH_CGROUPS.cgroups, WITHOUT_ANYTHING.cgroups, Mode(True, True, False, False).cgroups) == (
        True, False, True)


@pytest.mark.parametrize("mode, kills_group", [
    (WITHOUT_ANYTHING, True), (WITH_CGROUPS, False), (Mode(True, False, False, False), False),
    (Mode(False, True, True, True), False)])
def test_only_a_run_without_the_container_and_the_freezer_kills_its_process_group(
        fake_executor, tmp_path, monkeypatch, mode, kills_group):
    killed = []
    monkeypatch.setattr(execution.os, "killpg", lambda pid, signum: killed.append((pid, signum)))
    fake_executor(block=True)
    group = ComponentGroup()
    thread = threading.Thread(target=lambda: run_fake(tmp_path, group, mode=mode))
    thread.start()
    assert wait_until(lambda: FakeExecutor.instances and FakeExecutor.instances[0].SUB_PROCESS_PIDS)

    group.stop()
    thread.join(15)

    assert killed == ([(4711, signal.SIGKILL)] if kills_group else [])


class FakeCgroups:
    """Stands in for what benchexec.cgroups.Cgroups.initialize returns, with
    the subsystems `usable`."""

    FREEZE, MEMORY, CPU = "freeze", "memory", "cpu"

    def __init__(self, *usable):
        self.usable = set(usable)

    def require_subsystem(self, subsystem, log_method=None):
        return subsystem in self.usable


@pytest.mark.parametrize("container, usable, expected", [
    (True, ("freeze", "memory", "cpu"), Mode(True, True, True, True)),
    (False, ("freeze", "memory", "cpu"), Mode(False, True, True, True)),
    (True, (), Mode(True, False, False, False)),
    (True, ("memory",), Mode(True, True, False, False)),
    # Without the container and the freezer RunExecutor refuses to run, so no cgroup is used
    (False, ("memory", "cpu"), Mode(False, False, False, False)),
    (False, (), Mode(False, False, False, False)),
])
def test_detect_mode(monkeypatch, container, usable, expected):
    execution.detect_mode.cache_clear()
    monkeypatch.setattr(execution, "container_works", lambda tools_dir: container)
    monkeypatch.setattr(execution.Cgroups, "initialize", staticmethod(lambda: FakeCgroups(*usable)))
    try:
        assert execution.detect_mode("/tools") == expected
        monkeypatch.setattr(execution, "container_works", lambda tools_dir: not container)
        assert execution.detect_mode("/tools") == expected  # found once
    finally:
        execution.detect_mode.cache_clear()


def test_run_lines():
    run = execution.FinishedRun(["x"], -9, "", WITH_CGROUPS, cpu_time=3.061, termination_reason="cputime",
                                wall_time=3.07, memory=4096)
    assert run_lines("X", run) == [
        f"Tool name: X Mode: {WITH_CGROUPS.description()}",
        "Tool name: X Measured: cputime=3.06s walltime=3.07s memory=4096B terminationreason=cputime"]
    ended = execution.FinishedRun(["x"], 0, "", WITHOUT_ANYTHING, wall_time=1.0)
    assert run_lines("X", ended)[1].endswith("cputime=- walltime=1.00s memory=- terminationreason=none")
    assert run_lines("X", execution.FinishedRun(["x"], None, "", WITH_CGROUPS)) == []


# --- real runs without cgroups and without the container -------------------------
#
# RunExecutorWithoutCgroups runs on any Linux, so these test the Mode with
# neither (Docker without cgroup access, say) also where another Mode is found.

def test_a_run_without_cgroups_and_container_measures_no_cpu_time_and_memory(tmp_path):
    result = run_command(["sh", "-c", "pwd; echo err >&2; exit 3"], tmp_path, ComponentGroup(),
                         mode=WITHOUT_ANYTHING)

    assert (result.returncode, result.stdout) == (3, f"{tmp_path}\nerr\n")
    assert (result.cpu_time, result.memory, result.termination_reason) == (None, None, None)
    assert result.wall_time is not None


def test_a_stopped_run_without_cgroups_and_container_ends_its_process_group(tmp_path):
    marker = tmp_path / "escaped"
    script = make_script(tmp_path / "stub", f"(sleep 2; touch {shlex.quote(str(marker))}) &\nexec sleep 60\n")
    group = ComponentGroup()
    results = []
    thread = threading.Thread(target=lambda: results.append(
        run_command([str(script)], tmp_path, group, mode=WITHOUT_ANYTHING)))
    thread.start()
    assert wait_until(lambda: group.processes)
    time.sleep(0.5)

    group.stop()
    thread.join(15)

    assert results[0].returncode == -signal.SIGKILL
    assert results[0].termination_reason == "killed"
    time.sleep(2.5)
    assert not marker.exists()


def test_a_run_without_cgroups_is_given_its_own_home_and_tmpdir(tmp_path):
    result = run_command(["sh", "-c", 'echo "$HOME $TMPDIR"'], tmp_path, ComponentGroup(), mode=WITHOUT_ANYTHING)

    home, tmpdir = result.stdout.split()
    assert home != os.environ.get("HOME") and os.path.basename(home) == "home"
    assert os.path.basename(tmpdir) == "tmp" and not os.path.exists(home)


# --- real runs in the Mode found here --------------------------------------------

BUSY_STUB = f"exec {shlex.quote(sys.executable)} -c 'while True: pass'\n"
ALLOCATING_STUB = f"exec {shlex.quote(sys.executable)} -c 'bytearray(2**30)'\n"


def stub_run(tmp_path, strategy, mode=None, **stubs):
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32", mode=mode)
    for name, body in stubs.items():
        register_stub(runner, name.replace("_", " "), make_script(tmp_path / "stubs" / name, body))
    return Run(runner, strategy)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    directory = tmp_path / "cwd"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory


def test_a_cpu_time_limit_gives_timeout_and_the_next_component_answers(tmp_path, run_dir, capsys):
    if not mode_here().cpu_time:
        pytest.skip(f"no CPU-time limit here: {mode_here().description()}")
    run = stub_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Stub B": "all"}],
                                "cpuTimeLimits": {"Stub A": 1}},
                     Stub_A=BUSY_STUB, Stub_B='echo "STUB-STATUS: true"\n')

    verdict = run.execute()
    lines = capsys.readouterr().out.splitlines()

    assert verdict == "true"
    assert "CPU-time limit of Stub A: 1 s (cpu cgroup of its run)" in lines
    assert "Tool name: Stub A Status: TIMEOUT Exit code: signal 9" in lines
    assert any(line.startswith("Tool name: Stub A Measured: ") and line.endswith("terminationreason=cputime")
               for line in lines)
    assert lines[-1] == "CoOpeRace result from: Stub B"


def test_a_memory_limit_gives_out_of_memory(tmp_path, run_dir, capsys):
    if not mode_here().memory:
        pytest.skip(f"no memory limit here: {mode_here().description()}")
    limit = 128 * 2**20
    run = stub_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Stub B": "all"}],
                                "memoryLimits": {"Stub A": limit}},
                     Stub_A=ALLOCATING_STUB, Stub_B='echo "STUB-STATUS: true"\n')

    verdict = run.execute()
    lines = capsys.readouterr().out.splitlines()

    assert verdict == "true"
    assert f"Memory limit of Stub A: {limit} bytes (memory cgroup of its run)" in lines
    assert "Tool name: Stub A Status: OUT OF MEMORY Exit code: signal 9" in lines


def test_a_parallel_loser_is_stopped_with_a_process_in_a_session_of_its_own(tmp_path, run_dir, capsys):
    if not (mode_here().container or mode_here().freezer):
        pytest.skip(f"only the container or the freezer reach another session: {mode_here().description()}")
    marker = tmp_path / "escaped"
    run = stub_run(
        tmp_path, {"runType": "parallel", "tools": [{"Stub Slow": "all"}, {"Stub Fast": "all"}]},
        # A descendant in a session of its own, which killpg would not reach;
        # it would write the marker after 3 s.
        Stub_Slow=f"setsid sh -c 'sleep 3; touch {shlex.quote(str(marker))}' &\nexec sleep 60\n",
        Stub_Fast='sleep 1\necho "STUB-STATUS: true"\n')

    started = time.monotonic()
    verdict = run.execute()
    elapsed = time.monotonic() - started
    lines = capsys.readouterr().out.splitlines()

    assert verdict == "true"
    assert any(line.startswith("Tool name: Stub Slow Status: stopped by CoOpeRace") for line in lines)
    assert elapsed < 30
    time.sleep(3.5)
    assert not marker.exists()
    assert run.group.processes == set()


@pytest.mark.parametrize("mode", [None, WITHOUT_ANYTHING], ids=["mode-here", "without-anything"])
def test_the_witness_written_in_the_component_directory_is_delivered_and_a_stale_one_is_not(
        tmp_path, run_dir, capsys, mode):
    # A stale witness in the component's directory is neither collected nor
    # touched; the one written in this run is delivered, and the directory
    # itself keeps only what was there (in the container through the
    # overlay, without it by move_new_witness_files).
    body = 'echo new > "$(dirname "$0")/fresh-witness.graphml"\necho "STUB-STATUS: false"\n'
    run = stub_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}]}, mode=mode, Stub_A=body)
    stale = tmp_path / "stubs" / "Stub_A" / "old-witness.yml"
    stale.write_text("stale")
    os.utime(stale, (time.time() + 3600, time.time() + 3600))  # newer than the run: no start time tells it apart

    verdict = run.execute()

    assert verdict == "false"
    assert (run_dir / "witness.graphml").read_text() == "new\n"
    assert not (run_dir / "witness.yml").exists()
    assert stale.read_text() == "stale"
    assert not (tmp_path / "stubs" / "Stub_A" / "fresh-witness.graphml").exists()
