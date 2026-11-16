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
from src.cooperace.components import REGISTRY
from src.cooperace.config import ConfError

ROOT = Path(__file__).resolve().parents[2]

# A linter that decides by a marker in the witness: "# lint: invalid" (or a
# file named *invalid.witness.yml) is invalid syntax, "# lint: type" a witness
# of the unexpected type, "# lint: crash" a traceback, "# lint: hang" a linter
# that never ends, "# lint: silent" no output; anything else is valid.
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
    if "# lint: invalid" in text or path.endswith("invalid.witness.yml"):
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
