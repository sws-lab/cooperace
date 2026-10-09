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
# The properties of C.Concurrency that the default conf, conf/svcomp26.json,
# has no strategy for, and the property files of sv-benchmarks that hold no
# property of C.Concurrency
NO_STRATEGY = ["unreach-call", "no-overflow", "valid-memsafety"]
UNKNOWN_PROPERTIES = sorted(path for path in (ROOT / "tests" / "properties").glob("*.prp")
                            if path.stem not in [*NO_STRATEGY, "no-data-race"])
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
    """Replaces cli.run by a function that records the conf and the
    property_name of the runner of each call, and returns "false"."""
    calls = []

    def fake_run(conf, runner, group=None):
        calls.append((conf, runner.property_name))
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

@pytest.mark.parametrize("name", NO_STRATEGY)
def test_a_property_the_default_conf_has_no_strategy_for_is_refused_and_no_component_starts(
        run_main, no_run, name):
    status, out, err = run_main("--prop", str(ROOT / "tests" / "properties" / f"{name}.prp"))

    assert (status, out) == (1, "")
    #Not even the line on the missing --arch: the runner is not made
    assert err.splitlines() == [f"CoOpeRace: error: unsupported property {name}: "
                                "the conf has no strategy for it"]
    assert no_run == []


@pytest.mark.parametrize("path", UNKNOWN_PROPERTIES, ids=lambda path: path.name)
def test_a_property_file_of_no_known_property_is_refused_and_no_component_starts(
        run_main, no_run, path):
    assert_refused(run_main("--prop", str(path)), f"unsupported property in {path}: its formulas are not")
    assert no_run == []


def test_the_property_is_recognized_by_its_content_and_not_by_its_name(run_main, no_run, tmp_path):
    (tmp_path / "anything.txt").write_text("CHECK(init(main()),\n  LTL(G !   data-race))\n\n")
    (tmp_path / "no-data-race.prp").write_text("CHECK( init(main()), LTL(G ! call(reach_error())) )")

    accepted = run_main("--prop", "anything.txt")
    refused = run_main("--prop", "no-data-race.prp")

    assert accepted[0] == 0
    assert_refused(refused, "unsupported property unreach-call: the conf has no strategy for it")
    assert [name for _, name in no_run] == ["no-data-race"]


@pytest.mark.parametrize("text", [
    "",
    FORMULA + "\nCHECK( init(main()), LTL(G ! overflow) )\n",
    FORMULA + " extra",
    "LTL(G ! data-race)",
    "CHECK( init(main()), LTL(G ! data-races) )",
], ids=["empty", "two-formulas", "trailing-text", "no-check", "different-atom"])
def test_a_property_file_that_is_not_a_known_formula_is_refused(run_main, no_run, tmp_path, text):
    (tmp_path / "p.prp").write_text(text)

    assert_refused(run_main("--prop", "p.prp"), "unsupported property in p.prp: its formulas are not those of "
                                                "unreach-call, no-overflow, valid-memsafety, no-data-race")
    assert no_run == []


def test_a_property_file_that_cannot_be_read_is_refused(run_main, no_run):
    assert_refused(run_main("--prop", "missing.prp"), "cannot read the property file")
    assert no_run == []


@pytest.mark.parametrize("name", [*NO_STRATEGY, "no-data-race"])
def test_a_conf_with_a_strategy_for_the_property_runs_it(run_main, no_run, tmp_path, name):
    strategy = {"runType": "sequential", "tools": [{"Goblint": "all"}]}
    conf = {"properties": {name: strategy}}
    (tmp_path / "conf.json").write_text(json.dumps(conf))

    status, out, _ = run_main("--prop", str(ROOT / "tests" / "properties" / f"{name}.prp"), "--conf", "conf.json")

    assert status == 0
    assert out.splitlines()[-1] == "CoOpeRace verdict: false"
    assert no_run == [(conf, name)]


@pytest.mark.parametrize("result", ["true", "false", "unknown", "false(unreach-call)", "false(no-overflow)",
                                    "false(valid-deref)", "false(valid-free)", "false(valid-memtrack)"])
def test_main_prints_the_result_of_run_as_the_verdict_line(run_main, monkeypatch, result):
    monkeypatch.setattr(cli, "run", lambda conf, runner, group=None: result)

    status, out, _ = run_main("--prop", str(DATA_RACE))

    assert status == 0
    assert out.splitlines()[-1] == f"CoOpeRace verdict: {result}"


def test_a_conf_of_properties_refuses_a_property_it_has_no_strategy_for(run_main, no_run, tmp_path):
    conf = {"properties": {"unreach-call": {"runType": "sequential", "tools": [{"Goblint": "all"}]}}}
    (tmp_path / "conf.json").write_text(json.dumps(conf))

    assert_refused(run_main("--prop", str(DATA_RACE), "--conf", "conf.json"),
                   "unsupported property no-data-race: the conf has no strategy for it")
    assert no_run == []


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
    ({"properties": {"no-data-race": {"runType": "sequential", "tools": []}}, "runType": "sequential"},
     "a conf with 'properties' has no other key, but this one has 'runType'"),
    ({"properties": {"data-race": {"runType": "sequential", "tools": []}}},
     "the conf's properties has a strategy for 'data-race'"),
    ({"properties": {"unreach-call": {"runType": "sequential", "tools": [{"Goblnt": "all"}]}}},
     "in the strategy for unreach-call: component 'Goblnt'"),
], ids=["unknown-component", "acceptance", "run-type", "no-run-type", "cpu-limit-key", "memory-limit-key", "twice",
        "properties-and-strategy", "unknown-property", "other-property"])
def test_a_conf_that_load_refuses_ends_cooperace_without_a_verdict(run_main, tmp_path, conf, message):
    (tmp_path / "conf.json").write_text(json.dumps(conf))

    assert_refused(run_main("--prop", str(DATA_RACE), "--conf", "conf.json"), message)


def test_a_refused_conf_gives_one_line_on_stderr_and_nothing_else(run_main, tmp_path):
    (tmp_path / "conf.json").write_text(json.dumps({"runType": "sequential", "tools": [{"Goblnt": "all"}]}))

    status, out, err = run_main("--prop", str(DATA_RACE), "--conf", "conf.json")

    assert (status, out) == (1, "")
    assert err.splitlines() == ["CoOpeRace: error: component 'Goblnt' in the conf's tools is not a component "
                                "CoOpeRace can run"]


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


def test_run_runs_the_strategy_for_the_property_of_the_runner_and_no_other(tmp_path, capsys):
    marker = tmp_path / "started"
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32", property_name="valid-memsafety")
    register_stub(runner, "Stub A", make_script(tmp_path / "a", f'touch "{marker}"\necho "STUB-STATUS: true"\n'))
    register_stub(runner, "Stub B", make_script(tmp_path / "b", 'echo "STUB-STATUS: true"\n'))
    conf = {"properties": {"no-data-race": {"runType": "sequential", "tools": [{"Stub A": "all"}]},
                           "valid-memsafety": {"runType": "sequential", "tools": [{"Stub B": "all"}]}}}

    verdict = cli.run(conf, runner)

    assert verdict == "true"
    assert "CoOpeRace result from: Stub B" in capsys.readouterr().out
    assert not marker.exists()


@pytest.mark.parametrize("name", NO_STRATEGY)
def test_run_refuses_a_conf_that_is_one_strategy_for_another_property(make_runner, tmp_path, name):
    marker = tmp_path / "started"
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32", property_name=name)
    register_stub(runner, "Stub A", make_script(tmp_path / "a", f'touch "{marker}"\necho "STUB-STATUS: true"\n'))

    with pytest.raises(cli.SetupError, match=f"unsupported property {name}: the conf has no strategy for it"):
        cli.run({"runType": "sequential", "tools": [{"Stub A": "all"}]}, runner)

    assert not marker.exists()
    assert runner.work_dir is None


def test_a_component_that_is_not_installed_refuses_the_conf_also_in_another_propertys_strategy(
        make_runner, tmp_path):
    marker = tmp_path / "started"
    runner = make_runner()
    runner.tools_dir = str(tmp_path / "no-tools")
    register_stub(runner, "Stub A", make_script(tmp_path / "a", f'touch "{marker}"\necho "STUB-STATUS: true"\n'))
    conf = {"properties": {"no-data-race": {"runType": "sequential", "tools": [{"Stub A": "all"}]},
                           "unreach-call": {"runType": "sequential", "tools": [{"Goblint": "all"}]},
                           "no-overflow": {"runType": "parallel", "tools": [{"Goblint": "all"}]}}}

    with pytest.raises(cli.SetupError) as refusal:
        cli.run(conf, runner)

    assert len(refusal.value.problems) == 1
    assert refusal.value.problems[0].startswith("component 'Goblint'")
    assert not marker.exists()


def test_a_component_installed_from_another_doi_refuses_the_conf_also_in_another_propertys_strategy(
        make_runner, tmp_path):
    marker = tmp_path / "started"
    runner = make_runner()
    register_stub(runner, "Stub A", make_script(tmp_path / "a", f'touch "{marker}"\necho "STUB-STATUS: true"\n'))
    script = make_script(tmp_path / "b", 'echo "STUB-STATUS: true"\n')
    register_stub(runner, "Stub B", script)
    (script.parent / components.DOI_RECORD).write_text("10.5281/zenodo.99\n")
    conf = {"properties": {"no-data-race": {"runType": "sequential", "tools": [{"Stub A": "all"}]},
                           "valid-memsafety": {"runType": "sequential", "tools": [{"Stub B": "all"}]}}}

    with pytest.raises(cli.SetupError) as refusal:
        cli.run(conf, runner)

    assert refusal.value.problems == (
        f"component 'Stub B': {script.parent} is installed from 10.5281/zenodo.99, but "
        f"{components.OPTIONS_FILE} holds the options of {STUB_DOI}; run scripts/download-tools.py",)
    assert not marker.exists()


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


def test_a_runner_takes_only_a_known_property_and_no_data_race_by_default():
    assert ComponentRunner("/dev/null", "/dev/null", "ILP32").property_name == "no-data-race"
    with pytest.raises(ValueError, match="unknown property 'termination'"):
        ComponentRunner("/dev/null", "/dev/null", "ILP32", property_name="termination")


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


def test_the_launcher_refuses_a_property_without_a_strategy_with_status_1_and_no_verdict_line(tmp_path):
    (tmp_path / "foo.c").touch()

    result = launch(tmp_path, "--prop", str(ROOT / "tests/properties/unreach-call.prp"), "foo.c")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "CoOpeRace: error: unsupported property unreach-call: the conf has no strategy for it"]


def test_the_launcher_refuses_an_unknown_property_with_status_1_and_no_verdict_line(tmp_path):
    (tmp_path / "foo.c").touch()
    path = ROOT / "tests/properties/termination.prp"

    result = launch(tmp_path, "--prop", str(path), "foo.c")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"CoOpeRace: error: unsupported property in {path}: its formulas are not those of "
        "unreach-call, no-overflow, valid-memsafety, no-data-race"]
