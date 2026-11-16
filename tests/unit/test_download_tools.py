# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""scripts/download-tools.py: which components the lock files tools.txt and
tools-pool.txt make it install, how it checks the DOIs against fm-tools before
installing, the record tools/<name>/.doi, and the options it writes into
tools-options.json. The network and fm_tools are replaced by stubs, and every
file the script writes is redirected into tmp_path."""
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download-tools.py"
spec = importlib.util.spec_from_file_location("download_tools", SCRIPT)
dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dt)

GOBLINT_DATA = {"versions": [
    {"version": "svcomp26-validation", "doi": "10.5281/zenodo.1",
     "benchexec_toolinfo_options": ["--validate", "${witness}"]},
    {"version": "svcomp26", "doi": "10.5281/zenodo.1",
     "benchexec_toolinfo_options": ["--portfolio-conf", "conf/svcomp26/seq.txt"]},
    {"version": "svcomp25", "doi": "10.5281/zenodo.2", "benchexec_toolinfo_options": ["--conf", "svcomp25.json"]},
    {"version": "by-url", "url": "https://example.org/g.zip"},
    {"version": "validator-only", "doi": "10.5281/zenodo.5", "benchexec_toolinfo_options": ["-w", "${witness}"]},
]}
FM_TOOLS = {"goblint": GOBLINT_DATA,
            "dartagnan": {"versions": [{"version": "svcomp26", "doi": "10.5281/zenodo.3",
                                        "benchexec_toolinfo_options": []}]},
            "deagle": {"versions": [{"version": "svcomp26", "doi": "10.5281/zenodo.4",
                                     "benchexec_toolinfo_options": ["--x"]}]},
            #A checker: every version is a witness validator
            "witnesslint": {"versions": [{"version": "2.4-correctness-witnesses-v2", "doi": "10.5281/zenodo.9",
                                          "benchexec_toolinfo_options": ["--witness", "${witness}"]},
                                         {"version": "2.4-violation-witnesses-v2", "doi": "10.5281/zenodo.9",
                                          "benchexec_toolinfo_options": ["--witness", "${witness}"]}]}}


def test_parse_tools_file():
    text = "# the lock\ngoblint: 10.5281/zenodo.1\n\n  dartagnan :10.5281/zenodo.3  \n"
    assert dt.parse_tools_file(text) == {"goblint": "10.5281/zenodo.1", "dartagnan": "10.5281/zenodo.3"}


@pytest.mark.parametrize("text", ["goblint\n", "goblint:\n", ": 10.5281/zenodo.1\n",
                                  "goblint: 1\ngoblint: 2\n"])
def test_parse_tools_file_refuses_a_bad_line(text):
    with pytest.raises(ValueError):
        dt.parse_tools_file(text)


def test_read_lock_files(tmp_path):
    (tmp_path / "a.txt").write_text("goblint: 10.5281/zenodo.1\n")
    (tmp_path / "p.txt").write_text("# pool\ndeagle: 10.5281/zenodo.4\n")
    assert dt.read_lock_files(tmp_path / "a.txt", tmp_path / "p.txt") == (
        {"goblint": "10.5281/zenodo.1"}, {"deagle": "10.5281/zenodo.4"})
    assert dt.read_lock_files(tmp_path / "a.txt", tmp_path / "absent.txt") == ({"goblint": "10.5281/zenodo.1"}, {})
    with pytest.raises(ValueError, match="absent.txt"):
        dt.read_lock_files(tmp_path / "absent.txt", tmp_path / "p.txt")
    (tmp_path / "p.txt").write_text("goblint: 10.5281/zenodo.2\n")
    with pytest.raises(ValueError, match="goblint"):
        dt.read_lock_files(tmp_path / "a.txt", tmp_path / "p.txt")


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


def test_version_lookup_skips_the_versions_of_a_witness_validator():
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.1") == "svcomp26"
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.2") == "svcomp25"
    assert dt.version_id_for(GOBLINT_DATA, "https://example.org/g.zip") == "by-url"
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.5") is None
    assert dt.version_id_for(GOBLINT_DATA, "10.5281/zenodo.99") is None
    assert dt.version_id_for({}, "10.5281/zenodo.1") is None
    assert dt.listed_references(GOBLINT_DATA)[:3] == ["10.5281/zenodo.1", "10.5281/zenodo.1", "10.5281/zenodo.2"]


def test_options_record():
    entry = dt.version_entry_for(GOBLINT_DATA, "10.5281/zenodo.1")
    assert dt.options_record("10.5281/zenodo.1", entry) == {
        "doi": "10.5281/zenodo.1", "version": "svcomp26", "options": ["--portfolio-conf", "conf/svcomp26/seq.txt"]}
    assert dt.options_record("u", dt.version_entry_for(GOBLINT_DATA, "https://example.org/g.zip"))["options"] == []


def record(name):
    """The object of the component `name` in tools-options.json, from FM_TOOLS."""
    doi = FM_TOOLS[name]["versions"][-1]["doi"] if name != "goblint" else "10.5281/zenodo.1"
    return dt.options_record(doi, dt.version_entry_for(FM_TOOLS[name], doi))


class Setup:
    """A repository with tools.txt, tools-pool.txt (`pool`), tools-options.json
    (`options`, a dict, or None for no file), a tools/ directory and a stub for
    the fm-tools lookup and for the installation."""

    def __init__(self, tmp_path, monkeypatch, lock, pool="", options=None, unlisted=None, old_fm_tools=None,
                 checkers=None):
        self.tools = tmp_path / "tools"
        self.lock = tmp_path / "tools.txt"
        self.lock.write_text(lock)
        self.pool = tmp_path / "tools-pool.txt"
        self.pool.write_text(pool)
        self.options = tmp_path / "tools-options.json"
        if options is not None:
            self.options.write_text(json.dumps(options))
        self.unlisted = tmp_path / "tools-unlisted.txt"
        if unlisted is not None:
            self.unlisted.write_text(unlisted)
        #{(name, commit): data} of the entries at older fm-tools commits
        self.old_fm_tools = old_fm_tools or {}
        self.loaded = []
        self.installed = []
        self.checkers = tmp_path / "tools-checkers.txt"
        if checkers is not None:
            self.checkers.write_text(checkers)
        monkeypatch.setattr(dt, "CHECKERS_FILE", self.checkers)
        monkeypatch.setattr(dt, "UNLISTED_FILE", self.unlisted)
        monkeypatch.setattr(dt, "load_fm_tool_data_at", self.load_at)
        monkeypatch.setattr(dt, "DOI_FILE", self.lock)
        monkeypatch.setattr(dt, "POOL_FILE", self.pool)
        monkeypatch.setattr(dt, "OPTIONS_FILE", self.options)
        monkeypatch.setattr(dt, "TOOLS_ROOT", self.tools)
        monkeypatch.setattr(dt, "load_fm_tool_data", self.load)
        monkeypatch.setattr(dt, "install", lambda *args: self.installed.append(args[0]))

    def load(self, name, fm_tools_dir):
        self.loaded.append(name)
        return FM_TOOLS[name]

    def load_at(self, name, fm_tools_dir, revision):
        self.loaded.append(f"{name}@{revision}")
        return self.old_fm_tools[(name, revision)]

    def run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["download-tools.py", *args])
        dt.main()

    def written_options(self):
        return json.loads(self.options.read_text())


def test_nothing_is_looked_up_when_the_records_match(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", options={"goblint": record("goblint")})
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    before = setup.options.read_text()
    setup.run(monkeypatch)
    assert setup.loaded == [] and setup.installed == []
    assert "match the lock files" in capsys.readouterr().out
    assert setup.options.read_text() == before


def test_the_options_of_every_component_of_both_lock_files_are_written_and_only_the_archive_is_installed(
        tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", pool="deagle: 10.5281/zenodo.4\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    setup.run(monkeypatch)
    assert setup.installed == []
    assert setup.written_options() == {"goblint": record("goblint"), "deagle": record("deagle")}
    assert list(setup.written_options()) == ["goblint", "deagle"]


def test_pool_installs_the_pool_too(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", pool="deagle: 10.5281/zenodo.4\n",
                  options={"goblint": record("goblint"), "deagle": record("deagle")})
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    setup.run(monkeypatch, "--pool")
    assert setup.installed == ["deagle"]


def test_options_of_another_doi_and_of_a_component_in_no_lock_file_are_replaced(tmp_path, monkeypatch):
    old = {"doi": "10.5281/zenodo.2", "version": "svcomp25", "options": ["--conf", "svcomp25.json"]}
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\ndartagnan: 10.5281/zenodo.3\n",
                  options={"goblint": old, "dartagnan": record("dartagnan"), "gone": record("deagle")})
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    make_tool(setup.tools, "dartagnan", "10.5281/zenodo.3")
    setup.run(monkeypatch)
    assert setup.loaded == ["goblint"] and setup.installed == []
    assert setup.written_options() == {"goblint": record("goblint"), "dartagnan": record("dartagnan")}


def test_options_only_rewrites_every_object_and_installs_nothing(tmp_path, monkeypatch):
    stale = dict(record("goblint"), options=["--changed-in-fm-tools-since"])
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", options={"goblint": stale})
    setup.run(monkeypatch, "--options-only")
    assert setup.loaded == ["goblint"] and setup.installed == []
    assert setup.written_options() == {"goblint": record("goblint")}


def test_check_compares_the_options_file_with_the_lock_files(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", pool="deagle: 10.5281/zenodo.4\n",
                  options={"goblint": record("goblint")})
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    with pytest.raises(SystemExit) as stop:
        setup.run(monkeypatch, "--check")
    assert "tools-options.json does not match the lock files: deagle" in str(stop.value)
    assert setup.loaded == []

    setup.options.write_text(dt.options_text({"goblint": "", "deagle": ""},
                                             {"goblint": record("goblint"), "deagle": record("deagle")}))
    setup.run(monkeypatch, "--check")
    with pytest.raises(SystemExit, match="deagle"):
        setup.run(monkeypatch, "--check", "--pool")


def test_check_with_fm_tools_compares_the_options_themselves(tmp_path, monkeypatch):
    stale = dict(record("goblint"), options=["--changed-in-fm-tools-since"])
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    setup.options.write_text(dt.options_text({"goblint": ""}, {"goblint": record("goblint")}))
    setup.run(monkeypatch, "--check", "--fm-tools-dir", str(tmp_path))
    setup.options.write_text(dt.options_text({"goblint": ""}, {"goblint": stale}))
    setup.run(monkeypatch, "--check")
    with pytest.raises(SystemExit, match="differ from those of the fm-tools entries"):
        setup.run(monkeypatch, "--check", "--fm-tools-dir", str(tmp_path))
    assert setup.installed == []


def test_stale_components_are_installed(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\ndartagnan: 10.5281/zenodo.3\n",
                  options={"goblint": record("goblint"), "dartagnan": record("dartagnan")})
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    make_tool(setup.tools, "dartagnan", None)
    setup.run(monkeypatch)
    assert setup.loaded == ["dartagnan"]
    assert setup.installed == ["dartagnan"]


def test_a_doi_fm_tools_does_not_list_installs_nothing(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "dartagnan: 10.5281/zenodo.3\ngoblint: 10.5281/zenodo.99\n")
    with pytest.raises(SystemExit) as stop:
        setup.run(monkeypatch)
    assert "goblint: fm-tools lists 10.5281/zenodo.99 for no verifier version" in str(stop.value)
    assert "10.5281/zenodo.1" in str(stop.value)
    assert setup.installed == [] and not setup.options.exists()


def test_check_and_dry_run_install_nothing(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n")
    with pytest.raises(SystemExit):
        setup.run(monkeypatch, "--check")
    assert setup.loaded == [] and setup.installed == []
    setup.run(monkeypatch, "--dry-run")
    assert setup.loaded == ["goblint"] and setup.installed == []
    assert not setup.options.exists()


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


# Deagle's entry at an older fm-tools commit, which still lists the DOI 10.5281/zenodo.40 that
# FM_TOOLS (fm-tools main) no longer lists
OLD_DEAGLE = {"versions": [{"version": "svcomp27", "doi": "10.5281/zenodo.40", "benchexec_toolinfo_options": ["--y"]}]}


def test_read_unlisted_file(tmp_path):
    path = tmp_path / "tools-unlisted.txt"
    assert dt.read_unlisted_file(path) == {}
    path.write_text("# why\ndeagle: 10.5281/zenodo.40 abc123\n\n")
    assert dt.read_unlisted_file(path) == {"deagle": ("10.5281/zenodo.40", "abc123")}
    for text in ["deagle: 10.5281/zenodo.40\n", "deagle 10.5281/zenodo.40 abc\n",
                 "deagle: 1 a\ndeagle: 2 b\n"]:
        path.write_text(text)
        with pytest.raises(ValueError, match="tools-unlisted.txt"):
            dt.read_unlisted_file(path)


def test_a_doi_fm_tools_no_longer_lists_is_checked_against_the_commit_of_its_exception(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path, monkeypatch, "deagle: 10.5281/zenodo.40\n", unlisted="deagle: 10.5281/zenodo.40 abc123\n",
                  old_fm_tools={("deagle", "abc123"): OLD_DEAGLE})
    make_tool(setup.tools, "deagle", "10.5281/zenodo.40")
    setup.options.write_text(dt.options_text({"deagle": ""}, {"deagle": dt.options_record(
        "10.5281/zenodo.40", OLD_DEAGLE["versions"][0])}))
    setup.run(monkeypatch, "--check", "--fm-tools-dir", str(tmp_path))
    out = capsys.readouterr().out
    assert "EXCEPTION deagle: fm-tools lists 10.5281/zenodo.4, not 10.5281/zenodo.40" in out
    assert "1 of them by an exception of tools-unlisted.txt" in out
    assert setup.loaded == ["deagle", "deagle@abc123"]

    #The options are those of the old commit's entry
    setup.options.write_text(dt.options_text({"deagle": ""}, {"deagle": dict(
        dt.options_record("10.5281/zenodo.40", OLD_DEAGLE["versions"][0]), options=["--x"])}))
    with pytest.raises(SystemExit, match="differ from those of the fm-tools entries"):
        setup.run(monkeypatch, "--check", "--fm-tools-dir", str(tmp_path))


def test_without_an_exception_a_doi_fm_tools_no_longer_lists_is_refused(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "deagle: 10.5281/zenodo.40\n")
    with pytest.raises(SystemExit, match="deagle: fm-tools lists 10.5281/zenodo.40 for no verifier version"):
        setup.run(monkeypatch, "--dry-run")


@pytest.mark.parametrize("lock, unlisted, message", [
    ("deagle: 10.5281/zenodo.4\n", "deagle: 10.5281/zenodo.4 abc123\n", "fm-tools lists 10.5281/zenodo.4 again"),
    ("deagle: 10.5281/zenodo.40\n", "deagle: 10.5281/zenodo.41 abc123\n",
     "names 10.5281/zenodo.41, but the lock file 10.5281/zenodo.40"),
    ("deagle: 10.5281/zenodo.42\n", "deagle: 10.5281/zenodo.42 abc123\n",
     "at abc123 lists 10.5281/zenodo.42 for no verifier version either"),
    ("goblint: 10.5281/zenodo.1\n", "deagle: 10.5281/zenodo.40 abc123\n", "names deagle, which no lock file names"),
], ids=["listed-again", "other-doi", "not-at-the-commit", "no-lock-file"])
def test_an_exception_that_does_not_hold_is_refused(tmp_path, monkeypatch, lock, unlisted, message):
    setup = Setup(tmp_path, monkeypatch, lock, unlisted=unlisted, old_fm_tools={("deagle", "abc123"): OLD_DEAGLE})
    with pytest.raises(SystemExit, match=message):
        setup.run(monkeypatch, "--dry-run")
    assert setup.installed == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git, which the competition image does not have")
def test_the_entry_at_a_commit_is_read_from_the_history_of_the_checkout(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "deagle.yml").write_text(json.dumps(OLD_DEAGLE))
    git("add", "data")
    git("commit", "-q", "-m", "old")
    old = git("rev-parse", "HEAD")
    (tmp_path / "data" / "deagle.yml").write_text(json.dumps(FM_TOOLS["deagle"]))
    git("commit", "-q", "-am", "new")
    assert dt.load_fm_tool_data_at("deagle", tmp_path, old) == OLD_DEAGLE
    git("checkout", "-q", "--orphan", "other")
    git("commit", "-q", "-m", "unrelated")
    with pytest.raises(RuntimeError, match="not in the history of HEAD"):
        dt.load_fm_tool_data_at("deagle", tmp_path, old)


# --- the checkers of tools-checkers.txt ------------------------------------------------

def test_a_stale_checker_is_installed_from_its_first_version_without_options(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", options={"goblint": record("goblint")},
                  checkers="witnesslint: 10.5281/zenodo.9\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    seen = []
    monkeypatch.setattr(dt, "install", lambda name, data, version, doi, root: seen.append((name, version, doi)))

    setup.run(monkeypatch)

    assert seen == [("witnesslint", "2.4-correctness-witnesses-v2", "10.5281/zenodo.9")]
    assert "witnesslint" not in setup.written_options()
    assert "(checker): to install" in capsys.readouterr().out


def test_check_reports_a_stale_checker_and_accepts_an_installed_one(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", options={"goblint": record("goblint")},
                  checkers="witnesslint: 10.5281/zenodo.9\n")
    make_tool(setup.tools, "goblint", "10.5281/zenodo.1")
    with pytest.raises(SystemExit, match="checker\\(s\\) in .* do not match tools-checkers.txt: witnesslint"):
        setup.run(monkeypatch, "--check")

    make_tool(setup.tools, "witnesslint", "10.5281/zenodo.9")
    setup.run(monkeypatch, "--check")


def test_a_checker_that_a_component_lock_names_is_refused(tmp_path, monkeypatch):
    setup = Setup(tmp_path, monkeypatch, "goblint: 10.5281/zenodo.1\n", checkers="goblint: 10.5281/zenodo.1\n")

    with pytest.raises(SystemExit, match="tools-checkers.txt names goblint"):
        setup.run(monkeypatch, "--check")
