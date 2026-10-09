"""The registry of components: its entries, the lazy import of the tool-info
modules, and how a step reports a component that cannot be set up."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import make_script, register_stub

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
def test_every_tool_info_module_imports_and_names_its_component(name):
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
    code = ("import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import src.cooperace.components\n"
            "print(sorted(m for m in sys.modules if m.startswith('benchexec.tools.')))\n")

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, timeout=60)

    assert result.stdout.strip() == "['benchexec.tools.template']"


def test_a_tool_info_module_that_cannot_be_imported_is_a_step_without_a_verdict(
        runner, group, capsys):
    runner.registry["Broken"] = ComponentSpec("Broken", "no_such_toolinfo_module", "broken")

    outcome = runner.run_step(Step("Broken", "all"), group)

    assert outcome.verdict == "unknown"
    assert capsys.readouterr().out.splitlines() == [
        "---Broken logs---",
        "",
        "---end of Broken logs---",
        ("Tool name: Broken Status: ERROR (ModuleNotFoundError: No module named "
         "'no_such_toolinfo_module') Exit code: none, not started"),
        "Tool name: Broken Result: unknown",
    ]


def test_a_component_is_found_by_its_name_in_the_conf_whatever_the_case_of_the_name_of_its_tool(
        runner, group, capsys, tmp_path):
    """BenchExec's sv-sanitizers module names itself "SV-sanitizers", the entry in
    REGISTRY is "sv-sanitizers": the entry is looked up by the conf's name, and the
    block is printed under the module's name."""
    register_stub(runner, "stub-case", make_script(tmp_path, 'echo "STUB-STATUS: true"\n'),
                  name_of_tool="STUB-Case")

    outcome = runner.run_step(Step("stub-case", "all"), group)

    assert (outcome.verdict, outcome.component) == ("true", "STUB-Case")
    lines = capsys.readouterr().out.splitlines()
    assert "Tool name: STUB-Case Status: true Exit code: 0" in lines
    assert "Tool name: STUB-Case Result: true" in lines
