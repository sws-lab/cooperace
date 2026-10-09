"""tests/integration/run.py: which BenchExec the runs of a component alone
use. subprocess.run is replaced by a stub, so no BenchExec is started."""
import importlib.util
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
    runs = {"alone-goblint": dict(kind="alone", tasks=[task]),
            "only-goblint": dict(kind="only", tasks=[task], conf=cdir / "conf" / "only-goblint.json")}
    monkeypatch.setattr(run, "write_definition", lambda *args, **kwargs: None)
    args = SimpleNamespace(timelimit=100, memlimit="4GB", cores=2, parallel=1, allowed_cores=None)

    run.verify(args, runs, cdir, tmp_path / "out")

    wheel = str(cdir / "lib" / "benchexec-3.35-py3-none-any.whl")
    assert [(cmd[0], cwd, pythonpath) for cmd, cwd, pythonpath in started] == [
        ("benchexec", cdir / "tools" / "goblint", wheel),
        ("benchexec", cdir, None)]
