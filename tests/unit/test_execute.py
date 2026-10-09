"""execute() end to end with stub components: shell scripts run through the
real runActor, actorResult and componentStatus, and the stop on a signal in a
separate process."""
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from support import make_script, register_stub, wait_until

from src.cooperace import processExited

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


def stubbed_coop(make_coop, tmp_path, run_type, **stubs):
    """A Cooperace whose conf runs the stub components in `stubs` (name to
    script body, in the order given, each accepting "all"), registered with
    register_stub. Returns it with the directory of each stub's script."""
    names = {key.replace("_", " "): body for key, body in stubs.items()}
    coop = make_coop({"runType": run_type, "tools": [{name: "all"} for name in names]})
    directories = {}
    for name, body in names.items():
        directories[name] = tmp_path / "stubs" / name.replace(" ", "_")
        register_stub(coop, name, make_script(directories[name], body))
    return coop, directories


def lines_of(captured):
    return captured.out.splitlines()


def test_sequence_prints_the_protocol_lines_and_returns_the_first_verdict(
        make_coop, tmp_path, run_dir, capsys):
    coop, _ = stubbed_coop(
        make_coop, tmp_path, "sequential",
        Stub_A='echo "log of A"\necho "STUB-STATUS: unknown"\n',
        Stub_B='echo "log of B"\necho "STUB-STATUS: true"\n',
        Stub_C='echo "never runs"\necho "STUB-STATUS: true"\n',
    )

    verdict = coop.execute()
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
    ]
    assert not any("Stub C" in line for line in lines)


def test_exit_code_and_signal_appear_in_status_lines(make_coop, tmp_path, run_dir, capsys):
    coop, _ = stubbed_coop(
        make_coop, tmp_path, "sequential",
        Stub_A='echo "STUB-STATUS: ERROR"\nexit 3\n',
        Stub_B='kill -TERM $$\nsleep 5\n',
        Stub_C='echo "STUB-STATUS: false"\n',
    )

    verdict = coop.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "false"
    assert "Tool name: Stub A Status: ERROR (3) Exit code: 3" in lines
    assert "Tool name: Stub A Result: unknown" in lines
    assert "Tool name: Stub B Status: KILLED Exit code: signal 15" in lines
    assert "Tool name: Stub B Result: unknown" in lines
    assert "Tool name: Stub C Status: false Exit code: 0" in lines
    assert "Tool name: Stub C Result: false" in lines


def test_a_verdict_the_conf_does_not_accept_is_unknown(make_coop, tmp_path, run_dir, capsys):
    coop, _ = stubbed_coop(make_coop, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: false"\n')
    coop.conf["tools"] = [{"Stub A": "true"}]

    verdict = coop.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "unknown"
    assert "Tool name: Stub A Status: false Exit code: 0" in lines
    assert "Tool name: Stub A Result: unknown" in lines


def test_the_witness_of_the_returned_component_is_delivered_to_the_working_directory(
        make_coop, tmp_path, run_dir):
    coop, directories = stubbed_coop(
        make_coop, tmp_path, "sequential",
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

    verdict = coop.execute()

    assert verdict == "false"
    assert (run_dir / "witness.graphml").read_text() == "<graphml new/>\n"
    assert (run_dir / "witness.yml").read_text() == "- entry\n"
    assert old.read_text() == "old"
    # The files were moved out of the component's directory.
    assert not (directories["Stub B"] / "run-witness.graphml").exists()
    # Stub A's witness, written during the run but not returned, is not delivered.
    assert not (directories["Stub A"] / "witness.graphml").exists()


def test_a_stale_witness_in_the_working_directory_is_removed_when_there_is_no_verdict(
        make_coop, tmp_path, run_dir):
    coop, _ = stubbed_coop(make_coop, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: unknown"\n')
    (run_dir / "witness.graphml").write_text("from an earlier run")

    assert coop.execute() == "unknown"
    assert not (run_dir / "witness.graphml").exists()


def test_parallel_winner_is_returned_and_the_loser_is_reported_as_stopped(
        make_coop, tmp_path, run_dir, capsys):
    coop, _ = stubbed_coop(
        make_coop, tmp_path, "parallel",
        Stub_Slow='echo "slow started"\nexec sleep 60\n',
        Stub_Fast='sleep 0.5\necho "STUB-STATUS: true"\n',
    )

    verdict = coop.execute()
    lines = lines_of(capsys.readouterr())

    assert verdict == "true"
    assert "Tool name: Stub Fast Result: true" in lines
    assert "Tool name: Stub Slow Status: stopped by CoOpeRace Exit code: signal 15" in lines
    assert not any(line.startswith("Tool name: Stub Slow Result:") for line in lines)


def test_the_work_directory_is_removed_and_no_component_is_left(
        make_coop, tmp_path, run_dir):
    coop, _ = stubbed_coop(make_coop, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: true"\n')

    coop.execute()

    assert not os.path.exists(coop.work_dir)
    assert coop.root_group.processes == set()


def test_an_unknown_run_type_prints_an_error_and_gives_unknown(
        make_coop, tmp_path, run_dir, capsys):
    coop = make_coop({"runType": "interleaved", "tools": []})

    verdict = coop.execute()
    out = capsys.readouterr().out

    assert verdict == "unknown"
    assert "Error, something went wrong:" in out
    assert "execution type in conf file is incorrect" in out


def test_execute_restores_the_signal_handlers(make_coop, tmp_path, run_dir):
    coop, _ = stubbed_coop(make_coop, tmp_path, "sequential",
                           Stub_A='echo "STUB-STATUS: true"\n')
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    coop.execute()

    assert {s: signal.getsignal(s) for s in before} == before


# --- a signal while components run ------------------------------------------

DRIVER = textwrap.dedent('''
    """Runs CoOpeRace on two stub components in parallel, as execute() does
    under BenchExec, and prints the verdict if it gets to return one."""
    import os
    import sys
    from pathlib import Path

    root, support_dir, stub_dir, run_dir = sys.argv[1:5]
    os.chdir(root)
    sys.path[:0] = [root, support_dir]
    from src.cooperace import Cooperace
    from support import register_stub

    os.chdir(run_dir)
    conf = {"runType": "parallel", "tools": [{"Stub A": "all"}, {"Stub B": "all"}]}
    coop = Cooperace("/dev/null", "/dev/null", "ILP32", conf)
    for letter in "AB":
        register_stub(coop, f"Stub {letter}", Path(stub_dir) / letter / "stub.sh")
    print("verdict:", coop.execute(), flush=True)
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
