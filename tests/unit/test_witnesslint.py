# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""WitnessLint as the gate of a required witness (src/cooperace/witnesslint.py,
"witnessLint": true): the outcomes of witnesslint.lint for a fake linter that
prints what benchexec.tools.witnesslint maps to each status, the technical
failures, the self-test, the loader's checks, and the self-test of the real
linter where tools/witnesslint and its Python modules are installed. The
random scenarios of test_first_verdict run with the switch on there."""
import importlib.util
import os
import textwrap
from pathlib import Path

import pytest

from src.cooperace import config, witnesslint
from src.cooperace.components import REGISTRY, ComponentRunner
from src.cooperace.config import ConfError, Step

ROOT = Path(__file__).resolve().parents[2]

# A linter that, like WitnessLint 2.4 (yaml_linter.matches_file_name_schema),
# refuses a witness file whose name does not end in .yml or .yml.gz, and a
# waypoint without "action" (the self-test's invalid witness), and otherwise
# decides by a marker in the witness: "# lint: invalid" is invalid syntax,
# "# lint: type" a witness of the unexpected type, "# lint: crash" a
# traceback, "# lint: hang" a linter that never ends, "# lint: silent" no
# output; anything else is valid.
FAKE_LINTER = textwrap.dedent('''\
    import sys, time
    path = sys.argv[sys.argv.index("--witness") + 1]
    try:
        text = open(path).read()
    except OSError:
        print("Running witnesslint version 2.4")
        print("witnesslint finished with exit code 5")
        sys.exit(5)
    print("Running witnesslint version 2.4")
    if not path.endswith((".yml", ".yml.gz")):
        print("CRITICAL: Witness file name does not match the schema.")
        print("witnesslint finished with exit code 1")
        sys.exit(1)
    if "# lint: crash" in text:
        print("Traceback (most recent call last):")
        sys.exit(1)
    if "# lint: hang" in text:
        time.sleep(60)
    if "# lint: silent" in text:
        sys.exit(0)
    if "# lint: type" in text:
        print("Witness Type-Match: False")
        print("witnesslint finished with exit code 0")
        sys.exit(0)
    import yaml
    try:
        entries = yaml.safe_load(text)
    except yaml.YAMLError:
        entries = None
    waypoints = [waypoint["waypoint"] for entry in entries or [] if isinstance(entry, dict)
                 for segment in entry.get("content") or [] if isinstance(segment, dict)
                 for waypoint in segment.get("segment") or [] if isinstance(waypoint, dict) and "waypoint" in waypoint]
    if "# lint: invalid" in text or any("action" not in waypoint for waypoint in waypoints):
        print("witnesslint finished with exit code 1")
        sys.exit(1)
    print("Witness Type-Match: True")
    print("witnesslint finished with exit code 0")
''')


@pytest.fixture
def fake_linter(tmp_path, monkeypatch):
    """LINTER_DIR set to a directory with FAKE_LINTER, the self-test's cache
    cleared."""
    directory = tmp_path / "witnesslint"
    directory.mkdir()
    (directory / "witnesslinter.py").write_text(FAKE_LINTER)
    monkeypatch.setattr(witnesslint, "LINTER_DIR", str(directory))
    monkeypatch.setattr(witnesslint, "_usable", None)
    return directory


def lint_text(tmp_path, text, verdict="false"):
    witness = tmp_path / "witness.yml"
    witness.write_text(text)
    program = tmp_path / "program.c"
    program.write_text("int main(void) { return 0; }\n")
    return witnesslint.lint(str(witness), str(program), verdict)


@pytest.mark.parametrize("marker, outcome, status", [
    ("", witnesslint.VALID, "done"),
    ("# lint: invalid\n", witnesslint.INVALID, "ERROR (invalid witness syntax)"),
    ("# lint: type\n", witnesslint.INVALID, "ERROR (unexpected witness type)"),
])
def test_the_linter_s_answer_gives_the_outcome(marker, outcome, status, tmp_path, fake_linter):
    assert lint_text(tmp_path, marker + "- entry_type: violation_sequence\n") == (outcome, status)


def test_a_missing_witness_is_invalid(tmp_path, fake_linter):
    program = tmp_path / "program.c"
    program.write_text("")

    got = witnesslint.lint(str(tmp_path / "absent.yml"), str(program), "true")

    assert got == (witnesslint.INVALID, "ERROR (witness does not exist)")


@pytest.mark.parametrize("marker, reason", [
    ("# lint: crash\n", "status EXCEPTION"),
    ("# lint: silent\n", "status ERROR (linter did not finish)"),
    ("# lint: hang\n", "took longer than"),
])
def test_a_technical_failure_is_failed(marker, reason, tmp_path, fake_linter, monkeypatch):
    monkeypatch.setattr(witnesslint, "LINT_SECONDS", 1.0)

    got = lint_text(tmp_path, marker)

    assert got.outcome == witnesslint.FAILED and reason in got.status


def test_a_linter_that_is_not_installed_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(witnesslint, "LINTER_DIR", str(tmp_path / "nowhere"))

    assert lint_text(tmp_path, "").outcome == witnesslint.FAILED


def test_no_budget_left_is_failed(tmp_path, fake_linter, monkeypatch):
    monkeypatch.setattr(witnesslint, "run_cpu_seconds", lambda: 869.0)
    witness = tmp_path / "w.yml"
    witness.write_text("")

    got = witnesslint.lint(str(witness), str(witness), "false", cpu_time_budget=870)

    assert got.outcome == witnesslint.FAILED and "leaves it no time" in got.status


def test_the_command_is_the_competition_s(tmp_path, fake_linter, monkeypatch):
    seen = []
    real = witnesslint.subprocess.run

    def spy(command, **kwargs):
        seen.append(command)
        return real(command, **kwargs)

    monkeypatch.setattr(witnesslint.subprocess, "run", spy)
    lint_text(tmp_path, "", verdict="true")
    lint_text(tmp_path, "", verdict="false")

    tails = [command[command.index(str(fake_linter)) + 1:] for command in seen]
    assert tails[0][:4] == ["--expectCorrectnessWitness", "--ignoreSelfLoops", "--expectedWitnessVersion", "2"]
    assert tails[1][:4] == ["--expectViolationWitness", "--ignoreSelfLoops", "--expectedWitnessVersion", "2"]
    assert tails[1][4] == "--witness" and tails[1][6].endswith("program.c")


def test_the_self_test_passes_with_a_linter_that_tells_valid_from_invalid(fake_linter):
    assert witnesslint.self_test() == []
    assert witnesslint.self_test_problems() == []


def test_the_self_test_fails_with_a_linter_that_says_invalid_to_everything(fake_linter):
    (fake_linter / "witnesslinter.py").write_text(
        'print("witnesslint finished with exit code 1")\nimport sys\nsys.exit(1)\n')

    problems = witnesslint.self_test_problems()

    assert len(problems) == 1 and "valid.witness.yml" in problems[0]
    assert witnesslint.self_test_problems() is problems     # run once per process


def test_the_self_test_without_time_left_in_the_run_s_budget_fails(fake_linter, monkeypatch):
    monkeypatch.setattr(witnesslint, "run_cpu_seconds", lambda: 869.0)

    problems = witnesslint.self_test_problems(870)

    assert len(problems) == 2 and all("leaves it no time" in problem for problem in problems)


def test_the_self_test_s_lints_get_what_the_run_s_budget_leaves(fake_linter, monkeypatch):
    monkeypatch.setattr(witnesslint, "run_cpu_seconds", lambda: 850.0)
    seen = []
    real_run = witnesslint.subprocess.run

    def spy(command, **kwargs):
        seen.append(kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(witnesslint.subprocess, "run", spy)

    assert witnesslint.self_test(870) == []
    assert seen == [870 - 850 - witnesslint.CHECK_MARGIN_SECONDS + 1] * 2


def test_the_self_test_s_files_are_shipped():
    dist = (ROOT / "scripts" / "svcomp-dist.sh").read_text()
    for name, _verdict, _outcome in witnesslint.SELF_TEST:
        assert f"tests/witnesslint/{name}" in dist
    assert "tests/witnesslint/program.c" in dist and "tools-checkers.txt" in dist


@pytest.mark.skipif(not (Path(witnesslint.LINTER_DIR) / "witnesslinter.py").is_file()
                    or importlib.util.find_spec("clang") is None or importlib.util.find_spec("jsonschema") is None,
                    reason="WitnessLint or its Python modules are not installed")
def test_the_real_linter_passes_the_self_test(monkeypatch):
    monkeypatch.setattr(witnesslint, "_usable", None)

    assert witnesslint.self_test() == []


def test_the_competition_image_check_fails_on_the_warnings_of_a_linter_that_is_not_used():
    """scripts/sv-comp/Dockerfile's check greps the run's output for the
    prefix of the two warnings with which the internal check takes over."""
    check = (ROOT / "scripts" / "sv-comp" / "Dockerfile").read_text()
    code = (ROOT / "src" / "cooperace" / "components.py").read_text()

    assert "grep '^CoOpeRace: WARNING: WitnessLint' /tmp/runexec-output.log" in check
    assert 'say_line(f"CoOpeRace: WARNING: WitnessLint is not used' in code
    assert 'say_line(f"CoOpeRace: WARNING: WitnessLint failed technically' in code


# --- the loader --------------------------------------------------------------------

FV = {"firstVerdict": True, "witnessLint": True, "phases": [["Goblint"]], "answers": [["Goblint"]]}


def test_witness_lint_loads_with_the_first_verdict_mode():
    loaded = config.load(FV, REGISTRY)

    assert loaded.witness_lint and loaded.step("Goblint").witness_lint


@pytest.mark.parametrize("strategy, message", [
    ({**FV, "firstVerdict": False, "witnesses": []}, "witnessLint true without firstVerdict true"),
    ({"phases": [["Goblint"]], "answers": [["Goblint"]], "witnesses": [], "witnessLint": True},
     "witnessLint true without firstVerdict true"),
    ({**FV, "witnessLint": "yes"}, "witnessLint is 'yes', expected true or false"),
])
def test_the_loader_refuses_witness_lint_outside_the_mode(strategy, message):
    with pytest.raises(ConfError, match=message):
        config.load(strategy, REGISTRY)


def test_the_checker_lock_names_witnesslint_2_4():
    lines = [line for line in (ROOT / "tools-checkers.txt").read_text().splitlines()
             if line.strip() and not line.startswith("#")]

    assert lines == ["witnesslint: 10.5281/zenodo.24089806"]
    assert os.path.basename(witnesslint.LINTER_DIR) == "witnesslint"


def test_a_copy_is_linted_under_the_delivered_name_and_the_original_is_kept(tmp_path, fake_linter):
    """WitnessLint refuses a file whose name does not end in .yml: lint_delivered
    lints a copy named witness.yml, with the task's path replaced, and leaves
    the component's file and no copy behind (036ec13 linted "<file>.as-delivered")."""
    source = tmp_path / "out" / "witness.yml.component"
    source.parent.mkdir()
    source.write_text("# /abs/task.c\n- entry_type: violation_sequence\n")
    program = tmp_path / "program.c"
    program.write_text("")
    work = tmp_path / "work"
    work.mkdir()

    assert witnesslint.lint(str(source), str(program), "false").status == "ERROR (invalid witness syntax)"
    got = witnesslint.lint_delivered(str(source), str(program), "false", None, {"/abs/task.c": "rel/task.c"},
                                     str(work))

    assert got == (witnesslint.VALID, "done")
    assert source.read_text().startswith("# /abs/task.c") and list(work.iterdir()) == []


def test_the_runner_delivers_the_bytes_it_linted_with_the_task_s_path_as_given(tmp_path, monkeypatch):
    """ComponentRunner.linted_witness lints a copy with the task's absolute
    path replaced by the path CoOpeRace was given, and ComponentRunner.deliver
    must deliver exactly that text."""
    task = tmp_path / "tasks" / "task.c"
    task.parent.mkdir()
    task.write_text("int main(void) { return 0; }\n")
    source = tmp_path / "out" / "witness.yml"
    source.parent.mkdir()
    source.write_text(f"- entry_type: violation_sequence\n  input_files: [{task}]\n  file_name: {task}\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    runner = ComponentRunner(str(task), "/dev/null", "ILP32", versions={}, property_name="unreach-call",
                             task_path="../tasks/task.c")
    runner.work_dir = str(tmp_path / "work")
    os.mkdir(runner.work_dir)
    linted = []

    def fake_lint(path, program, verdict, cpu_time_budget=None):
        linted.append(Path(path).read_text())
        return witnesslint.Lint(witnesslint.VALID, witnesslint.DONE)

    monkeypatch.setattr(witnesslint, "self_test_problems", lambda cpu_time_budget=None: [])
    monkeypatch.setattr(witnesslint, "lint", fake_lint)

    found = runner.linted_witness(Step("Goblint", "all", first_verdict=True, witness_lint=True), [str(source)],
                                  "false", None)
    runner.deliver([found])

    delivered = (cwd / "witness.yml").read_text()
    assert linted == [delivered]
    assert str(task) not in delivered and "file_name: ../tasks/task.c" in delivered
