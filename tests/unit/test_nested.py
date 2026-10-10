# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The nested runner: nested.run_nested with a fake RunExecutor (the stopping
and the result), the helpers of components.NestedComponentRunner, and, where
BenchExec can run a container here, cli.run end to end with stub components
run as nested BenchExec runs. On Ubuntu 24.04 that is inside BenchExec's own
container, for example

    runexec --read-only-dir / --overlay-dir ./ --hidden-dir /home/ -- python3 -m pytest tests/unit

since AppArmor forbids user namespaces to other programs; elsewhere these
tests are skipped."""
import os
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest
from benchexec import result as bresult
from benchexec import util as butil
from benchexec.container import DIR_FULL_ACCESS, DIR_HIDDEN, DIR_OVERLAY, DIR_READ_ONLY
from support import make_script, register_stub
from test_execute import Run
from test_status import StatusActor

from src.cooperace import nested
from src.cooperace.components import (
    DEFAULT_WITNESS,
    NestedComponentRunner,
    WitnessSpec,
    component_status,
    measurement_line,
    nested_environments,
    nested_result_files_patterns,
    nested_witness_files,
)
from src.cooperace.processes import ComponentGroup, FinishedProcess

# --- run_nested with a fake RunExecutor -------------------------------------


class FakeExecutor:
    """Stands in for benchexec.runexecutor.RunExecutor. execute_run writes
    `output` to the log file and returns `result`; with `block`, it first
    waits, until stop() is called after the run's process was registered,
    as the real one waits for its process. The process is registered after
    `start_delay` seconds, so that a stop() before that does not end it."""

    instances = []

    def __init__(self, use_namespaces=True, dir_modes=None, output="out\n", result=None, block=False,
                 start_delay=0.0):
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
        self.kwargs = None
        FakeExecutor.instances.append(self)

    def stop(self):
        with self.SUB_PROCESS_PIDS_LOCK:
            self.stops += 1
            if self.SUB_PROCESS_PIDS:
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
    """Makes run_nested use FakeExecutor, with the keyword arguments given
    to the returned function, and returns that function."""
    FakeExecutor.instances = []
    settings = {}

    def configure(**kwargs):
        settings.update(kwargs)

    monkeypatch.setattr(nested.runexecutor, "RunExecutor",
                        lambda **kwargs: FakeExecutor(**kwargs, **settings))
    return configure


def run_fake(tmp_path, group, **kwargs):
    return nested.run_nested(["tool", "--option"], str(tmp_path), {}, group, str(tmp_path / "run.log"),
                             {"/": DIR_READ_ONLY}, **kwargs)


def test_run_nested_returns_the_output_and_the_measurements(fake_executor, tmp_path):
    fake_executor(output="line 1\nline 2\n")
    group = ComponentGroup()

    result = run_fake(tmp_path, group, memory=100, cpu_time=5, output_dir="/o", result_files_patterns=["w.yml"])

    assert (result.returncode, result.stdout, result.cpu_time, result.wall_time, result.memory) == (
        0, "line 1\nline 2\n", 1.5, 2.0, 1000)
    assert result.termination_reason is None
    kwargs = FakeExecutor.instances[0].kwargs
    assert (kwargs["memlimit"], kwargs["hardtimelimit"], kwargs["workingDir"]) == (100, 5, str(tmp_path))
    assert (kwargs["output_dir"], kwargs["result_files_patterns"], kwargs["write_header"]) == (
        "/o", ["w.yml"], False)
    assert group.processes == set()


def test_run_nested_gives_a_signal_as_a_negative_returncode(fake_executor, tmp_path):
    fake_executor(result={"exitcode": butil.ProcessExitCode.create(signal=9), "terminationreason": "memory",
                          "cputime": 0.4, "walltime": 0.4, "memory": 512})

    result = run_fake(tmp_path, ComponentGroup())

    assert (result.returncode, result.termination_reason) == (-9, "memory")


def test_run_nested_raises_when_benchexec_could_not_start_the_run(fake_executor, tmp_path):
    fake_executor(result={"terminationreason": "failed"})

    with pytest.raises(nested.NestedRunError, match="failed"):
        run_fake(tmp_path, ComponentGroup())


def test_run_nested_starts_nothing_in_a_stopped_group(fake_executor, tmp_path):
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
    assert group.processes == set()


def test_a_stop_before_the_process_starts_is_repeated_until_it_kills_it(fake_executor, tmp_path):
    # RunExecutor.stop() before execute_run has registered the process does
    # not keep it from running; run_nested calls stop() again.
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
    assert executor.stops_before_start >= 1 and executor.killed.is_set()
    assert results[0].termination_reason == "killed"


# --- the helpers of NestedComponentRunner -------------------------------------

def test_container_dir_modes():
    modes = nested.container_dir_modes("/tools/x", ["/work/run"])

    assert modes == {"/": DIR_READ_ONLY, "/run": DIR_HIDDEN, "/tmp": DIR_HIDDEN, "/home": DIR_HIDDEN,
                     "/tools/x": DIR_OVERLAY, "/work/run": DIR_FULL_ACCESS}


def test_nested_environments():
    assert nested_environments(None) == {}
    assert nested_environments({"A": "1"}) == {"keepEnv": {}, "newEnv": {"A": "1"}}


def test_nested_result_files_patterns():
    assert nested_result_files_patterns(WitnessSpec("run", ("--w", "{dir}"), ("witness.yml",))) == []
    assert nested_result_files_patterns(WitnessSpec("component", (), ("output/witness.graphml",))) == [
        "output/witness.graphml"]
    patterns = nested_result_files_patterns(DEFAULT_WITNESS)
    assert len(patterns) == 2 and all(p.startswith("**/") for p in patterns)


def test_nested_witness_files(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "witness.graphml").write_text("w")
    (tmp_path / "a-Witness.yml").write_text("w")

    assert nested_witness_files(WitnessSpec("component", (), ("output/witness.graphml", "missing.yml")),
                                str(tmp_path)) == [str(tmp_path / "output" / "witness.graphml")]
    assert sorted(nested_witness_files(DEFAULT_WITNESS, str(tmp_path))) == sorted(
        [str(tmp_path / "output" / "witness.graphml"), str(tmp_path / "a-Witness.yml")])


def test_measurement_line():
    run = nested.NestedFinishedProcess(["x"], -9, "", "", cpu_time=3.061, termination_reason="cputime",
                                       wall_time=3.07, memory=4096)
    assert measurement_line("X", run) == ("Tool name: X Measured: cputime=3.06s walltime=3.07s memory=4096B "
                                          "terminationreason=cputime")
    ended = nested.NestedFinishedProcess(["x"], 0, "", "", cpu_time=1.0, wall_time=None)
    assert measurement_line("X", ended).endswith("walltime=- memory=- terminationreason=none")
    assert measurement_line("X", nested.NestedFinishedProcess(["x"], None, "", "")) is None
    assert measurement_line("X", FinishedProcess(["x"], 0, "", "", 1.0)) is None


@pytest.mark.parametrize("status, returncode, reason, expected", [
    (bresult.RESULT_UNKNOWN, -9, "cputime", bresult.RESULT_TIMEOUT),
    (bresult.RESULT_UNKNOWN, -9, "walltime", bresult.RESULT_TIMEOUT),
    (bresult.RESULT_ERROR, -9, "memory", "OUT OF MEMORY"),
    (bresult.RESULT_UNKNOWN, 0, "memory", "OUT OF MEMORY"),
    (bresult.RESULT_TRUE_PROP, -9, "cputime", bresult.RESULT_TRUE_PROP),
    (bresult.RESULT_UNKNOWN, -9, None, "KILLED BY SIGNAL 9"),
])
def test_component_status_of_a_nested_run(status, returncode, reason, expected):
    run = nested.NestedFinishedProcess(["x"], returncode, "", "", termination_reason=reason)
    assert component_status(StatusActor(status), ["x"], run, termination_reason=reason) == expected


# --- real nested runs ---------------------------------------------------------

def _nested_runs_work():
    """Whether BenchExec can run a container with cgroups here: a nested run
    of `true` ends with exit code 0."""
    try:
        from benchexec import runexecutor
        log = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"cooperace-nested-check-{os.getpid()}.log")
        result = runexecutor.RunExecutor(use_namespaces=True).execute_run(
            ["true"], log, write_header=False, memlimit=64 * 2**20, hardtimelimit=5)
        os.remove(log)
        exit_code = result.get("exitcode")
        return exit_code is not None and exit_code.value == 0
    except BaseException:  # noqa: B036 - SystemExit from BenchExec as well
        return False


_NESTED_RUNS_WORK = None


@pytest.fixture
def nested_runs():
    global _NESTED_RUNS_WORK
    if _NESTED_RUNS_WORK is None:
        _NESTED_RUNS_WORK = _nested_runs_work()
    if not _NESTED_RUNS_WORK:
        pytest.skip("BenchExec cannot run a container with cgroups here; on Ubuntu 24.04, AppArmor forbids "
                    "user namespaces outside BenchExec's own commands: run the tests under runexec")


BUSY_STUB = f"exec {shlex.quote(sys.executable)} -c 'while True: pass'\n"
ALLOCATING_STUB = f"exec {shlex.quote(sys.executable)} -c 'bytearray(2**30)'\n"


def nested_run(tmp_path, strategy, **stubs):
    runner = NestedComponentRunner("/dev/null", "/dev/null", "ILP32")
    for name, body in stubs.items():
        register_stub(runner, name.replace("_", " "), make_script(tmp_path / "stubs" / name, body))
    return Run(runner, strategy)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    directory = tmp_path / "cwd"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory


def test_nested_cpu_time_limit_gives_timeout_and_the_next_component_answers(
        nested_runs, tmp_path, run_dir, capsys):
    run = nested_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Stub B": "all"}],
                                "cpuTimeLimits": {"Stub A": 1}},
                     Stub_A=BUSY_STUB, Stub_B='echo "STUB-STATUS: true"\n')

    verdict = run.execute()
    lines = capsys.readouterr().out.splitlines()

    assert verdict == "true"
    assert "CPU-time limit of Stub A: 1 s (cgroup of its nested run)" in lines
    assert "Tool name: Stub A Status: TIMEOUT Exit code: signal 9" in lines
    assert any(line.startswith("Tool name: Stub A Measured: ") and line.endswith("terminationreason=cputime")
               for line in lines)
    assert lines[-1] == "CoOpeRace result from: Stub B"


def test_nested_memory_limit_gives_out_of_memory(nested_runs, tmp_path, run_dir, capsys):
    limit = 128 * 2**20
    run = nested_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Stub B": "all"}],
                                "memoryLimits": {"Stub A": limit}},
                     Stub_A=ALLOCATING_STUB, Stub_B='echo "STUB-STATUS: true"\n')

    verdict = run.execute()
    lines = capsys.readouterr().out.splitlines()

    assert verdict == "true"
    assert f"Memory limit of Stub A: {limit} bytes (cgroup of its nested run)" in lines
    assert "Tool name: Stub A Status: OUT OF MEMORY Exit code: signal 9" in lines


def test_nested_parallel_loser_is_stopped_with_a_process_in_a_session_of_its_own(
        nested_runs, tmp_path, run_dir, capsys):
    marker = tmp_path / "escaped"
    run = nested_run(
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


def test_nested_witness_of_the_component_directory_comes_from_the_overlay(
        nested_runs, tmp_path, run_dir, capsys):
    # A stale witness in the component's directory is neither collected nor
    # touched; the one written in this run is delivered, and the directory
    # itself keeps only what was there.
    body = 'echo new > "$(dirname "$0")/fresh-witness.graphml"\necho "STUB-STATUS: false"\n'
    run = nested_run(tmp_path, {"runType": "sequential", "tools": [{"Stub A": "all"}]}, Stub_A=body)
    stale = tmp_path / "stubs" / "Stub_A" / "old-witness.yml"
    stale.write_text("stale")
    os.utime(stale, (time.time() + 3600, time.time() + 3600))  # newer than the run: no time filter helps

    verdict = run.execute()

    assert verdict == "false"
    assert (run_dir / "witness.graphml").read_text() == "new\n"
    assert not (run_dir / "witness.yml").exists()
    assert stale.read_text() == "stale"
    assert not (tmp_path / "stubs" / "Stub_A" / "fresh-witness.graphml").exists()
