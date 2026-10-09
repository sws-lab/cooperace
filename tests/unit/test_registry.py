"""The registry of components: its entries, the lazy import of the tool-info
modules, and how a step reports a component that cannot be set up."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.cooperace.components import DEFAULT_WITNESS, REGISTRY, ComponentSpec
from src.cooperace.config import Step

ROOT = Path(__file__).resolve().parents[2]

# The directory under tools/ of every component, as download-tools.py unpacks them.
DIRECTORIES = {
    "Goblint": "goblint",
    "Deagle": "deagle",
    "Dartagnan": "dartagnan",
    "ULTIMATE Automizer": "uautomizer",
    "ULTIMATE GemCutter": "ugemcutter",
    "ULTIMATE Taipan": "utaipan",
    "nacpa": "nacpa",
    "CPAchecker": "CPAchecker-4.0-unix",
    "sv-sanitizers": "sv-sanitizers",
    "RacerF": "racerf",
}


def test_the_registry_has_every_component_under_its_own_name():
    assert set(REGISTRY) == set(DIRECTORIES)
    assert all(spec.name == name for name, spec in REGISTRY.items())


def test_every_component_has_its_directory():
    assert {name: spec.directory for name, spec in REGISTRY.items()} == DIRECTORIES


def test_the_options_of_each_component():
    options = {name: spec.options for name, spec in REGISTRY.items() if spec.options}

    assert options == {
        "Goblint": ("--portfolio-conf", "conf/svcomp26/seq.txt"),
        "ULTIMATE Automizer": ("--full-output",),
        "ULTIMATE GemCutter": ("--full-output",),
        "ULTIMATE Taipan": ("--full-output",),
    }


def test_components_without_a_witness_spec_of_their_own_use_the_default():
    for name in ("Deagle", "nacpa", "CPAchecker", "sv-sanitizers", "RacerF"):
        assert REGISTRY[name].witness == DEFAULT_WITNESS


@pytest.mark.parametrize("name", sorted(DIRECTORIES))
def test_every_tool_info_module_imports_and_names_its_component(name, monkeypatch):
    # The BenchExec wheel is on sys.path relative to the repository root.
    monkeypatch.chdir(ROOT)

    tool = REGISTRY[name].tool()

    expected = "SV-sanitizers" if name == "sv-sanitizers" else name
    assert tool.name() == expected


def test_tool_makes_a_new_object_of_the_module_s_class_Tool(monkeypatch):
    class Tool:
        pass

    monkeypatch.setitem(sys.modules, "fake_toolinfo", SimpleNamespace(Tool=Tool))
    spec = ComponentSpec("Fake", "fake_toolinfo", "fake")

    first, second = spec.tool(), spec.tool()

    assert isinstance(first, Tool) and isinstance(second, Tool)
    assert first is not second


def test_importing_components_imports_no_tool_info_module():
    code = ("import os, sys\n"
            f"os.chdir({str(ROOT)!r}); sys.path.insert(0, {str(ROOT)!r})\n"
            "import src.cooperace.components\n"
            "print(sorted(m for m in sys.modules if m.startswith('benchexec.tools.')))\n")

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, timeout=60)

    assert result.stdout.strip() == "['benchexec.tools.template']"


def test_a_tool_info_module_that_cannot_be_imported_is_a_step_without_a_verdict(
        coop, capsys):
    coop.registry["Broken"] = ComponentSpec("Broken", "no_such_toolinfo_module", "broken")

    outcome = coop.runOne(Step("Broken", "all"), coop.root_group)

    assert outcome.verdict == "unknown"
    assert capsys.readouterr().out.splitlines() == [
        "---Broken logs---",
        "",
        "---end of Broken logs---",
        ("Tool name: Broken Status: ERROR (ModuleNotFoundError: No module named "
         "'no_such_toolinfo_module') Exit code: none, not started"),
        "Tool name: Broken Result: unknown",
    ]


def test_sv_sanitizers_is_reported_as_an_error_because_its_name_differs(
        coop, capsys, monkeypatch):
    """The tool-info module names the component "SV-sanitizers", and runActor
    looks the entry up by that name; see the entry in REGISTRY."""
    monkeypatch.chdir(ROOT)

    outcome = coop.runOne(Step("sv-sanitizers", "all"), coop.root_group)

    assert outcome.verdict == "unknown"
    lines = capsys.readouterr().out.splitlines()
    assert ("Tool name: SV-sanitizers Status: ERROR (KeyError: 'SV-sanitizers') "
            "Exit code: none, not started") in lines
