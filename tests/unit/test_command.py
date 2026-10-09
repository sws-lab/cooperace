"""How a component is started (ComponentRunner.command): the command line from
benchexec.model.cmdline_for_run, the working directory from the tool-info
module's working_directory() and the environment from its environment()
(run_environment), and that run_in_session starts it so."""
import os
from pathlib import Path

import pytest
from support import STUB_DOI, StubTool, make_script, register_stub

from src.cooperace import components
from src.cooperace.components import REGISTRY, ComponentRunner, run_environment
from src.cooperace.config import Step
from src.cooperace.processes import ComponentGroup, run_in_session

ROOT = Path(__file__).resolve().parents[2]


class PlacedTool(StubTool):
    """A StubTool whose executable is `executable`, under a subdirectory of
    its tool directory, and whose working_directory() and environment()
    return `working_directory` and `environments`."""

    def __init__(self, script, working_directory=os.curdir, environments=None):
        super().__init__("Placed", script)
        self.placed_working_directory = working_directory
        self.environments = environments or {}

    def working_directory(self, executable):
        return self.placed_working_directory

    def environment(self, executable):
        return self.environments


def placed_runner(tmp_path, make_runner):
    """A runner with the component "Placed", whose tool directory is
    tmp_path/tool, run with the options "-o" and "$HOME"."""
    runner = make_runner()
    directory = str(tmp_path / "tool")
    runner.registry["Placed"] = components.ComponentSpec("Placed", "", directory)
    runner.versions[directory] = components.ComponentVersion(STUB_DOI, "stub", ("-o", "$HOME"))
    return runner


def test_the_working_directory_is_the_tool_directory_also_for_an_executable_in_a_subdirectory(
        tmp_path, make_runner):
    tool = PlacedTool(make_script(tmp_path / "tool" / "bin", "exit 0\n"))
    runner = placed_runner(tmp_path, make_runner)

    command = runner.command(tool, runner.registry["Placed"], "/w")

    assert command.cwd == str(tmp_path / "tool")
    assert command.env is None


def test_the_working_directory_is_relative_to_the_tool_directory(tmp_path, make_runner):
    tool = PlacedTool(make_script(tmp_path / "tool" / "bin", "exit 0\n"), working_directory="data")

    runner = placed_runner(tmp_path, make_runner)

    command = runner.command(tool, runner.registry["Placed"], "/w")

    assert command.cwd == str(tmp_path / "tool" / "data")


def test_the_command_line_is_cmdline_for_run_s(tmp_path, make_runner, monkeypatch):
    """cmdline_for_run expands environment variables in every argument, which
    actor.cmdline alone does not."""
    monkeypatch.setenv("HOME", "/home/someone")
    tool = PlacedTool(make_script(tmp_path / "tool" / "bin", "exit 0\n"))
    runner = placed_runner(tmp_path, make_runner)

    command = runner.command(tool, runner.registry["Placed"], "/w")

    assert command.cmdline == [tool.script, "-o", "/home/someone"]


def test_the_environment_of_the_module_is_applied(tmp_path, make_runner, monkeypatch):
    monkeypatch.setenv("COOPERACE_TEST_PATH", "/a")
    tool = PlacedTool(make_script(tmp_path / "tool" / "bin", 'echo "$COOPERACE_TEST_NEW $COOPERACE_TEST_PATH"\n'),
                      environments={"newEnv": {"COOPERACE_TEST_NEW": "new"},
                                    "additionalEnv": {"COOPERACE_TEST_PATH": ":/b"}})
    runner = placed_runner(tmp_path, make_runner)

    command = runner.command(tool, runner.registry["Placed"], "/w")
    result = run_in_session(command.cmdline, command.cwd, ComponentGroup(), env=command.env)

    assert result.stdout == "new /a:/b\n"


def test_run_environment():
    environ = dict(os.environ)

    assert run_environment({}) is None
    assert run_environment({"newEnv": {}, "additionalEnv": {}}) is None
    assert run_environment({"newEnv": {"X_NEW": "1"}}) == {**environ, "X_NEW": "1"}
    assert run_environment({"additionalEnv": {"PATH": ":/extra"}})["PATH"] == environ.get("PATH", "") + ":/extra"
    assert run_environment({"keepEnv": {}}) == {}
    assert run_environment({"keepEnv": {"PATH": "", "X_UNSET_ANYWHERE": ""}}) == (
        {"PATH": environ["PATH"]} if "PATH" in environ else {})


@pytest.mark.parametrize("name", ["Goblint", "Dartagnan", "ULTIMATE Automizer"])
def test_the_components_of_svcomp26_run_in_their_directory_with_cooperace_s_environment(name):
    """What the modules of BenchExec 3.31 return: os.curdir and {}. So the
    working directory is tools/<directory>, the directory of the executable
    of each of the three, as before cmdline_for_run was used."""
    spec = REGISTRY[name]
    runner = ComponentRunner("/dev/null", "/dev/null", "ILP32")
    if runner.installation_problems([name]):
        pytest.skip(f"{name} is not installed under tools/")

    command = runner.command(spec.tool(), spec, "/w")

    assert command.cwd == os.path.join(runner.tools_dir, spec.directory)
    assert command.cwd == os.path.dirname(command.cmdline[0])
    assert command.env is None


def test_a_step_runs_the_component_in_its_working_directory(make_runner, group, tmp_path, capsys):
    runner = make_runner()
    register_stub(runner, "Stub A", make_script(tmp_path / "stub", 'echo "cwd: $(pwd)"\necho "STUB-STATUS: true"\n'))

    runner.run_step(Step("Stub A", "all"), group)

    assert f"cwd: {tmp_path / 'stub'}" in capsys.readouterr().out.splitlines()
