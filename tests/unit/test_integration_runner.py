# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""tests/integration/run.py: which BenchExec and which options the runs of a
component alone use, which witness files a `false` of CoOpeRace may deliver
(check 6), which witness file is collected, and what check 9 reads from
CoOpeRace's output.  subprocess.run is replaced by a stub, so no BenchExec is
started."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tests" / "integration" / "run.py"
spec = importlib.util.spec_from_file_location("integration_run", SCRIPT)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def make_checkout(tmp_path, wheels=("benchexec-3.35-py3-none-any.whl",)):
    (tmp_path / "lib").mkdir()
    for name in wheels:
        (tmp_path / "lib" / name).touch()
        (tmp_path / "lib" / (name + ".license")).touch()
    (tmp_path / "tools" / "goblint").mkdir(parents=True)
    return tmp_path


def test_bundled_benchexec_is_the_one_wheel(tmp_path):
    cdir = make_checkout(tmp_path)
    assert run.bundled_benchexec(cdir) == cdir / "lib" / "benchexec-3.35-py3-none-any.whl"


@pytest.mark.parametrize("wheels", [(), ("benchexec-3.31-py3-none-any.whl", "benchexec-3.35-py3-none-any.whl")])
def test_bundled_benchexec_needs_exactly_one_wheel(tmp_path, wheels):
    with pytest.raises(run.CouldNotRun, match="expected one lib/benchexec"):
        run.bundled_benchexec(make_checkout(tmp_path, wheels))


def test_environment_puts_the_wheel_first_on_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    assert run.benchexec_environment("/w/benchexec.whl")["PYTHONPATH"] == f"/w/benchexec.whl{os.pathsep}/elsewhere"
    monkeypatch.delenv("PYTHONPATH")
    assert run.benchexec_environment("/w/benchexec.whl")["PYTHONPATH"] == "/w/benchexec.whl"


def test_environment_without_a_wheel_keeps_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    assert run.benchexec_environment()["PYTHONPATH"] == "/elsewhere"
    monkeypatch.delenv("PYTHONPATH")
    assert "PYTHONPATH" not in run.benchexec_environment()


def test_components_alone_run_under_the_bundled_wheel(tmp_path, monkeypatch):
    """The commands `verify` starts: the wheel is on PYTHONPATH for the run of
    a component alone, and not for the runs of CoOpeRace's configurations."""
    cdir = make_checkout(tmp_path)
    started = []

    def fake_run(cmd, cwd, env, **kwargs):
        started.append((cmd, cwd, env.get("PYTHONPATH")))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    task = {"id": "t"}
    runs = {"alone-goblint": dict(kind="alone", tasks=[task], options=[]),
            "only-goblint": dict(kind="only", tasks=[task], conf=cdir / "conf" / "only-goblint.json")}
    monkeypatch.setattr(run, "write_definition", lambda *args, **kwargs: None)
    args = SimpleNamespace(timelimit=100, memlimit="4GB", cores=2, parallel=1, allowed_cores=None,
                           cooperace_option=[], benchexec_args="")

    run.verify(args, runs, cdir, tmp_path / "out")

    wheel = str(cdir / "lib" / "benchexec-3.35-py3-none-any.whl")
    assert [(cmd[0], cwd, pythonpath) for cmd, cwd, pythonpath in started] == [
        ("benchexec", cdir / "tools" / "goblint", wheel),
        ("benchexec", cdir, None)]


def test_cooperace_options_and_benchexec_args_reach_the_cooperace_runs(tmp_path, monkeypatch):
    cdir = tmp_path / "coop"
    (cdir / "lib").mkdir(parents=True)
    (cdir / "lib" / "benchexec-3.35-py3-none-any.whl").touch()
    started, definitions = [], []
    monkeypatch.setattr(run.subprocess, "run",
                        lambda cmd, cwd, env, **kwargs: started.append(cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(run, "write_definition",
                        lambda path, module, options, *args, **kwargs: definitions.append(options))
    runs = {"svcomp27": dict(kind="config", tasks=[{"id": "t"}], conf=cdir / "conf" / "svcomp27.json")}
    args = SimpleNamespace(timelimit=100, memlimit="4GB", cores=2, parallel=1, allowed_cores=None,
                           cooperace_option=["--arch", "LP64"],
                           benchexec_args="--read-only-dir / --overlay-dir ./ --hidden-dir /home/")

    run.verify(args, runs, cdir, tmp_path / "out")

    assert definitions == [{"svcomp27": [("--conf", str(cdir / "conf" / "svcomp27.json")),
                                         ("--arch", "LP64")]}]
    assert started[0][-7:-1] == ["--read-only-dir", "/", "--overlay-dir", "./", "--hidden-dir", "/home/"]


def write_options(cdir, doi_installed, doi_recorded):
    (cdir / "tools" / "goblint" / ".doi").write_text(doi_installed + "\n")
    (cdir / "tools-options.json").write_text(json.dumps(
        {"goblint": {"doi": doi_recorded, "version": "svcomp27",
                     "options": ["--portfolio-conf", "conf/svcomp27/seq.txt"]}}))


def test_a_component_alone_gets_the_options_of_tools_options_json(tmp_path):
    cdir = make_checkout(tmp_path)
    write_options(cdir, "10.5281/zenodo.1", "10.5281/zenodo.1")
    assert run.reference_options(cdir, "goblint") == ["--portfolio-conf", "conf/svcomp27/seq.txt"]


def test_options_of_another_doi_are_refused(tmp_path):
    cdir = make_checkout(tmp_path)
    write_options(cdir, "10.5281/zenodo.2", "10.5281/zenodo.1")
    with pytest.raises(run.CouldNotRun, match="records the options of 10.5281/zenodo.1"):
        run.reference_options(cdir, "goblint")


GRAPHML = ('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
           '<key id="p" attr.name="producer"/><graph><data key="p">X</data></graph></graphml>')
YAML = "- entry_type: violation_sequence\n  metadata:\n    producer:\n      name: Dartagnan\n"


def false_run(tmp_path, files):
    """A `false` run of CoOpeRace whose result files are `files` ({name: text})."""
    root = tmp_path / "files"
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    root.mkdir(exist_ok=True)
    return dict(verdict=False, reason=None, files=root, log=tmp_path / "log")


OUTPUT = ["CoOpeRace result from: Dartagnan", "CoOpeRace verdict: false"]
YAML_FORMAT = run.WITNESS_FORMATS["yaml-2.2"]


@pytest.mark.parametrize("files", [{"witness.yml": YAML}, {"witness.yml": YAML, "witness.graphml": GRAPHML}])
def test_a_false_may_deliver_one_file_per_format(tmp_path, files):
    assert run.protocol_problems(false_run(tmp_path, files), OUTPUT, YAML_FORMAT) == []


@pytest.mark.parametrize("files, problem", [
    ({"witness.graphml": GRAPHML}, "no witness.yml in the result files (witness.graphml)"),
    ({}, "no witness.yml in the result files (none)"),
    ({"witness.yml": YAML, "tools/dartagnan/output/witness.yml": YAML},
     "2 files in the format of witness.yml: tools/dartagnan/output/witness.yml, witness.yml"),
    ({"witness.yml": GRAPHML}, "witness.yml does not have the format its name says"),
    ({"witness.yml": YAML, "witness.txt": "x"}, "witness.txt is in none of the formats"),
])
def test_a_false_without_the_formats_file_or_with_two_of_a_format_fails(tmp_path, files, problem):
    problems = run.protocol_problems(false_run(tmp_path, files), OUTPUT, YAML_FORMAT)
    assert any(p.startswith(problem) for p in problems), problems


def test_the_producer_of_a_yaml_witness(tmp_path):
    (tmp_path / "witness.yml").write_text(YAML)
    assert run.yaml_producer(tmp_path / "witness.yml") == "Dartagnan"
    (tmp_path / "witness.yml").write_text("not: a list")
    assert run.yaml_producer(tmp_path / "witness.yml") is None


def test_cooperace_s_witness_is_the_one_at_the_top_of_its_result_files(tmp_path):
    """A file of the same name deeper in CoOpeRace's result files (a component's
    own output directory) is not what CoOpeRace delivered; a component alone
    may write its witness into such a directory."""
    for rundef in ("only-dartagnan", "alone-dartagnan"):
        (tmp_path / rundef / "tools" / "dartagnan" / "output").mkdir(parents=True)
        (tmp_path / rundef / "tools" / "dartagnan" / "output" / "witness.yml").write_text(YAML)
    results = {(rd, "t"): dict(verdict=False, files=tmp_path / rd) for rd in ("only-dartagnan", "alone-dartagnan")}
    runs = {"only-dartagnan": dict(kind="only"), "alone-dartagnan": dict(kind="alone")}
    run.collect_witnesses(results, runs, YAML_FORMAT, tmp_path / "out")
    assert results[("only-dartagnan", "t")]["witness"] is None
    collected = tmp_path / "out" / "witnesses" / "alone-dartagnan" / "t.yml" / "witness.yml"
    assert results[("alone-dartagnan", "t")]["witness"] == collected


# --- check 9: the verdict and the stops ----------------------------------------

GOBLINT_1S = {"properties": {"no-data-race": {
    "phases": [["Goblint"], ["Dartagnan", "ULTIMATE Automizer"]],
    "answers": [["Goblint:true"], ["Dartagnan"], ["ULTIMATE Automizer"]],
    "witnesses": ["Dartagnan", "ULTIMATE Automizer"]}}}


def block(name, status, result=None):
    """The lines of a component's block as print_component_run prints them."""
    lines = [f"---{name} logs---", f"---end of {name} logs---", f"Tool name: {name} Status: {status} Exit code: 0"]
    return lines + ([f"Tool name: {name} Result: {result}"] if result else [])


def test_check_9_accepts_a_higher_bracket_overriding_a_lower_false():
    # The case the earlier check 9 reported: Goblint's true decides above
    # sv-sanitizers' false, which ran beside it.
    config = {"properties": {"no-data-race": {
        "phases": [["sv-sanitizers", "Goblint"]], "answers": [["Goblint:true"], ["sv-sanitizers:false"]],
        "witnesses": ["sv-sanitizers"]}}}
    lines = (block("SV-sanitizers", "false", "false")
             + ["CoOpeRace answer: sv-sanitizers false, bracket 2, witness /w/witness.yml"]
             + block("Goblint", "true", "true") + ["CoOpeRace answer: Goblint true, bracket 1",
                                                   "CoOpeRace result from: Goblint", "CoOpeRace verdict: true"])

    assert run.stop_problems({"config": config}, lines) == []


def test_check_9_accepts_a_stopped_lower_bracket():
    lines = (block("Goblint", "TIMEOUT", "unknown") + block("Dartagnan", "false", "false")
             + ["CoOpeRace answer: Dartagnan false, bracket 2, witness /w/witness.yml"]
             + block("ULTIMATE Automizer", "stopped by CoOpeRace")
             + ["CoOpeRace result from: Dartagnan", "CoOpeRace verdict: false"])

    assert run.stop_problems({"config": GOBLINT_1S}, lines) == []


def test_check_9_reports_a_stopped_higher_bracket_and_a_wrong_verdict():
    lines = (block("Goblint", "stopped by CoOpeRace") + block("Dartagnan", "false", "false")
             + ["CoOpeRace answer: Dartagnan false, bracket 2, no witness that passes the check"]
             + ["CoOpeRace result from: Dartagnan", "CoOpeRace verdict: false"])

    assert run.stop_problems({"config": GOBLINT_1S}, lines) == [
        "the verdict is false, but the answers {'Dartagnan': ('false', False)} decide unknown",
        "Goblint was stopped, but it has an entry above bracket 2, which decided"]


def test_check_9_accepts_a_contradiction_and_a_wall_time_stop():
    config = {"properties": {"no-data-race": {
        "phases": [{"tools": ["A", "B", "C"], "wallTimeLimit": 5}], "answers": [["C:true"], ["A", "B"]],
        "witnesses": []}}}
    lines = (block("A", "true", "true") + ["CoOpeRace answer: A true, bracket 2"]
             + block("B", "false", "false") + ["CoOpeRace answer: B false, bracket 2, witness /w/w.yml"]
             + ["CoOpeRace: the wall-time limit of phase 1, 5 s, has passed: stopping C"]
             + block("C", "stopped by CoOpeRace") + ["CoOpeRace verdict: ERROR (contradiction: A true, B false)"])

    assert run.stop_problems({"config": config}, lines) == []
    assert run.VERDICT_LINE.fullmatch(lines[-1])
