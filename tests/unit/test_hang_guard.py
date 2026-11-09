# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The hang guard of execution.execute_run and the bounded join of
strategy.run_strategy, with BenchExec's RunExecutor replaced by FakeExecutor:
a run whose RunExecutor never starts the component's process is given up
after execution.START_TIMEOUT_SECONDS ("ERROR (start timed out)"), a run
still alive execution.STOP_GRACE_SECONDS after its stop is given up ("ERROR
(did not stop)"), a run whose RunExecutor has not returned
execution.END_GRACE_SECONDS after its process ended is given up ("ERROR (did
not return after its process ended)"), the run goes on and delivers what was
decided, and a run that starts in time is not affected however long it then
takes.

FakeExecutor runs the stub's script on the host in its directory and moves
the result files as RunExecutor would. The first line of a stub's script
after "#!/bin/sh" may say how the RunExecutor behaves: "# hang-start" (it
never starts the process and never returns), "# hang-stop" (it starts the
process, ignores stop() and never returns), "# hang-end" (it waits for the
process to end and never returns, still recording it) or "# hang-cleanup"
(the same, after it no longer records the process)."""
import glob
import os
import shutil
import subprocess
import threading
import time

import pytest
from benchexec import util as butil
from support import (
    PASSING_WITNESS,
    data_race_conf,
    make_script,
    one_after_another,
    register_stub,
    side_by_side,
    write_witness,
)

from src.cooperace import cli, execution, strategy, witnesses
from src.cooperace.components import ComponentRunner
from src.cooperace.processes import ComponentGroup

# Never set: what a hanging RunExecutor waits for.
NEVER = threading.Event()


class FakeExecutor:
    """Stands in for benchexec.runexecutor.RunExecutor (see the module)."""

    def __init__(self, use_namespaces=True, dir_modes=None, additional_cgroup_subsystems=()):
        self.SUB_PROCESS_PIDS_LOCK = threading.Lock()
        self.SUB_PROCESS_PIDS = set()
        self.process = None
        self.ignore_stop = False

    def stop(self):
        with self.SUB_PROCESS_PIDS_LOCK:
            if self.process is not None and not self.ignore_stop:
                self.process.kill()

    def execute_run(self, args, output_filename, write_header=False, workingDir=None, environments=None,
                    memlimit=None, hardtimelimit=None, output_dir=None, result_files_patterns=(),
                    cgroupValues=None):
        with open(args[0]) as script:
            mode = script.read().splitlines()[1]
        if mode == "# hang-start":
            NEVER.wait()
        began = time.monotonic()
        with open(output_filename, "w") as log:
            process = subprocess.Popen(args, cwd=workingDir, stdout=log, stderr=subprocess.STDOUT)
        with self.SUB_PROCESS_PIDS_LOCK:
            self.process = process
            self.ignore_stop = mode == "# hang-stop"
            self.SUB_PROCESS_PIDS.add(process.pid)
        if mode == "# hang-stop":
            NEVER.wait()
        returncode = process.wait()
        if mode == "# hang-end":
            NEVER.wait()
        with self.SUB_PROCESS_PIDS_LOCK:
            self.SUB_PROCESS_PIDS.discard(process.pid)
        if mode == "# hang-cleanup":
            NEVER.wait()
        for pattern in result_files_patterns:
            for path in glob.glob(os.path.join(workingDir, pattern), recursive=True):
                shutil.move(path, os.path.join(output_dir, os.path.basename(path)))
        raw = (-returncode) if returncode < 0 else returncode << 8
        return {"exitcode": butil.ProcessExitCode.from_raw(raw), "cputime": 0.01,
                "walltime": time.monotonic() - began, "memory": 1}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """FakeExecutor in place of RunExecutor, guard times of half a second,
    and the working directory of the run, where witnesses are delivered."""
    monkeypatch.setattr(execution.runexecutor, "RunExecutor", FakeExecutor)
    monkeypatch.setattr(execution, "task_limit_supported", lambda: True)
    monkeypatch.setattr(execution, "START_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(execution, "STOP_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(execution, "END_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(strategy, "JOIN_SECONDS", 5.0)
    directory = tmp_path / "cwd"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory


def stub_run(tmp_path, strategy_of, **stubs):
    """cli.run's arguments for the stubs `stubs` (name to script body) under
    the strategy `strategy_of(*names)`."""
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32")
    #FakeExecutor needs no container
    runner.container_problems = lambda: []
    names = {key.replace("_", " "): body for key, body in stubs.items()}
    for name, body in names.items():
        register_stub(runner, name, make_script(tmp_path / "stubs" / name.replace(" ", "_"), body))
    return data_race_conf(strategy_of(*names)), runner


# Seconds after which run_lines fails a run that has not ended: a guard that
# does not work makes the run wait for ever.
RUN_SECONDS = 20


def run_lines(conf, runner, capsys):
    """cli.run in a daemon thread, which the test waits for at most
    RUN_SECONDS: its verdict, the lines it printed and its wall time."""
    outcome = {}

    def run():
        try:
            outcome["verdict"] = cli.run(conf, runner, ComponentGroup())
        except BaseException as error:  # noqa: B036 - re-raised in the test's thread
            outcome["error"] = error

    began = time.monotonic()
    thread = threading.Thread(target=run, name="cli.run of the test", daemon=True)
    thread.start()
    thread.join(RUN_SECONDS)
    if thread.is_alive():
        pytest.fail(f"the run did not end within {RUN_SECONDS} s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["verdict"], capsys.readouterr().out.splitlines(), time.monotonic() - began


def test_a_component_whose_start_hangs_is_an_error_and_the_next_one_decides(tmp_path, fake, capsys):
    conf, runner = stub_run(tmp_path, one_after_another,
                            Stub_A="# hang-start\necho 'STUB-STATUS: true'\n",
                            Stub_B="echo 'STUB-STATUS: true'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert "Tool name: Stub A Status: ERROR (start timed out) Exit code: none, not started" in lines
    assert "Tool name: Stub A Result: unknown" in lines
    assert "Tool name: Stub B Status: true Exit code: 0" in lines
    assert lines[-1] == "CoOpeRace result from: Stub B"
    assert verdict == "true"
    assert seconds < 5


def test_the_decided_answer_and_its_witness_are_delivered_when_a_stopped_start_hangs(tmp_path, fake, capsys):
    # As Goblint's true deciding while GemCutter's RunExecutor hangs in its start.
    conf, runner = stub_run(tmp_path, side_by_side,
                            Stub_A="sleep 0.2\n" + write_witness("no-data-race", "false")
                            + "echo 'STUB-STATUS: false'\n",
                            Stub_B="# hang-start\necho 'STUB-STATUS: true'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert verdict == "false(no-data-race)"
    assert "Tool name: Stub B Status: ERROR (did not stop) Exit code: none, not started" in lines
    assert lines[-1] == "CoOpeRace result from: Stub A"
    assert (fake / "witness.yml").read_text() == PASSING_WITNESS.read_text()
    assert not any("left behind" in line for line in lines)
    assert seconds < 5


def test_a_stopped_component_that_does_not_stop_is_given_up(tmp_path, fake, capsys):
    conf, runner = stub_run(tmp_path, side_by_side,
                            Stub_A="sleep 0.2\necho 'STUB-STATUS: true'\n",
                            Stub_B="# hang-stop\nsleep 30\necho 'STUB-STATUS: true'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert verdict == "true"
    assert "Tool name: Stub B Status: ERROR (did not stop) Exit code: none, not started" in lines
    assert lines[-1] == "CoOpeRace result from: Stub A"
    assert seconds < 5


@pytest.mark.parametrize("mode", ["# hang-end", "# hang-cleanup"])
def test_a_component_whose_runexecutor_does_not_return_after_its_process_ended_is_given_up(
        tmp_path, fake, capsys, mode):
    # As a RunExecutor stuck in its cleanup in phase 2 of no-data-race, which nothing else would stop. The
    # process runs for longer than POLL_SECONDS, so that execute_run sees it once RunExecutor no longer records it.
    conf, runner = stub_run(tmp_path, one_after_another,
                            Stub_A=f"{mode}\nsleep 0.3\necho 'STUB-STATUS: true'\n",
                            Stub_B="echo 'STUB-STATUS: true'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert ("Tool name: Stub A Status: ERROR (did not return after its process ended) Exit code: none, "
            "not started") in lines
    assert "Tool name: Stub B Status: true Exit code: 0" in lines
    assert lines[-1] == "CoOpeRace result from: Stub B"
    assert verdict == "true"
    assert seconds < 5


def test_a_witness_check_that_takes_too_long_costs_the_witness_and_not_the_verdict_line(
        tmp_path, fake, capsys, monkeypatch):
    # A witness above IN_PROCESS_BYTES whose check in a process of its own never ends: a no-data-race false
    # needs a witness that passes, so the result is unknown, and it comes as soon as the check is given up.
    started = tmp_path / "check-started"
    monkeypatch.setattr(witnesses, "_CHECK", f"open({str(started)!r}, 'w').close()\nimport time\ntime.sleep(60)\n")
    monkeypatch.setattr(witnesses, "CHECK_SECONDS", 1.0)
    conf, runner = stub_run(tmp_path, one_after_another,
                            Stub_A=write_witness("no-data-race", "false")
                            + f"printf '#%0{witnesses.IN_PROCESS_BYTES}d\\n' 0 >> run-witness.yml\n"
                            + "echo 'STUB-STATUS: false'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert verdict == "unknown"
    assert "CoOpeRace answer: Stub A false, bracket 1, no witness that passes the check" in lines
    assert started.exists()
    assert 1 <= seconds < 10


def test_a_component_that_started_in_time_runs_as_long_as_it_takes(tmp_path, fake, capsys):
    # It runs four times START_TIMEOUT_SECONDS, which counts only until its process starts.
    conf, runner = stub_run(tmp_path, one_after_another,
                            Stub_A="sleep 2\necho 'STUB-STATUS: true'\n")

    verdict, lines, _ = run_lines(conf, runner, capsys)

    assert verdict == "true"
    assert "Tool name: Stub A Status: true Exit code: 0" in lines
    assert not any("ERROR" in line or "left behind" in line for line in lines)


def test_a_stopped_component_that_ends_in_time_is_stopped_as_before(tmp_path, fake, capsys):
    conf, runner = stub_run(tmp_path, side_by_side,
                            Stub_A="sleep 0.2\necho 'STUB-STATUS: true'\n",
                            Stub_B="sleep 30\necho 'STUB-STATUS: true'\n")

    verdict, lines, seconds = run_lines(conf, runner, capsys)

    assert verdict == "true"
    assert "Tool name: Stub B Status: stopped by CoOpeRace Exit code: signal 9" in lines
    assert not any("ERROR" in line for line in lines)
    assert seconds < 5


class StuckRunner:
    """A StepRunner whose step for "Stuck" never returns, ignoring its group,
    and whose step for any other component returns its verdict "true"."""

    property_name = "no-data-race"

    def prepare(self):
        pass

    def run_step(self, step, group):
        if step.component == "Stuck":
            NEVER.wait()
        return strategy.Outcome("true", step.component, [], "true")

    def deliver(self, witness_files):
        pass

    def cleanup(self):
        pass


def test_the_final_join_is_bounded_and_names_the_thread_left_behind(monkeypatch, capsys):
    from src.cooperace import config

    monkeypatch.setattr(strategy, "JOIN_SECONDS", 0.5)
    root = config.load(side_by_side("Done", "Stuck"), {"Done", "Stuck"})

    result = strategy.execute(root, StuckRunner())
    lines = capsys.readouterr().out.splitlines()

    assert result == "true"
    assert "CoOpeRace: Stuck did not end within 0.5 s: left behind" in lines
    assert lines[-1] == "CoOpeRace result from: Done"
