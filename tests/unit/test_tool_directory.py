# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The tool directory: CoOpeRace finds lib/, conf/ and tools/ from the
location of its package and not from the working directory, and imports the
one BenchExec wheel in lib/."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src import cooperace
from src.cooperace import cli, components

ROOT = Path(__file__).resolve().parents[2]


def test_the_tool_directory_is_the_directory_of_the_launcher():
    assert Path(cooperace.TOOL_DIR) == ROOT
    assert (ROOT / "cooperace").is_file()


def test_the_tools_are_looked_for_in_the_tool_directory_whatever_the_working_directory(
        make_runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert make_runner().tools_dir == os.path.join(cooperace.TOOL_DIR, "tools")


def test_find_wheel_returns_the_one_wheel_in_lib(tmp_path):
    (tmp_path / "lib").mkdir()
    wheel = tmp_path / "lib" / "benchexec-3.31-py3-none-any.whl"
    wheel.touch()
    (tmp_path / "lib" / "benchexec-3.31-py3-none-any.whl.license").touch()

    assert cooperace.find_wheel(str(tmp_path)) == str(wheel)


def test_find_wheel_refuses_a_lib_without_a_wheel(tmp_path):
    (tmp_path / "lib").mkdir()

    with pytest.raises(FileNotFoundError, match=r"no BenchExec wheel .*lib.benchexec-\*\.whl"):
        cooperace.find_wheel(str(tmp_path))


def test_find_wheel_refuses_a_tool_directory_without_lib(tmp_path):
    with pytest.raises(FileNotFoundError):
        cooperace.find_wheel(str(tmp_path))


def test_find_wheel_refuses_two_wheels_and_names_them(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "benchexec-3.31-py3-none-any.whl").touch()
    (tmp_path / "lib" / "benchexec-3.35-py3-none-any.whl").touch()

    with pytest.raises(ValueError, match=r"more than one BenchExec wheel.*3\.31.*3\.35"):
        cooperace.find_wheel(str(tmp_path))


def copy_of_package(tool_dir, wheels):
    """A copy of src/cooperace in `tool_dir`/src, with the files `wheels` as
    empty files in `tool_dir`/lib. Returns `tool_dir`."""
    shutil.copytree(ROOT / "src" / "cooperace", tool_dir / "src" / "cooperace",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tool_dir / "lib").mkdir()
    for wheel in wheels:
        (tool_dir / "lib" / wheel).touch()
    return tool_dir


def import_package(tool_dir, cwd):
    """Imports the package copy in `tool_dir` in a new process started in
    `cwd` and prints the first entry of sys.path."""
    code = f"import sys; sys.path.insert(0, {str(tool_dir)!r}); import src.cooperace; print(sys.path[0])"
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True,
                          timeout=60, check=False)


@pytest.mark.parametrize("wheels, message", [
    ([], "no BenchExec wheel"),
    (["benchexec-3.31-py3-none-any.whl", "benchexec-3.35-py3-none-any.whl"], "more than one BenchExec wheel"),
])
def test_importing_the_package_without_exactly_one_wheel_ends_with_one_line_on_stderr(
        tmp_path, wheels, message):
    (tmp_path / "tool").mkdir()
    tool_dir = copy_of_package(tmp_path / "tool", wheels)

    result = import_package(tool_dir, tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("CoOpeRace: error: ")
    assert message in result.stderr
    assert len(result.stderr.splitlines()) == 1


def test_the_package_puts_its_own_wheel_first_on_sys_path_from_any_directory(tmp_path):
    (tmp_path / "tool").mkdir()
    tool_dir = copy_of_package(tmp_path / "tool", ["benchexec-3.31-py3-none-any.whl"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # A wheel in the working directory's lib/ must not be taken for the bundled one.
    (elsewhere / "lib").mkdir()
    (elsewhere / "lib" / "benchexec-9.9-py3-none-any.whl").touch()

    result = import_package(tool_dir, elsewhere)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tool_dir / "lib" / "benchexec-3.31-py3-none-any.whl")


def test_the_bundled_benchexec_is_the_one_imported_from_any_directory(tmp_path):
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
            "import src.cooperace.components, benchexec\n"
            "print(benchexec.__version__, benchexec.__file__)")

    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                            text=True, timeout=60, check=True)

    (wheel,) = (ROOT / "lib").glob("benchexec-*.whl")
    version, path = result.stdout.split()
    assert wheel.name == f"benchexec-{version}-py3-none-any.whl"
    assert path.startswith(str(wheel))


def test_the_launcher_started_elsewhere_finds_the_package(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "cooperace"), "--version"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("CoOpeRace")


@pytest.fixture
def conf_read_by_main(tmp_path, monkeypatch):
    """Returns a function that runs cli.main in `tmp_path` with the arguments
    it is given, with cli.run replaced, and returns the conf main passed to it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "foo.c").touch()
    seen = []

    def fake_run(conf, runner, group=None):
        seen.append(conf)
        return "unknown"

    monkeypatch.setattr(cli, "run", fake_run)

    def run_main(*arguments):
        monkeypatch.setattr(sys, "argv", ["cooperace", "--prop", str(ROOT / "tests/properties/no-data-race.prp"),
                                          *arguments, "foo.c"])
        cli.main()
        return seen[-1]

    return run_main


def test_the_default_conf_is_read_from_the_tool_directory(conf_read_by_main):
    conf = conf_read_by_main()

    assert conf == json.loads((ROOT / "conf" / "svcomp26.json").read_text())


def test_a_conf_given_with_conf_is_read_relative_to_the_working_directory(conf_read_by_main, tmp_path):
    (tmp_path / "mine.json").write_text('{"runType": "sequential", "tools": []}')

    conf = conf_read_by_main("--conf", "mine.json")

    assert conf == {"runType": "sequential", "tools": []}


def test_a_witness_is_delivered_into_the_working_directory(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    witness = tmp_path / "w.graphml"
    witness.write_text("<graphml/>")

    components.witness_files_to_file_root([str(witness)])

    assert (work / "witness.graphml").read_text() == "<graphml/>"
