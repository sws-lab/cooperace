"""scripts/download-tools.py: which components tools.txt makes it install,
how it checks the DOIs against fm-tools before installing, and the record
tools/<name>/.doi. The network and fm_tools are replaced by stubs."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download-tools.py"
spec = importlib.util.spec_from_file_location("download_tools", SCRIPT)
dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dt)

GOBLINT_DATA = {"versions": [
    {"version": "svcomp26", "doi": "10.5281/zenodo.1"},
    {"version": "svcomp26-validation", "doi": "10.5281/zenodo.1"},
    {"version": "svcomp25", "doi": "10.5281/zenodo.2"},
    {"version": "by-url", "url": "https://example.org/g.zip"},
]}
FM_TOOLS = {"goblint": GOBLINT_DATA, "dartagnan": {"versions": [{"version": "svcomp26", "doi": "10.5281/zenodo.3"}]}}


def test_parse_tools_file():
    text = "# the lock\ngoblint: 10.5281/zenodo.1\n\n  dartagnan :10.5281/zenodo.3  \n"
    assert dt.parse_tools_file(text) == {"goblint": "10.5281/zenodo.1", "dartagnan": "10.5281/zenodo.3"}


@pytest.mark.parametrize("text", ["goblint\n", "goblint:\n", ": 10.5281/zenodo.1\n",
                                  "goblint: 1\ngoblint: 2\n"])
def test_parse_tools_file_refuses_a_bad_line(text):
    with pytest.raises(ValueError):
        dt.parse_tools_file(text)


def make_tool(root, name, record):
    (root / name).mkdir(parents=True)
    if record is not None:
        (root / name / ".doi").write_text(record + "\n")


def test_stale_tools(tmp_path):
    make_tool(tmp_path, "current", "10.5281/zenodo.1")
    make_tool(tmp_path, "other", "10.5281/zenodo.9")
    make_tool(tmp_path, "unrecorded", None)
    wanted = {name: "10.5281/zenodo.1" for name in ("current", "other", "unrecorded", "absent")}
    assert dt.stale_tools(wanted, tmp_path) == {"other": "10.5281/zenodo.9", "unrecorded": None, "absent": None}


def test_version_lookup():
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.1") == "svcomp26"
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.2") == "svcomp25"
    assert dt.version_id_for(GOBLINT_DATA, "https://example.org/g.zip") == "by-url"
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.99") is None
    assert dt.version_id_for({}, "10.5281/zenodo.1") is None
    assert dt.listed_references(GOBLINT_DATA)[:3] == ["10.5281/zenodo.1", "10.5281/zenodo.1", "10.5281/zenodo.2"]


class Setup:
    """A repository with tools.txt, a tools/ directory and a stub for the
    fm-tools lookup and for the installation."""

    def __init__(self, tmp_path, monkeypatch, lock):
        self.tools = tmp_path / "tools"
        self.lock = tmp_path / "tools.txt"
        self.lock.write_text(lock)
        self.loaded = []
        self.installed = []
        monkeypatch.setattr(dt, "DOI_FILE", self.lock)
        monkeypatch.setattr(dt, "TOOLS_ROOT", self.tools)
        monkeypatch.setattr(dt, "load_fm_tool_data", self.load)
        monkeypatch.setattr(dt, "install", lambda *args: self.installed.append(args[0]))

    def load(self, name, fm_tools_dir):
        self.loaded.append(name)
        return FM_TOOLS[name]

    def run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["download-tools.py", *args])
        dt.main()


def test_nothing_is_looked_up_when_the_records_match(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    setup.run(monkeypatch)
    assert setup.loaded == [] and setup.installed == []
    assert "match tools.txt" in capsys.readouterr().out


def test_stale_components_are_installed(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\ndartagnan: 10.5281/zenodo.3\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    make_tool(setup.tools, "dartagnan", None)
    setup.run(monkeypatch)
    assert setup.loaded == ["dartagnan"]
    assert setup.installed == ["dartagnan"]


def test_a_doi_fm_tools_does_not_list_installs_nothing(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "dartagnan: 10.5281/zenodo.3\ngoblint: 10.5281/zenodo.99\n")
    with pytest.raises(SystemExit) as stop:
        setup.run(monkeypatch)
    assert "goblint: fm-tools does not list 10.5281/zenodo.99" in str(stop.value)
    assert "10.5281/zenodo.1" in str(stop.value)
    assert setup.installed == []


def test_check_and_dry_run_install_nothing(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n")
    with pytest.raises(SystemExit):
        setup.run(monkeypatch, "--check")
    assert setup.loaded == [] and setup.installed == []
    setup.run(monkeypatch, "--dry-run")
    assert setup.loaded == ["goblint"] and setup.installed == []


def fake_fm_tools(monkeypatch, download):
    """Stubs of the three fm_tools modules install imports; `download` is
    called with the target directory in place of the real download."""
    class Version:
        def __init__(self, tool, version_id):
            self.version_id = version_id

        def download_and_install_into(self, target, delegate, show_loading_bar):
            download(target, self.version_id)

    for module, attribute, value in [("fm_tools.download", "DownloadDelegate", lambda: None),
                                     ("fm_tools.fmtool", "FmTool", lambda data: data),
                                     ("fm_tools.fmtoolversion", "FmToolVersion", Version)]:
        stub = types.ModuleType(module)
        setattr(stub, attribute, value)
        monkeypatch.setitem(sys.modules, module, stub)


def test_install_records_the_doi_after_the_download(tmp_path, monkeypatch):
    def download(target, version_id):
        target.mkdir()
        (target / "tool").write_text(version_id)

    fake_fm_tools(monkeypatch, download)
    dt.install("goblint", GOBLINT_DATA, "svcomp26", "10.5281/zenodo.1", tmp_path / "tools")
    assert (tmp_path / "tools" / "goblint" / ".doi").read_text() == "10.5281/zenodo.1\n"
    assert dt.installed_doi(tmp_path / "tools" / "goblint") == "10.5281/zenodo.1"


def test_a_failed_download_leaves_no_record(tmp_path, monkeypatch):
    def download(target, version_id):
        target.mkdir()
        raise OSError("interrupted")

    fake_fm_tools(monkeypatch, download)
    with pytest.raises(OSError):
        dt.install("goblint", GOBLINT_DATA, "svcomp26", "10.5281/zenodo.1", tmp_path / "tools")
    assert dt.stale_tools({"goblint": "10.5281/zenodo.1"}, tmp_path / "tools") == {"goblint": None}
