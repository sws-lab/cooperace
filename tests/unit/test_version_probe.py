# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""ComponentRunner.probe_versions: the version of an ULTIMATE component is
determined once, before any component starts, and kept by the tool-info
object that run_step uses; a probe that fails or takes too long gives the
version "unknown", with which ULTIMATE's cmdline still gives the default
(wrapper) command line."""
import threading
from dataclasses import dataclass

import pytest
from benchexec.tools.template import BaseTool2
from benchexec.tools.ultimategemcutter import Tool as GemCutter

from src.cooperace import components
from src.cooperace.components import ComponentRunner, ComponentSpec

NEVER = threading.Event()


class ProbedGemCutter(GemCutter):
    """GemCutter's tool-info class, whose wrapper and Java version queries
    are replaced by `behaviour` ("ok", "hang" or "fail"), counted."""

    def __init__(self, behaviour):
        super().__init__()
        self.behaviour = behaviour
        self.queries = 0

    def _version_from_tool(self, executable, *args, **kwargs):
        self.queries += 1
        if self.behaviour == "hang":
            NEVER.wait()
        if self.behaviour == "fail":
            raise OSError("no java")
        return "abc1234"

    def _ultimate_version(self, executable):
        return "0.3.0"


@dataclass(frozen=True)
class ProbedSpec(ComponentSpec):
    behaviour: str = "ok"

    def tool(self):
        return ProbedGemCutter(self.behaviour)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """A ComponentRunner whose tools directory holds an ULTIMATE
    installation that executable() accepts, with three registry entries for
    it: "Ok", "Hang" and "Fail"."""
    monkeypatch.setattr(components, "VERSION_PROBE_SECONDS", 0.3)
    install = tmp_path / "tools" / "ugemcutter"
    (install / "plugins").mkdir(parents=True)
    (install / "plugins" / "org.eclipse.equinox.launcher_1.0.jar").write_text("")
    (install / "Ultimate.py").write_text("#!/bin/sh\n")
    (install / "Ultimate.py").chmod(0o755)
    made = ComponentRunner("/dev/null", "/dev/null", "ILP32")
    made.tools_dir = str(tmp_path / "tools")
    for behaviour in ("ok", "hang", "fail"):
        made.registry[behaviour.title()] = ProbedSpec(behaviour.title(), "", "ugemcutter", behaviour=behaviour)
    return made


def cmdline(runner, name):
    actor = runner.tool_of(name)
    executable = actor.executable(BaseTool2.ToolLocator(tool_directory=f"{runner.tools_dir}/ugemcutter"))
    task = BaseTool2.Task.with_files(["task.c"], property_file="unreach-call.prp",
                                     options={"data_model": "ILP32", "language": "C"})
    return executable, actor.cmdline(executable, [], task, BaseTool2.ResourceLimits())


def test_the_version_is_probed_once_and_kept_by_the_object_that_runs(runner):
    runner.probe_versions(["Ok"])
    actor = runner.tool_of("Ok")
    assert actor.queries == 1

    executable, first = cmdline(runner, "Ok")
    _, second = cmdline(runner, "Ok")

    assert actor.version(executable) == "0.3.0-abc1234"
    assert first == second == [executable, "--spec", "unreach-call.prp", "--file", "task.c",
                               "--architecture", "32bit"]
    assert actor.queries == 1


@pytest.mark.parametrize("name, reason", [("Hang", "no answer in 0.3 s"), ("Fail", "OSError: no java")])
def test_without_a_version_the_default_command_line_is_given(runner, name, reason, capsys):
    runner.probe_versions([name])

    executable, line = cmdline(runner, name)

    assert runner.tool_of(name).version(executable) == components.UNKNOWN_VERSION
    assert line == [executable, "--spec", "unreach-call.prp", "--file", "task.c", "--architecture", "32bit"]
    assert (f"CoOpeRace: the version of {name} could not be determined ({reason}): going on without it"
            in capsys.readouterr().err.splitlines())


class Plain(BaseTool2):
    """A tool-info class that is not ULTIMATE's, whose version() must not be called."""

    def executable(self, tool_locator):
        return "/bin/true"

    def name(self):
        return "Plain"

    def version(self, executable):
        raise AssertionError("version() called")

    def cmdline(self, executable, options, task, rlimits):
        return [executable]


@dataclass(frozen=True)
class PlainSpec(ComponentSpec):
    def tool(self):
        return Plain()


def test_components_that_are_not_ultimate_are_not_probed(runner, capsys):
    runner.registry["Plain"] = PlainSpec("Plain", "", "plain")

    runner.probe_versions(["Plain"])

    assert capsys.readouterr().err == ""
