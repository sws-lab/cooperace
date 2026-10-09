"""The registry of components: its entries, their options in
tools-options.json, the lazy import of the tool-info modules, and how a step
reports a component that cannot be set up."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import make_script, register_stub

from src.cooperace.components import (
    DEFAULT_WITNESS,
    OPTIONS_FILE,
    REGISTRY,
    ComponentSpec,
    ComponentVersion,
    OptionsFileError,
    read_component_versions,
)
from src.cooperace.config import Step

ROOT = Path(__file__).resolve().parents[2]

# scripts/download-tools.py, which writes tools-options.json from the lock files
_spec = importlib.util.spec_from_file_location("download_tools", ROOT / "scripts" / "download-tools.py")
download_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(download_tools)

# The directory under tools/ of every component, its fm-tools name, as
# download-tools.py unpacks them.
DIRECTORIES = {
    "Goblint": "goblint",
    "Deagle": "deagle",
    "Dartagnan": "dartagnan",
    "ULTIMATE Automizer": "uautomizer",
    "ULTIMATE GemCutter": "ugemcutter",
    "ULTIMATE Taipan": "utaipan",
    "nacpa": "nacpa",
    "CPAchecker": "cpachecker",
    "sv-sanitizers": "sv-sanitizers",
    "RacerF": "racerf",
}


def test_the_registry_has_every_component_under_its_own_name():
    assert set(REGISTRY) == set(DIRECTORIES)
    assert all(spec.name == name for name, spec in REGISTRY.items())


def test_every_component_has_its_directory():
    assert {name: spec.directory for name, spec in REGISTRY.items()} == DIRECTORIES


def test_the_options_of_the_components_of_svcomp26_are_those_of_their_fm_tools_entries():
    """The benchexec_toolinfo_options of version svcomp26 of goblint.yml,
    dartagnan.yml and uautomizer.yml in fm-tools (91acb17e)."""
    versions = read_component_versions(str(ROOT / OPTIONS_FILE))

    assert versions["goblint"] == ComponentVersion("10.5281/zenodo.17642247", "svcomp26",
                                                   ("--portfolio-conf", "conf/svcomp26/seq.txt"))
    assert versions["dartagnan"] == ComponentVersion("10.5281/zenodo.17723884", "svcomp26", ())
    assert versions["uautomizer"] == ComponentVersion("10.5281/zenodo.17735224", "svcomp26", ("--full-output",))


def test_every_component_has_options_and_the_options_file_matches_the_lock_files():
    archive, pool = download_tools.read_lock_files(ROOT / "tools.txt", ROOT / "tools-pool.txt")
    versions = read_component_versions(str(ROOT / OPTIONS_FILE))

    assert {spec.directory for spec in REGISTRY.values()} == set(versions)
    assert {name: version.doi for name, version in versions.items()} == {**archive, **pool}
    assert list(versions) == [*archive, *pool]


def test_the_archive_lock_holds_the_components_of_svcomp26_only():
    archive, _pool = download_tools.read_lock_files(ROOT / "tools.txt", ROOT / "tools-pool.txt")
    conf = json.loads((ROOT / "conf" / "svcomp26.json").read_text())
    names = {name for stage in conf["tools"] for entry in (stage if isinstance(stage, list) else [stage])
             for name in entry}

    assert set(archive) == {REGISTRY[name].directory for name in names}


def test_read_component_versions_ignores_further_keys(tmp_path):
    path = tmp_path / OPTIONS_FILE
    path.write_text(json.dumps({"goblint": {"doi": "d", "version": "v", "options": ["-a"], "later": {}}}))

    assert read_component_versions(str(path)) == {"goblint": ComponentVersion("d", "v", ("-a",))}


@pytest.mark.parametrize("content", ["", "[]", '{"g": []}', '{"g": {"doi": "d", "version": "v"}}',
                                     '{"g": {"doi": "d", "version": "v", "options": [1]}}',
                                     '{"g": {"doi": 1, "version": "v", "options": []}}'])
def test_read_component_versions_refuses_another_format(tmp_path, content):
    path = tmp_path / OPTIONS_FILE
    path.write_text(content)

    with pytest.raises(OptionsFileError, match=str(path)):
        read_component_versions(str(path))


def test_a_component_is_run_with_its_options_before_those_of_its_witness(runner, group, tmp_path, capsys):
    script = make_script(tmp_path / "stub", 'echo "args: $*"\necho "STUB-STATUS: true"\n')
    register_stub(runner, "Stub A", script, options=("--from-fm-tools", "x"))

    runner.run_step(Step("Stub A", "all"), group)

    assert "args: --from-fm-tools x" in capsys.readouterr().out.splitlines()


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
