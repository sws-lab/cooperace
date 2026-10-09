"""Witness files: the time filter and table of collectWitnessFiles, the
delivery of witnessFilesToFileRoot and removeOldWitnessFiles."""
import os
from types import SimpleNamespace

import pytest

from src.cooperace.components import DEFAULT_WITNESS_FILES, WITNESS_FILES

LONG_AGO = 946684800 * 10**9  # 2000-01-01 in nanoseconds


def actor(name):
    return SimpleNamespace(name=lambda: name)


def write(path, text="witness", mtime_ns=None):
    """Writes `path` and sets its modification time to `mtime_ns`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.fixture
def run_dirs(tmp_path):
    """The directory of a component (its executable's directory, the working
    directory of its run) and the directory made for the run."""
    component = tmp_path / "component"
    run = tmp_path / "run"
    component.mkdir()
    run.mkdir()
    return component, run


# --- startTime --------------------------------------------------------------

def test_startTime_is_the_modification_time_of_a_new_marker_file(coop, tmp_path):
    started = coop.startTime(str(tmp_path))

    assert started == (tmp_path / ".started").stat().st_mtime_ns


def test_startTime_does_not_go_backwards(coop, tmp_path):
    first = coop.startTime(str(tmp_path))
    second = coop.startTime(str(tmp_path))

    assert second >= first


# --- collectWitnessFiles ----------------------------------------------------

def test_files_written_in_the_run_directory_are_collected(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    witness = write(run / "witness.yml", mtime_ns=started + 1)

    collected = coop.collectWitnessFiles(actor("Goblint"), str(component), str(run), started)

    assert collected == [str(witness)]
    assert witness.exists()


def test_only_files_modified_at_or_after_the_start_are_collected(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    write(run / "witness.graphml", mtime_ns=started - 1)
    write(run / "witness.yml", mtime_ns=started)

    collected = coop.collectWitnessFiles(actor("ULTIMATE Automizer"), str(component),
                                         str(run), started)

    assert collected == [str(run / "witness.yml")]


def test_an_old_file_is_left_where_it_is(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    old = write(component / "output/witness.graphml", "old", mtime_ns=LONG_AGO)

    collected = coop.collectWitnessFiles(actor("Dartagnan"), str(component), str(run), started)

    assert collected == []
    assert old.read_text() == "old"


def test_a_file_under_the_component_directory_is_moved_into_the_run_directory(
        coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    source = write(component / "output/witness.graphml", "new", mtime_ns=started + 1)

    collected = coop.collectWitnessFiles(actor("Dartagnan"), str(component), str(run), started)

    destination = run / "output/witness.graphml"
    assert collected == [str(destination)]
    assert destination.read_text() == "new"
    assert not source.exists()


def test_missing_files_are_ignored(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))

    for name in ("Goblint", "Dartagnan", "ULTIMATE Taipan", "Unlisted"):
        assert coop.collectWitnessFiles(actor(name), str(component), str(run), started) == []


def test_a_component_collects_only_the_files_its_entry_names(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    write(run / "witness.graphml", mtime_ns=started + 1)  # Goblint writes only witness.yml
    wanted = write(run / "witness.yml", mtime_ns=started + 1)

    collected = coop.collectWitnessFiles(actor("Goblint"), str(component), str(run), started)

    assert collected == [str(wanted)]


def test_ultimate_collects_graphml_and_yaml_witnesses(coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    write(run / "witness.graphml", mtime_ns=started + 1)
    write(run / "witness.yml", mtime_ns=started + 1)

    collected = coop.collectWitnessFiles(actor("ULTIMATE GemCutter"), str(component),
                                         str(run), started)

    assert collected == [str(run / "witness.graphml"), str(run / "witness.yml")]


def test_a_component_without_an_entry_collects_by_name_pattern_under_its_directory(
        coop, run_dirs):
    component, run = run_dirs
    started = coop.startTime(str(run))
    new = started + 1
    wanted = {
        "witness.graphml": write(component / "witness.graphml", mtime_ns=new),
        "Mixed-WITNESS.yml": write(component / "Mixed-WITNESS.yml", mtime_ns=new),
        "sub/dir/my-witness.yml": write(component / "sub/dir/my-witness.yml", mtime_ns=new),
    }
    ignored = [
        write(component / "witness.txt", mtime_ns=new),
        write(component / "witness.yaml", mtime_ns=new),
        write(component / "witness.graphml.bak", mtime_ns=new),
        write(component / "result.graphml", mtime_ns=new),
        write(component / "old-witness.yml", mtime_ns=LONG_AGO),
    ]

    collected = coop.collectWitnessFiles(actor("Unlisted"), str(component), str(run), started)

    assert sorted(collected) == sorted(str(run / relative) for relative in wanted)
    for relative, source in wanted.items():
        assert not source.exists()
        assert (run / relative).exists()
    assert all(path.exists() for path in ignored)


def test_the_default_entry_and_the_table_of_witness_files():
    assert DEFAULT_WITNESS_FILES == {"directory": "component", "options": [], "files": None}
    assert set(WITNESS_FILES) == {"Goblint", "Dartagnan", "ULTIMATE Automizer",
                                  "ULTIMATE GemCutter", "ULTIMATE Taipan"}
    assert WITNESS_FILES["Goblint"]["directory"] == "run"
    assert WITNESS_FILES["Dartagnan"]["directory"] == "component"


def test_witnessFiles_matches_the_name_pattern(coop, tmp_path):
    for relative in ("a-witness.graphml", "b/WITNESS.yml", "c.graphml", "witness.txt",
                     "d/witness.yaml"):
        write(tmp_path / relative)

    found = coop.witnessFiles(str(tmp_path))

    assert sorted(found) == [str(tmp_path / "a-witness.graphml"),
                             str(tmp_path / "b/WITNESS.yml")]


# --- witnessOptions ---------------------------------------------------------

def test_witnessOptions_put_the_run_directory_into_the_options(coop):
    assert coop.witnessOptions(actor("Goblint"), "/w") == [
        "--set", "witness.yaml.path", "/w/witness.yml"]
    assert coop.witnessOptions(actor("ULTIMATE Taipan"), "/w") == ["--witness-dir", "/w"]
    assert coop.witnessOptions(actor("Dartagnan"), "/w") == []
    assert coop.witnessOptions(actor("Unlisted"), "/w") == []


# --- witnessFilesToFileRoot and removeOldWitnessFiles -----------------------

def test_witnessFilesToFileRoot_delivers_each_format_under_its_name(
        coop, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "cwd"
    destination.mkdir()
    graphml = write(source / "some-run-witness.graphml", "<graphml/>")
    yaml = write(source / "other-witness.yml", "- entry")
    monkeypatch.chdir(destination)

    coop.witnessFilesToFileRoot([str(graphml), str(yaml)])

    assert (destination / "witness.graphml").read_text() == "<graphml/>"
    assert (destination / "witness.yml").read_text() == "- entry"
    assert graphml.exists() and yaml.exists()  # copied, not moved


def test_witnessFilesToFileRoot_delivers_a_yaml_file_as_witness_yml(
        coop, tmp_path, monkeypatch):
    destination = tmp_path / "cwd"
    destination.mkdir()
    yaml = write(tmp_path / "w.yaml", "- entry")
    monkeypatch.chdir(destination)

    coop.witnessFilesToFileRoot([str(yaml)])

    assert [path.name for path in destination.iterdir()] == ["witness.yml"]


def test_witnessFilesToFileRoot_skips_other_files_and_accepts_an_empty_list(
        coop, tmp_path, monkeypatch):
    destination = tmp_path / "cwd"
    destination.mkdir()
    other = write(tmp_path / "witness.txt")
    monkeypatch.chdir(destination)

    coop.witnessFilesToFileRoot([str(other)])
    coop.witnessFilesToFileRoot([])

    assert list(destination.iterdir()) == []


def test_removeOldWitnessFiles_removes_both_delivered_names_only(
        coop, tmp_path, monkeypatch):
    write(tmp_path / "witness.graphml")
    write(tmp_path / "witness.yml")
    keep = write(tmp_path / "other-witness.yml")
    monkeypatch.chdir(tmp_path)

    coop.removeOldWitnessFiles()

    assert [path.name for path in tmp_path.iterdir()] == [keep.name]


def test_removeOldWitnessFiles_without_files_does_nothing(coop, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    coop.removeOldWitnessFiles()

    assert list(tmp_path.iterdir()) == []
