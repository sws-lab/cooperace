"""The command line: what makes CoOpeRace end with status 1 or 2, one line on
stderr and no "CoOpeRace verdict:" line (a defect of the command line, the
property, the conf, the installation, or an exception of CoOpeRace's own), and
what does not (a component that crashes)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from support import STUB_DOI, make_script, register_stub

from src.cooperace import cli, components
from src.cooperace.components import ComponentRunner

ROOT = Path(__file__).resolve().parents[2]
DATA_RACE = ROOT / "tests" / "properties" / "no-data-race.prp"
OTHER_PROPERTIES = sorted(path for path in (ROOT / "tests" / "properties").glob("*.prp")
                          if path.name != "no-data-race.prp")
FORMULA = "CHECK( init(main()), LTL(G ! data-race) )"


@pytest.fixture
def run_main(tmp_path, monkeypatch, capsys):
    """Returns a function that runs cli.main in `tmp_path`, where the task
    foo.c exists, with the arguments it is given and a task foo.c appended,
    and returns (exit status, standard output, standard error)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "foo.c").touch()

    def run(*arguments):
        monkeypatch.setattr(sys, "argv", ["cooperace", *arguments, "foo.c"])
        try:
            cli.main()
            status = 0
        except SystemExit as exit_:
            status = exit_.code
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    return run


@pytest.fixture
def no_run(monkeypatch):
    """Replaces cli.run by a function that records its calls and returns "false"."""
    calls = []

    def fake_run(conf, runner, group=None):
        calls.append(conf)
        return "false"

    monkeypatch.setattr(cli, "run", fake_run)
    return calls


def error_lines(stderr):
    return [line for line in stderr.splitlines() if not line.startswith("CoOpeRace: no --arch")]


def assert_refused(result, message):
    """`result` of run_main: status 1, no standard output (so no verdict
    line) and exactly one line on stderr, which has `message`."""
    status, out, err = result
    assert status == 1
    assert out == ""
    lines = error_lines(err)
    assert len(lines) == 1, err
    assert lines[0].startswith("CoOpeRace: error: ")
    assert message in lines[0]


# --- the command line ----------------------------------------------------------

def test_prop_is_required(run_main, no_run):
    status, out, err = run_main()

    assert status == 2
    assert out == ""
    assert "--prop" in err
    assert no_run == []


def test_a_task_that_is_not_a_file_is_refused(run_main, no_run, tmp_path):
    (tmp_path / "foo.c").unlink()

    assert_refused(run_main("--prop", str(DATA_RACE)), "the task foo.c is not a file")
    assert no_run == []


def test_a_data_race_run_prints_the_verdict_line_last_and_exits_normally(run_main, no_run):
    status, out, _ = run_main("--prop", str(DATA_RACE))

    assert status == 0
    assert out.splitlines()[-1] == "CoOpeRace verdict: false"


# --- the property --------------------------------------------------------------

@pytest.mark.parametrize("path", OTHER_PROPERTIES, ids=lambda path: path.name)
def test_a_property_other_than_no_data_race_is_refused_and_no_component_starts(
        run_main, no_run, path):
    assert_refused(run_main("--prop", str(path)), "unsupported property")
    assert no_run == []


def test_the_property_is_recognized_by_its_content_and_not_by_its_name(run_main, no_run, tmp_path):
    (tmp_path / "anything.txt").write_text("CHECK(init(main()),\n  LTL(G !   data-race))\n\n")
    (tmp_path / "no-data-race.prp").write_text("CHECK( init(main()), LTL(G ! call(reach_error())) )")

    accepted = run_main("--prop", "anything.txt")
    refused = run_main("--prop", "no-data-race.prp")

    assert accepted[0] == 0
    assert_refused(refused, "unsupported property")
    assert len(no_run) == 1


@pytest.mark.parametrize("text", [
    "",
    FORMULA + "\nCHECK( init(main()), LTL(G ! overflow) )\n",
    FORMULA + " extra",
    "LTL(G ! data-race)",
    "CHECK( init(main()), LTL(G ! data-races) )",
], ids=["empty", "two-formulas", "trailing-text", "no-check", "different-atom"])
def test_a_property_file_that_is_not_the_data_race_formula_is_refused(run_main, no_run, tmp_path, text):
    (tmp_path / "p.prp").write_text(text)

    assert_refused(run_main("--prop", "p.prp"), "unsupported property")
    assert no_run == []


def test_a_property_file_that_cannot_be_read_is_refused(run_main, no_run):
    assert_refused(run_main("--prop", "missing.prp"), "cannot read the property file")
    assert no_run == []


def test_is_data_race_property_ignores_white_space_only():
    assert cli.is_data_race_property(FORMULA)
    assert cli.is_data_race_property("CHECK(init(main()),LTL(G!data-race))")
    assert not cli.is_data_race_property("CHECK( init(main()), LTL(G data-race) )")


# --- the conf ------------------------------------------------------------------

def test_a_conf_that_does_not_exist_or_is_not_json_is_refused(run_main, no_run, tmp_path):
    (tmp_path / "bad.json").write_text("{not json")

    assert_refused(run_main("--prop", str(DATA_RACE), "--conf", "missing.json"), "cannot read the conf missing.json")
    assert_refused(run_main("--prop", str(DATA_RACE), "--conf", "bad.json"), "cannot read the conf bad.json")
    assert no_run == []


@pytest.mark.parametrize("conf, message", [
    ({"runType": "sequential", "tools": [{"Goblnt": "true"}]}, "'Goblnt' in the conf's tools is not a component"),
    ({"runType": "sequential", "tools": [{"Goblint": "True"}]}, "'Goblint' has acceptance 'True'"),
    ({"runType": "interleaved", "tools": []}, "runType is 'interleaved'"),
    ({"tools": []}, "the conf has no 'runType'"),
    ({"runType": "parallel", "tools": [{"Goblint": "all"}], "cpuTimeLimits": {"Deagle": 5}},
     "cpuTimeLimits has a limit for 'Deagle'"),
    ({"runType": "parallel", "tools": [{"Goblint": "all"}], "memoryLimits": {"Dartagnan": "70%"}},
     "memoryLimits has a limit for 'Dartagnan'"),
    ({"runType": "parallel", "tools": [{"Goblint": "all"}, {"Goblint": "all"}]}, "named more than once"),
], ids=["unknown-component", "acceptance", "run-type", "no-run-type", "cpu-limit-key", "memory-limit-key", "twice"])
def test_a_conf_that_load_refuses_ends_cooperace_without_a_verdict(run_main, tmp_path, conf, message):
    (tmp_path / "conf.json").write_text(json.dumps(conf))

    assert_refused(run_main("--prop", str(DATA_RACE), "--conf", "conf.json"), message)


# --- the installation ----------------------------------------------------------

def test_components_that_are_not_installed_are_refused_one_line_each_before_any_starts(
        run_main, tmp_path, monkeypatch):
    monkeypatch.setattr(components, "TOOL_DIR", str(tmp_path))
    shutil.copy(ROOT / components.OPTIONS_FILE, tmp_path)
    conf = {"runType": "sequential", "tools": [{"Goblint": "true"}, [{"Dartagnan": "all"}]]}
    (tmp_path / "conf.json").write_text(json.dumps(conf))

    status, out, err = run_main("--prop", str(DATA_RACE), "--conf", "conf.json")

    assert (status, out) == (1, "")
    lines = error_lines(err)
    assert len(lines) == 2, err
    assert lines[0].startswith("CoOpeRace: error: component 'Goblint': Could not find executable")
    assert lines[1].startswith("CoOpeRace: error: component 'Dartagnan': Could not find executable")


def test_nothing_starts_when_a_later_component_is_not_installed(make_runner, group, tmp_path):
    marker = tmp_path / "started"
    runner = make_runner()
    runner.tools_dir = str(tmp_path / "no-tools")
    register_stub(runner, "Stub A", make_script(tmp_path / "stub", f'touch "{marker}"\necho "STUB-STATUS: true"\n'))
    conf = {"runType": "sequential", "tools": [{"Stub A": "all"}, {"Goblint": "all"}]}

    with pytest.raises(cli.SetupError, match="component 'Goblint'"):
        cli.run(conf, runner, group)

    assert not marker.exists()
    assert runner.work_dir is None


def test_a_component_that_crashes_is_not_a_setup_error(make_runner, group, tmp_path, capsys):
    runner = make_runner()
    register_stub(runner, "Stub A", make_script(tmp_path / "stub", 'echo "STUB-STATUS: ERROR"\nexit 3\n'))

    verdict = cli.run({"runType": "sequential", "tools": [{"Stub A": "all"}]}, runner, group)

    assert verdict == "unknown"
    assert "Tool name: Stub A Status: ERROR (3) Exit code: 3" in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, "{", '{"goblint": {"doi": "10.5281/zenodo.1"}}', "[]"])
def test_an_options_file_that_is_missing_or_malformed_is_refused_before_any_component_starts(
        run_main, tmp_path, monkeypatch, content):
    monkeypatch.setattr(components, "TOOL_DIR", str(tmp_path))
    if content is not None:
        (tmp_path / components.OPTIONS_FILE).write_text(content)

    status, out, err = run_main("--prop", str(DATA_RACE))

    assert (status, out) == (1, "")
    (line,) = error_lines(err)
    assert line.startswith("CoOpeRace: error: ")
    assert str(tmp_path / components.OPTIONS_FILE) in line


def test_installation_problems_names_each_missing_component_on_one_line(tmp_path):
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32")
    runner.tools_dir = str(tmp_path)

    problems = runner.installation_problems(["Goblint", "Dartagnan"])

    assert len(problems) == 2
    assert all("\n" not in problem for problem in problems)
    assert problems[0].startswith("component 'Goblint': Could not find executable")
    assert str(tmp_path / "goblint") in problems[0]


def test_installation_problems_is_empty_for_components_that_are_found_in_their_version(make_runner, tmp_path):
    runner = make_runner()
    register_stub(runner, "Stub A", make_script(tmp_path / "stub", "exit 0\n"))

    assert runner.installation_problems(["Stub A"]) == []


@pytest.mark.parametrize("record, found", [(None, "an unrecorded DOI (no .doi)"),
                                           ("10.5281/zenodo.99", "10.5281/zenodo.99")])
def test_installation_problems_names_a_component_installed_from_another_doi(make_runner, tmp_path, record, found):
    runner = make_runner()
    script = make_script(tmp_path / "stub", "exit 0\n")
    register_stub(runner, "Stub A", script)
    if record is None:
        (script.parent / components.DOI_RECORD).unlink()
    else:
        (script.parent / components.DOI_RECORD).write_text(record + "\n")

    (problem,) = runner.installation_problems(["Stub A"])

    assert problem == (f"component 'Stub A': {script.parent} is installed from {found}, but "
                       f"{components.OPTIONS_FILE} holds the options of {STUB_DOI}; run scripts/download-tools.py")


def test_installation_problems_names_a_component_without_options(make_runner, tmp_path):
    runner = make_runner()
    script = make_script(tmp_path / "stub", "exit 0\n")
    register_stub(runner, "Stub A", script)
    del runner.versions[str(script.parent)]

    (problem,) = runner.installation_problems(["Stub A"])

    assert problem == (f"component 'Stub A': {components.OPTIONS_FILE} has no options for {script.parent}; "
                       "run scripts/download-tools.py")


def test_installation_problems_leaves_other_errors_to_the_step(make_runner):
    runner = make_runner()
    runner.registry["Broken"] = components.ComponentSpec("Broken", "no_such_toolinfo_module", "broken")

    assert runner.installation_problems(["Broken"]) == []


# --- an exception of CoOpeRace's own -------------------------------------------

def test_an_unexpected_exception_ends_cooperace_with_its_traceback_and_no_verdict(
        run_main, monkeypatch):
    def failing_run(conf, runner, group=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run", failing_run)

    status, out, err = run_main("--prop", str(DATA_RACE))

    assert status == 1
    assert out == ""
    lines = error_lines(err)
    assert lines[0] == "CoOpeRace: error: RuntimeError: boom"
    assert "Traceback (most recent call last):" in lines
    assert lines[-1] == "RuntimeError: boom"


# --- the launcher --------------------------------------------------------------

def launch(tmp_path, *arguments):
    return subprocess.run([sys.executable, str(ROOT / "cooperace"), *arguments], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60, check=False)


def test_the_launcher_without_prop_exits_with_status_2(tmp_path):
    result = launch(tmp_path, "foo.c")

    assert result.returncode == 2
    assert "CoOpeRace verdict" not in result.stdout


def test_the_launcher_refuses_another_property_with_status_1_and_no_verdict_line(tmp_path):
    (tmp_path / "foo.c").touch()

    result = launch(tmp_path, "--prop", str(ROOT / "tests/properties/unreach-call.prp"), "foo.c")

    assert result.returncode == 1
    assert result.stdout == ""
    assert error_lines(result.stderr) == [
        f"CoOpeRace: error: unsupported property in {ROOT / 'tests/properties/unreach-call.prp'}: "
        f"CoOpeRace checks only {FORMULA}"]
