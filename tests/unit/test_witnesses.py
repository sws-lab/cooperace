# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Witness files: which files of a run's directory are its witness
(witness_files_in), how a run without the container tells the files it wrote
under the component's directory from the others (witness_file_states,
move_new_witness_files), the patterns of a run in the container
(result_files_patterns), the delivery of witness_files_to_file_root and
remove_old_witness_files."""
import os
import time

import pytest

from src.cooperace import components
from src.cooperace.components import DEFAULT_WITNESS, REGISTRY, WitnessSpec

LONG_AGO = 946684800 * 10**9  # 2000-01-01 in nanoseconds


def witness_of(name):
    """The WitnessSpec of the component `name`, DEFAULT_WITNESS for a name the
    registry does not have."""
    return REGISTRY[name].witness if name in REGISTRY else DEFAULT_WITNESS


def write(path, text="witness", mtime_ns=None):
    """Writes `path` and sets its modification time to `mtime_ns`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.fixture
def run_dirs(tmp_path):
    """The directory of a component (its tool directory, the working
    directory of its run) and the directory made for the run."""
    component = tmp_path / "component"
    run = tmp_path / "run"
    component.mkdir()
    run.mkdir()
    return component, run


# --- witness_files_in --------------------------------------------------------

def test_the_files_a_spec_names_are_collected_from_the_run_directory(run_dirs):
    _component, run = run_dirs
    write(run / "witness.graphml")  # Goblint writes only witness.yml
    wanted = write(run / "witness.yml")

    assert components.witness_files_in(witness_of("Goblint"), str(run)) == [str(wanted)]
    assert components.witness_files_in(witness_of("ULTIMATE GemCutter"), str(run)) == [
        str(run / "witness.graphml"), str(wanted)]


def test_missing_files_are_ignored(run_dirs):
    _component, run = run_dirs

    for name in ("Goblint", "Dartagnan", "ULTIMATE Taipan", "Unlisted"):
        assert components.witness_files_in(witness_of(name), str(run)) == []


def test_a_spec_without_names_collects_by_name_pattern(run_dirs):
    _component, run = run_dirs
    wanted = [write(run / "witness.graphml"), write(run / "sub/dir/Mixed-WITNESS.yml")]
    write(run / "witness.yaml")
    write(run / "result.graphml")

    assert sorted(components.witness_files_in(witness_of("Unlisted"), str(run))) == sorted(map(str, wanted))


# --- witness_file_states and move_new_witness_files ---------------------------

def test_a_file_written_by_the_run_is_moved_and_a_file_left_before_stays(run_dirs):
    component, run = run_dirs
    old = write(component / "old-witness.yml", "old", mtime_ns=LONG_AGO)
    # Newer than any file the run writes: no comparison with a start time would leave it
    future = write(component / "shipped-witness.yml", "shipped", mtime_ns=time.time_ns() + 3600 * 10**9)
    before = components.witness_file_states(witness_of("Unlisted"), str(component))
    new = write(component / "sub/dir/new-witness.graphml", "new")

    components.move_new_witness_files(witness_of("Unlisted"), str(component), str(run), before)

    assert (run / "sub/dir/new-witness.graphml").read_text() == "new"
    assert not new.exists()
    assert old.read_text() == "old" and future.read_text() == "shipped"
    assert not (run / "old-witness.yml").exists() and not (run / "shipped-witness.yml").exists()


def test_a_file_the_run_rewrites_is_moved(run_dirs):
    component, run = run_dirs
    write(component / "output/witness.graphml", "old", mtime_ns=LONG_AGO)
    before = components.witness_file_states(witness_of("Dartagnan"), str(component))
    write(component / "output/witness.graphml", "rewritten")

    components.move_new_witness_files(witness_of("Dartagnan"), str(component), str(run), before)

    assert (run / "output/witness.graphml").read_text() == "rewritten"
    assert components.witness_files_in(witness_of("Dartagnan"), str(run)) == [str(run / "output/witness.graphml")]


def test_only_the_files_a_spec_names_are_moved(run_dirs):
    component, run = run_dirs
    before = components.witness_file_states(witness_of("Dartagnan"), str(component))
    other = write(component / "output/other-witness.graphml")

    components.move_new_witness_files(witness_of("Dartagnan"), str(component), str(run), before)

    assert other.exists() and list(run.iterdir()) == []


def test_witness_file_states_records_the_existing_candidates(run_dirs):
    component, _run = run_dirs
    path = write(component / "output/witness.graphml", "abc", mtime_ns=LONG_AGO)

    states = components.witness_file_states(witness_of("Dartagnan"), str(component))

    assert states == {str(path): (path.stat().st_ino, 3, LONG_AGO)}


# --- result_files_patterns ----------------------------------------------------

def test_result_files_patterns():
    assert components.result_files_patterns(WitnessSpec("run", ("--w", "{dir}"), ("witness.yml",))) == []
    assert components.result_files_patterns(witness_of("Dartagnan")) == ["output/witness.graphml"]
    patterns = components.result_files_patterns(DEFAULT_WITNESS)
    assert len(patterns) == 2 and all(p.startswith("**/") for p in patterns)


def test_the_default_witness_spec_and_the_components_with_their_own():
    assert DEFAULT_WITNESS == WitnessSpec("component", (), None)
    assert {name for name, spec in REGISTRY.items() if spec.witness != DEFAULT_WITNESS} == {
        "Goblint", "Dartagnan", "ULTIMATE Automizer", "ULTIMATE GemCutter", "ULTIMATE Taipan"}
    assert REGISTRY["Goblint"].witness.directory == "run"
    assert REGISTRY["Dartagnan"].witness.directory == "component"


def test_witness_files_under_matches_the_name_pattern(tmp_path):
    for relative in ("a-witness.graphml", "b/WITNESS.yml", "c.graphml", "witness.txt",
                     "d/witness.yaml"):
        write(tmp_path / relative)

    found = components.witness_files_under(str(tmp_path))

    assert sorted(found) == [str(tmp_path / "a-witness.graphml"),
                             str(tmp_path / "b/WITNESS.yml")]


# --- witness_options ---------------------------------------------------------

def test_witness_options_put_the_run_directory_into_the_options():
    assert components.witness_options(witness_of("Goblint"), "/w") == [
        "--set", "witness.yaml.path", "/w/witness.yml"]
    assert components.witness_options(witness_of("ULTIMATE Taipan"), "/w") == ["--witness-dir", "/w"]
    assert components.witness_options(witness_of("Dartagnan"), "/w") == []
    assert components.witness_options(witness_of("Unlisted"), "/w") == []


# --- witness_files_to_file_root and remove_old_witness_files -----------------------

def test_witness_files_to_file_root_delivers_each_format_under_its_name(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "cwd"
    destination.mkdir()
    graphml = write(source / "some-run-witness.graphml", "<graphml/>")
    yaml = write(source / "other-witness.yml", "- entry")
    monkeypatch.chdir(destination)

    components.witness_files_to_file_root([str(graphml), str(yaml)])

    assert (destination / "witness.graphml").read_text() == "<graphml/>"
    assert (destination / "witness.yml").read_text() == "- entry"
    assert graphml.exists() and yaml.exists()  # copied, not moved


def test_witness_files_to_file_root_delivers_a_yaml_file_as_witness_yml(tmp_path, monkeypatch):
    destination = tmp_path / "cwd"
    destination.mkdir()
    yaml = write(tmp_path / "w.yaml", "- entry")
    monkeypatch.chdir(destination)

    components.witness_files_to_file_root([str(yaml)])

    assert [path.name for path in destination.iterdir()] == ["witness.yml"]


def test_witness_files_to_file_root_skips_other_files_and_accepts_an_empty_list(tmp_path, monkeypatch):
    destination = tmp_path / "cwd"
    destination.mkdir()
    other = write(tmp_path / "witness.txt")
    monkeypatch.chdir(destination)

    components.witness_files_to_file_root([str(other)])
    components.witness_files_to_file_root([])

    assert list(destination.iterdir()) == []


def test_remove_old_witness_files_removes_both_delivered_names_only(tmp_path, monkeypatch):
    write(tmp_path / "witness.graphml")
    write(tmp_path / "witness.yml")
    keep = write(tmp_path / "other-witness.yml")
    monkeypatch.chdir(tmp_path)

    components.remove_old_witness_files()

    assert [path.name for path in tmp_path.iterdir()] == [keep.name]


def test_remove_old_witness_files_without_files_does_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    components.remove_old_witness_files()

    assert list(tmp_path.iterdir()) == []
