# SPDX-FileCopyrightText: 2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""scripts/component-smoke.py: the report logic on fake outputs (parsing a
CoOpeRace run, judging a task, the single-component conf, the sampler's
cgroup peaks, the list of written files) and the unpacking of an archive.
Real runs are the smoke itself and need the competition image."""
import importlib.util
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from src.cooperace import config
from src.cooperace.components import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "component-smoke.py"
spec = importlib.util.spec_from_file_location("component_smoke", SCRIPT)
cs = importlib.util.module_from_spec(spec)
sys.modules["component_smoke"] = cs  # dataclasses look their module up there
spec.loader.exec_module(cs)

CONF = json.loads((ROOT / "conf" / "svcomp27.json").read_text())

# The output of a CoOpeRace run of sv-sanitizers alone (a run of the smoke, shortened)
TRUE_RUN = """\
Task limit of sv-sanitizers: 2048 tasks
CPU-time limit of sv-sanitizers: 30 s
---SV-sanitizers logs---
         U time@GLIBC_2.0
SV-COMP result: true
---end of SV-sanitizers logs---
Tool name: SV-sanitizers Status: true Exit code: 0
Tool name: SV-sanitizers Result: true
Tool name: SV-sanitizers Measured: cputime=0.20s walltime=0.34s memory=26107904B terminationreason=none
CoOpeRace answer: sv-sanitizers true, bracket 1, witness /tmp/cooperace-x/SV-sanitizers-y/witness.yml
CoOpeRace result from: SV-sanitizers
CoOpeRace verdict: true
"""


def run_of(status, result, verdict, *, output=("SV-COMP result: x",), termination="none", cpu="1.00s",
           extra=()):
    """The output of a one-component CoOpeRace run with these values."""
    lines = ["---Tool logs---", *output, "---end of Tool logs---",
             f"Tool name: Tool Status: {status} Exit code: 0", f"Tool name: Tool Result: {result}",
             f"Tool name: Tool Measured: cputime={cpu} walltime=2.00s memory=100000000B "
             f"terminationreason={termination}", *extra, f"CoOpeRace verdict: {verdict}"]
    return cs.parse_run("\n".join(lines) + "\n")


def task(prop="no-data-race", expected="false", prerun="false(no-data-race)", doi="10.5281/zenodo.1"):
    return {"id": "x-01", "property": prop, "role": "false", "task": "c/t.yml", "expected": expected,
            "prerun_doi": doi, "prerun_status": prerun, "prerun_cpu": "1.0", "prerun_mem": "100000000"}


def judge(task_row, run, *, same_archive=True, trusts="both", needs_witness=False, witnesses=(), delivered=False,
          lint=None, outside=(), tasks_peak=10, task_limit=1024, takes_no_witness=False):
    return cs.evaluate(task_row, run, same_archive=same_archive, trusts=trusts, needs_witness=needs_witness,
                       witnesses=list(witnesses), delivered=delivered, lint=lint or {}, outside=list(outside),
                       tasks_peak=tasks_peak, task_limit=task_limit, takes_no_witness=takes_no_witness)


def witness(name="witness.yml", named=True, passes=True):
    return {"name": name, "path": "/w/" + name, "named": named, "passes": passes,
            "entry_type": "violation_sequence", "format_version": "2.2"}


def test_parse_run_reads_the_protocol_lines():
    run = cs.parse_run(TRUE_RUN)
    assert (run.status, run.exit_code, run.result, run.verdict) == ("true", "0", "true", "true")
    assert (run.cpu, run.wall, run.memory, run.termination) == (0.2, 0.34, 26107904, None)
    assert run.component_output == ["         U time@GLIBC_2.0", "SV-COMP result: true"]
    assert run.limits == ["Task limit of sv-sanitizers: 2048 tasks", "CPU-time limit of sv-sanitizers: 30 s"]
    assert not run.errors and not run.ignored and not run.probe_failures


def test_parse_run_reads_guards_probe_failures_and_errors():
    run = cs.parse_run("CoOpeRace: the task calls pthread_once: Deagle:true is ignored\n"
                       "CoOpeRace: the version of ULTIMATE GemCutter could not be determined (no version): going on\n"
                       "CoOpeRace: error: component 'Deagle': not installed\n")
    assert run.ignored and run.probe_failures and run.errors
    assert run.status is None and run.verdict is None


def test_a_correct_answer_with_its_witness_passes():
    notes, failures = judge(task(), run_of("false(no-data-race)", "false", "false(no-data-race)"),
                            needs_witness=True, witnesses=[witness()], delivered=True, lint={"witness.yml": 0})
    assert failures == [] and notes == []


def test_a_plain_false_counts_on_a_property_without_sub_properties():
    notes, failures = judge(task(), run_of("false", "false", "false(no-data-race)"),
                            witnesses=[witness()], delivered=True)
    assert failures == ["differs from this archive's pre-run: false against false(no-data-race)"]
    notes, failures = judge(task(prerun="false"), run_of("false", "false", "false(no-data-race)"))
    assert failures == []


def test_a_wrong_answer_fails():
    _notes, failures = judge(task(expected="true", prerun="true"), run_of("false(no-data-race)", "false", "x"))
    assert "wrong: false(no-data-race) where true is expected" in failures


def test_valid_memsafety_needs_the_sub_property():
    row = task("valid-memsafety", "false(valid-deref)", "false(valid-deref)")
    _notes, failures = judge(row, run_of("false(valid-memtrack)", "false", "false(valid-memtrack)"))
    assert any(failure.startswith("wrong:") for failure in failures)


def test_an_answer_lost_to_an_unread_output_line_fails_and_quotes_the_line():
    run = run_of("ERROR (1)", "unknown", "unknown", output=["Result: FALSE(data-race)", "done"])
    _notes, failures = judge(task(), run)
    assert failures == ["lost: ERROR (1) where this archive's pre-run answered false(no-data-race); "
                        "output has 'Result: FALSE(data-race)'"]


def test_an_answer_lost_at_a_limit_is_a_note():
    run = run_of("TIMEOUT", "unknown", "unknown", termination="cputime", output=("still running",))
    notes, failures = judge(task(), run, same_archive=False)
    assert failures == []
    assert notes == ["lost: TIMEOUT where the bundled archive's pre-run answered false(no-data-race) "
                     "(ended by its cputime limit)"]


def test_a_verdict_status_of_another_property_is_refused():
    run = run_of("false(unreach-call)", "unknown", "unknown")
    _notes, failures = judge(task(prerun="unknown"), run)
    assert failures == ["status 'false(unreach-call)' is not accepted as a verdict on no-data-race"]


def test_a_missing_required_witness_and_a_witness_of_another_name_fail():
    run = run_of("false(no-data-race)", "false", "false(no-data-race)")
    _notes, failures = judge(task(), run, needs_witness=True,
                             witnesses=[witness("error-witness.yml", named=False, passes=None)])
    assert "witness file not named by the registry: error-witness.yml" in failures
    assert "no witness delivered for false, which needs one (error-witness.yml written)" in failures


def test_a_component_whose_witness_the_registry_takes_not_gets_notes():
    run = run_of("false(no-data-race)", "false", "false(no-data-race)")
    notes, failures = judge(task(), run, needs_witness=True, takes_no_witness=True,
                            witnesses=[witness("witness.yml", named=False, passes=None)])
    assert failures == []
    assert notes == ["the registry takes no witness from this component; it wrote witness.yml"]


def test_witnesslint_failure_and_a_refused_witness():
    run = run_of("false(valid-deref)", "false", "false(valid-deref)")
    row = task("valid-memsafety", "false(valid-deref)", "false(valid-deref)")
    notes, failures = judge(row, run, witnesses=[witness(passes=False)], lint={"witness.yml": 1})
    assert failures == ["WitnessLint exit 1 on witness.yml"]
    assert notes == ["CoOpeRace's witness check refuses witness.yml for false(valid-deref) "
                     "(the verdict needs none, so it is delivered without)"]


def test_no_verdict_line_and_a_guard():
    _notes, failures = judge(task(), cs.parse_run("Traceback (most recent call last):\n"))
    assert failures[0].startswith("CoOpeRace ended without a verdict line")
    notes, failures = judge(task(), cs.parse_run("CoOpeRace: the task calls pthread_barrier_wait: "
                                                 "Dartagnan:false is ignored\nCoOpeRace verdict: unknown\n"))
    assert failures == [] and notes[0].startswith("guard: the task calls pthread_barrier_wait")


def test_gained_answers_tasks_and_written_files():
    run = run_of("true", "true", "true")
    notes, failures = judge(task(expected="true", prerun="unknown"), run, trusts="false", tasks_peak=600,
                            outside=["/tmp/hsperfdata/1", "/var/x"])
    assert "gained: true where this archive's pre-run gave unknown" in notes
    assert "the default conf does not trust true of this component here" in notes
    assert any("2 files written outside" in note and "/var/x" in note for note in notes)
    assert failures == ["600 tasks, over half its task limit 1024"]


def test_version_probe_failure_fails():
    run = run_of("true", "true", "true", extra=["CoOpeRace: the version of ULTIMATE GemCutter could not be "
                                                "determined (no answer in 60 s): going on without it"])
    _notes, failures = judge(task(expected="true", prerun="true"), run)
    assert failures == ["version probe: the version of ULTIMATE GemCutter could not be determined "
                        "(no answer in 60 s): going on without it"]


@pytest.mark.parametrize("name", sorted({spec.name for spec in REGISTRY.values()}))
def test_the_single_component_conf_is_a_conf_cooperace_loads(name):
    properties = cs.conf_properties(CONF, name) or list(cs.PROPERTIES)
    conf = cs.single_component_conf(CONF, name, properties)
    strategies = config.load_strategies(conf, REGISTRY)
    assert set(strategies) == set(properties)
    for strategy in strategies.values():
        assert strategy.tools == (name,)


def test_the_single_component_conf_keeps_the_component_s_limits_and_guards():
    conf = cs.single_component_conf(CONF, "Goblint", ["unreach-call"])["properties"]["unreach-call"]
    assert conf["phases"] == [{"tools": ["Goblint"], "wallTimeLimit": 45}]
    assert conf["cpuTimeLimits"] == {"Goblint": 30} and conf["memoryLimits"] == {"Goblint": "45%"}
    conf = cs.single_component_conf(CONF, "Dartagnan", ["no-data-race"])["properties"]["no-data-race"]
    assert conf["phases"] == [{"tools": ["Dartagnan"]}] and conf["memoryLimits"] == {"Dartagnan": "80%"}
    assert set(conf["ignoreIfCalls"]) == {"Dartagnan", "Dartagnan:true"}
    assert cs.conf_properties(CONF, "iekke") == []
    assert cs.conf_trusts(CONF, "Goblint", "unreach-call") == "true"
    assert cs.conf_trusts(CONF, "Dartagnan", "no-data-race") == "both"
    assert cs.conf_trusts(CONF, "iekke", "no-data-race") == "none"


def test_cgroup_peaks_take_the_leaf_cgroups_below_the_run(tmp_path):
    lines = ["1.0\t/run1\t900\t3000000000\t1\tpython3,",
             "1.0\t/run1/inner\t880\t2900000000\t1\t",
             "1.0\t/run1/inner/cooperace/comp\t31\t200000000\t0\tjava,",
             "2.0\t/run1/inner/cooperace/comp\t35\t250000000\t0\tjava,"]
    path = tmp_path / "x.cgroups.tsv"
    path.write_text("\n".join(lines) + "\n")
    assert cs.cgroup_peaks(path) == (35, 250000000)
    assert cs.cgroup_peaks(path.with_name("none.tsv")) == (None, None)


def test_written_outside_and_the_manifest(tmp_path):
    manifest = tmp_path / "m.tsv"
    manifest.write_text("#cwd\t/t/tools/x\t/tmp/w\n/t/tools/x/witness.yml\t10\n/tmp/a\t5\n")
    cwd, files = cs.read_manifest(manifest)
    assert cwd == "/t/tools/x" and files == [("/t/tools/x/witness.yml", 10), ("/tmp/a", 5)]
    assert cs.written_outside(files, "/t/tools/x") == ["/tmp/a"]


def test_raw_status_and_witness_facts(tmp_path):
    assert cs.raw_status("witness invalid (false(no-data-race))") == "false(no-data-race)"
    assert cs.raw_status("TIMEOUT") == "TIMEOUT"
    path = tmp_path / "w.yml"
    path.write_text("- entry_type: violation_sequence\n  metadata:\n    format_version: '2.2'\n")
    assert cs.witness_facts(path) == {"entry_type": "violation_sequence", "format_version": "2.2"}
    path.write_text('[\n {\n  "entry_type": "violation_sequence",\n  "metadata": {\n   "format_version": "2.2"\n')
    assert cs.witness_facts(path) == {"entry_type": "violation_sequence", "format_version": "2.2"}
    assert cs.is_witness_name("output/witness.yml") and not cs.is_witness_name("witness.txt")


def test_unpack_keeps_modes_and_wants_one_top_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "MIN_FREE_BYTES", 0)
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as out:
        info = zipfile.ZipInfo("tool-1.0/run.sh")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        out.writestr(info, "#!/bin/sh\n")
        out.writestr("tool-1.0/witness.yml", "stale\n")
    directory = cs.unpack(archive, tmp_path / "new", "tool")
    assert directory == tmp_path / "new" / "tool"
    assert (directory / "run.sh").stat().st_mode & 0o777 == 0o755
    assert cs.shipped_witnesses(directory) == ["witness.yml"]
    two = tmp_path / "b.zip"
    with zipfile.ZipFile(two, "w") as out:
        out.writestr("a/x", "")
        out.writestr("b/y", "")
    with pytest.raises(cs.SmokeError, match="2 top-level entries"):
        cs.unpack(two, tmp_path / "other", "tool")


def test_the_task_file_names_known_components_and_properties():
    rows = [row for component in ("goblint", "dartagnan", "sv-sanitizers", "deagle", "ugemcutter", "ikos-conc")
            for row in cs.read_tasks(cs.TASKS_FILE, component, None)]
    assert rows
    for row in rows:
        assert row["property"] in cs.PROPERTIES and row["task"].startswith("c/") and row["task"].endswith(".yml")
        spec = cs.registry_entry(sys_modules_components(), row["component"])
        assert row["property"] in cs.conf_properties(CONF, spec.name)


def sys_modules_components():
    import src.cooperace.components as components
    return components
