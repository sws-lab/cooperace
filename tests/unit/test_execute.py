# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""cli.run and strategy.execute end to end with stub components: shell scripts
run through the real run_component, run_in_session and component_status, and the
stop on a signal in a separate process."""
import os
import shlex
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from support import make_script, register_stub, wait_until

from src.cooperace import cli
from src.cooperace.components import ComponentRunner
from src.cooperace.processes import ComponentGroup, processExited

ROOT = Path(__file__).resolve().parents[2]
SUPPORT_DIR = Path(__file__).resolve().parent
OLD = 946684800  # 2000-01-01, seconds since the epoch


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """The working directory of the run, where witnesses are delivered."""
    directory = tmp_path / "cwd"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory


class Run:
    """A ComponentRunner `runner` and a conf `conf` for it, run by execute with
    cli.run in the ComponentGroup `group`."""

    def __init__(self, runner, conf):
        self.runner = runner
        self.conf = conf
        self.group = ComponentGroup()

    def execute(self):
        return cli.run(self.conf, self.runner, self.group)


def stubbed_run(make_runner, tmp_path, run_type, **stubs):
    """A Run whose conf runs the stub components in `stubs` (name to
    script body, in the order given, each accepting "all"), registered with
    register_stub. Returns it with the directory of each stub's script."""
    names = {key.replace("_", " "): body for key, body in stubs.items()}
    run = Run(make_runner(), {"runType": run_type, "tools": [{name: "all"} for name in names]})
    directories = {}
    for name, body in names.items():
        directories[name] = tmp_path / "stubs" / name.replace(" ", "_")
        register_stub(run.runner, name, make_script(directories[name], body))
    return run, directories


def lines_of(captured):
    return captured.out.splitlines()


def test_a_violation_of_another_property_is_printed_as_the_status_and_is_no_verdict(
        make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(
        make_runner, tmp_path, "sequential",
        Stub_A='echo "STUB-STATUS: false(unreach-call)"\n',
        Stub_B='echo "STUB-STATUS: false(no-data-race)"\n',
    )

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "false"
    assert "Tool name: Stub A Status: false(unreach-call) Exit code: 0" in lines
    assert "Tool name: Stub A Result: unknown" in lines
    assert "CoOpeRace result from: Stub B" in lines


def test_sequence_prints_the_protocol_lines_and_returns_the_first_verdict(
        make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(
        make_runner, tmp_path, "sequential",
        Stub_A='echo "log of A"\necho "STUB-STATUS: unknown"\n',
        Stub_B='echo "log of B"\necho "STUB-STATUS: true"\n',
        Stub_C='echo "never runs"\necho "STUB-STATUS: true"\n',
    )

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "true"
    start = lines.index("---Stub A logs---")
    assert lines[start:start + 10] == [
        "---Stub A logs---",
        "log of A",
        "STUB-STATUS: unknown",
        "---end of Stub A logs---",
        "Tool name: Stub A Status: unknown Exit code: 0",
        "Tool name: Stub A Result: unknown",
        "---Stub B logs---",
        "log of B",
        "STUB-STATUS: true",
        "---end of Stub B logs---",
    ]
    assert lines[start + 10:] == [
        "Tool name: Stub B Status: true Exit code: 0",
        "Tool name: Stub B Result: true",
        "CoOpeRace result from: Stub B",
    ]
    assert not any("Stub C" in line for line in lines)


def test_exit_code_and_signal_appear_in_status_lines(make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(
        make_runner, tmp_path, "sequential",
        Stub_A='echo "STUB-STATUS: ERROR"\nexit 3\n',
        Stub_B='kill -TERM $$\nsleep 5\n',
        Stub_C='echo "STUB-STATUS: false"\n',
    )

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "false"
    assert "Tool name: Stub A Status: ERROR (3) Exit code: 3" in lines
    assert "Tool name: Stub A Result: unknown" in lines
    assert "Tool name: Stub B Status: KILLED Exit code: signal 15" in lines
    assert "Tool name: Stub B Result: unknown" in lines
    assert "Tool name: Stub C Status: false Exit code: 0" in lines
    assert "Tool name: Stub C Result: false" in lines


def test_a_verdict_the_conf_does_not_accept_is_unknown(make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(make_runner, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: false"\n')
    run.conf["tools"] = [{"Stub A": "true"}]

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "unknown"
    assert "Tool name: Stub A Status: false Exit code: 0" in lines
    assert "Tool name: Stub A Result: unknown" in lines


def test_the_witness_of_the_returned_component_is_delivered_to_the_working_directory(
        make_runner, tmp_path, run_dir):
    run, directories = stubbed_run(
        make_runner, tmp_path, "sequential",
        Stub_A='echo "<graphml/>" > witness.graphml\necho "STUB-STATUS: unknown"\n',
        Stub_B='echo "<graphml new/>" > run-witness.graphml\n'
               'echo "- entry" > run-witness.yml\necho "STUB-STATUS: false"\n',
    )
    # A file of an earlier run in the component's directory.
    old = directories["Stub B"] / "old-witness.graphml"
    old.write_text("old")
    os.utime(old, (OLD, OLD))
    # A witness of an earlier CoOpeRace run in the working directory.
    (run_dir / "witness.yml").write_text("from an earlier run")

    verdict = run.execute()

    assert verdict == "false"
    assert (run_dir / "witness.graphml").read_text() == "<graphml new/>\n"
    assert (run_dir / "witness.yml").read_text() == "- entry\n"
    assert old.read_text() == "old"
    # The files were moved out of the component's directory.
    assert not (directories["Stub B"] / "run-witness.graphml").exists()
    # Stub A's witness, written during the run but not returned, is not delivered.
    assert not (directories["Stub A"] / "witness.graphml").exists()


def test_a_stale_witness_in_the_working_directory_is_removed_when_there_is_no_verdict(
        make_runner, tmp_path, run_dir):
    run, _ = stubbed_run(make_runner, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: unknown"\n')
    (run_dir / "witness.graphml").write_text("from an earlier run")

    assert run.execute() == "unknown"
    assert not (run_dir / "witness.graphml").exists()


def test_parallel_winner_is_returned_and_the_loser_is_reported_as_stopped(
        make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(
        make_runner, tmp_path, "parallel",
        Stub_Slow='echo "slow started"\nexec sleep 60\n',
        Stub_Fast='sleep 0.5\necho "STUB-STATUS: true"\n',
    )

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "true"
    assert "Tool name: Stub Fast Result: true" in lines
    assert "Tool name: Stub Slow Status: stopped by CoOpeRace Exit code: signal 15" in lines
    assert not any(line.startswith("Tool name: Stub Slow Result:") for line in lines)


def test_the_work_directory_is_removed_and_no_component_is_left(
        make_runner, tmp_path, run_dir):
    run, _ = stubbed_run(make_runner, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: true"\n')

    run.execute()

    assert not os.path.exists(run.runner.work_dir)
    assert run.group.processes == set()


def test_an_unknown_run_type_is_refused_before_anything_runs(make_runner, tmp_path, run_dir, capsys):
    run = Run(make_runner(), {"runType": "interleaved", "tools": []})

    with pytest.raises(cli.SetupError, match="runType is 'interleaved'"):
        run.execute()

    assert capsys.readouterr().out == ""
    assert run.runner.work_dir is None


def test_an_exception_while_the_tree_runs_stops_the_components_and_propagates(
        make_runner, tmp_path, run_dir, capsys):
    run, _ = stubbed_run(make_runner, tmp_path, "sequential",
                         Stub_A='echo "STUB-STATUS: true"\n')

    def deliver(witness_files):
        raise RuntimeError("cannot deliver")

    run.runner.deliver = deliver

    with pytest.raises(RuntimeError, match="cannot deliver"):
        run.execute()

    out = capsys.readouterr().out
    assert "Error, something went wrong" not in out
    assert "CoOpeRace result from" not in out
    assert not os.path.exists(run.runner.work_dir)
    assert run.group.processes == set()


def test_execute_restores_the_signal_handlers(make_runner, tmp_path, run_dir):
    run, _ = stubbed_run(make_runner, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: true"\n')
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    run.execute()

    assert {s: signal.getsignal(s) for s in before} == before


# --- the result per property ---------------------------------------------------

@pytest.mark.parametrize("property_name, statuses, expected", [
    ("no-data-race", ["false(no-data-race)"], "false"),
    ("no-data-race", ["false"], "false"),
    ("no-data-race", ["true"], "true"),
    ("no-data-race", ["false(unreach-call)", "false(valid-deref)"], "unknown"),
    ("unreach-call", ["false(unreach-call)"], "false(unreach-call)"),
    ("unreach-call", ["false"], "false(unreach-call)"),
    ("unreach-call", ["false(no-data-race)", "true"], "true"),
    ("no-overflow", ["false(no-overflow)"], "false(no-overflow)"),
    ("no-overflow", ["false"], "false(no-overflow)"),
    ("no-overflow", ["false(unreach-call)", "unknown"], "unknown"),
    ("valid-memsafety", ["false(valid-deref)"], "false(valid-deref)"),
    ("valid-memsafety", ["false(valid-free)"], "false(valid-free)"),
    ("valid-memsafety", ["false(valid-memtrack)"], "false(valid-memtrack)"),
    ("valid-memsafety", ["false", "false(valid-memcleanup)", "false(valid-free)"], "false(valid-free)"),
    ("valid-memsafety", ["true"], "true"),
], ids=lambda value: ",".join(value) if isinstance(value, list) else value)
def test_run_returns_the_result_of_the_accepted_status_for_the_property(
        tmp_path, run_dir, capsys, property_name, statuses, expected):
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32", property_name=property_name)
    names = [f"Stub {index}" for index in range(len(statuses))]
    for name, status in zip(names, statuses, strict=True):
        register_stub(runner, name, make_script(tmp_path / "stubs" / name.replace(" ", "_"),
                                                f'echo "STUB-STATUS: {status}"\n'))
    strategy = {"runType": "sequential", "tools": [{name: "all"} for name in names]}

    result = cli.run({"properties": {property_name: strategy}}, runner)
    lines = lines_of(capsys.readouterr())

    assert result == expected
    #A component's own Result line keeps the verdict, without the property
    results = [line.split(" Result: ")[1] for line in lines if line.startswith("Tool name: ") and " Result: " in line]
    assert len(results) == len(statuses)
    assert set(results) <= {"true", "false", "unknown"}
    if expected == "unknown":
        assert not any(line.startswith("CoOpeRace result from:") for line in lines)
    else:
        assert lines[-1] == f"CoOpeRace result from: {names[-1]}"


# --- per-component limits ----------------------------------------------------

# A stub that runs until a signal ends it. It first makes itself non-dumpable
# (PR_SET_DUMPABLE is 4), because SIGXCPU's default action dumps core.
BUSY_STUB = (f"exec {shlex.quote(sys.executable)} -c "
             + shlex.quote("import ctypes; ctypes.CDLL(None).prctl(4, 0)\nwhile True: pass") + "\n")
# A stub that allocates 1 GiB, which fails with MemoryError under a smaller RLIMIT_DATA.
ALLOCATING_STUB = f"exec {shlex.quote(sys.executable)} -c 'bytearray(2**30)'\n"


def limited_run(make_runner, tmp_path, property_name, per_property, limits):
    """A Run of the stubs "Stub A" (BUSY_STUB or ALLOCATING_STUB, by the key of
    `limits`) and "Stub B" (answers true) in sequence, with the limits
    `limits` (a conf's "cpuTimeLimits" or "memoryLimits" for Stub A), for a
    runner of the property `property_name`. The conf is one strategy, or, if
    `per_property`, the strategy for `property_name` in a conf of properties."""
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32", property_name=property_name)
    body = BUSY_STUB if "cpuTimeLimits" in limits else ALLOCATING_STUB
    register_stub(runner, "Stub A", make_script(tmp_path / "stubs" / "A", body))
    register_stub(runner, "Stub B", make_script(tmp_path / "stubs" / "B", 'echo "STUB-STATUS: true"\n'))
    strategy = {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Stub B": "all"}], **limits}
    return Run(runner, {"properties": {property_name: strategy}} if per_property else strategy)


@pytest.mark.parametrize("property_name, per_property", [
    ("no-data-race", False), ("no-data-race", True), ("valid-memsafety", True)],
    ids=["one-strategy", "per-property-no-data-race", "per-property-valid-memsafety"])
def test_a_component_stopped_at_its_cpu_time_limit_leaves_cooperace_to_run_the_next(
        make_runner, tmp_path, run_dir, capsys, property_name, per_property):
    run = limited_run(make_runner, tmp_path, property_name, per_property, {"cpuTimeLimits": {"Stub A": 1}})

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "true"
    assert "CPU-time limit of Stub A: 1 s (RLIMIT_CPU)" in lines
    assert "Tool name: Stub A Status: TIMEOUT Exit code: signal 24" in lines
    assert "Tool name: Stub A Result: unknown" in lines
    assert not any(line.startswith("CPU-time limit of Stub B") for line in lines)
    assert lines[-1] == "CoOpeRace result from: Stub B"


def test_a_component_stopped_at_its_memory_limit_leaves_cooperace_to_run_the_next(
        make_runner, tmp_path, run_dir, capsys):
    limit = 256 * 2**20
    run = limited_run(make_runner, tmp_path, "unreach-call", True, {"memoryLimits": {"Stub A": limit}})

    verdict = run.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "true"
    assert f"Memory limit of Stub A: {limit} bytes (RLIMIT_DATA)" in lines
    assert "Tool name: Stub A Status: unknown Exit code: 1" in lines
    assert lines[-1] == "CoOpeRace result from: Stub B"


# --- a signal while components run ------------------------------------------

DRIVER = textwrap.dedent('''
    """Runs CoOpeRace on two stub components in parallel, as execute() does
    under BenchExec, and prints the verdict if it gets to return one."""
    import os
    import sys
    from pathlib import Path

    root, support_dir, stub_dir, run_dir = sys.argv[1:5]
    sys.path[:0] = [root, support_dir]
    from src.cooperace import cli
    from src.cooperace.components import ComponentRunner
    from support import register_stub

    os.chdir(run_dir)
    conf = {"runType": "parallel", "tools": [{"Stub A": "all"}, {"Stub B": "all"}]}
    run = ComponentRunner("/dev/null", "/dev/null", "ILP32")
    for letter in "AB":
        register_stub(run, f"Stub {letter}", Path(stub_dir) / letter / "stub.sh")
    print("verdict:", cli.run(conf, run), flush=True)
''')

# Writes its pid where the test can find it, then replaces the shell by sleep.
SLEEPING_STUB = 'echo $$ > "$(dirname "$0")/pid"\necho "stub started"\nexec sleep 60\n'


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_stop_signal_ends_cooperace_and_every_component(tmp_path, signum):
    stub_dir, run_dir = tmp_path / "stubs", tmp_path / "cwd"
    run_dir.mkdir()
    pid_files = []
    for letter in "AB":
        make_script(stub_dir / letter, SLEEPING_STUB)
        pid_files.append(stub_dir / letter / "pid")
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(DRIVER)

    driver = subprocess.Popen(
        [sys.executable, str(driver_file), str(ROOT), str(SUPPORT_DIR),
         str(stub_dir), str(run_dir)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True)
    try:
        assert wait_until(lambda: all(f.exists() and f.read_text().strip()
                                      for f in pid_files)), "the stubs did not start"
        os.kill(driver.pid, signum)
        output, _ = driver.communicate(timeout=30)
    finally:
        if driver.poll() is None:
            os.killpg(driver.pid, signal.SIGKILL)
            driver.wait()

    lines = output.splitlines()
    assert driver.returncode == -signum, output
    assert f"CoOpeRace stopped by signal {signum}" in lines
    for letter in "AB":
        assert (f"Tool name: Stub {letter} Status: stopped by CoOpeRace "
                "Exit code: signal 15") in lines
        assert f"Tool name: Stub {letter} Result: unknown" not in lines
    assert not any(line.startswith("verdict:") for line in lines)
    for pid_file in pid_files:
        pid = int(pid_file.read_text())
        assert wait_until(lambda pid=pid: processExited(pid)), f"stub {pid} still runs"
