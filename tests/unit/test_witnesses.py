"""Witness files: the time filter and WitnessSpecs of collect_witness_files, the
delivery of witness_files_to_file_root and remove_old_witness_files."""
import os

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


# --- start_time --------------------------------------------------------------

def test_start_time_is_the_modification_time_of_a_new_marker_file(tmp_path):
    started = components.start_time(str(tmp_path))

    assert started == (tmp_path / ".started").stat().st_mtime_ns


def test_start_time_does_not_go_backwards(tmp_path):
    first = components.start_time(str(tmp_path))
    second = components.start_time(str(tmp_path))

    assert second >= first


# --- collect_witness_files ----------------------------------------------------

def test_files_written_in_the_run_directory_are_collected(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    witness = write(run / "witness.yml", mtime_ns=started + 1)

    collected = components.collect_witness_files(witness_of("Goblint"), str(component), str(run), started)

    assert collected == [str(witness)]
    assert witness.exists()


def test_only_files_modified_at_or_after_the_start_are_collected(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    write(run / "witness.graphml", mtime_ns=started - 1)
    write(run / "witness.yml", mtime_ns=started)

    collected = components.collect_witness_files(witness_of("ULTIMATE Automizer"), str(component),
                                         str(run), started)

    assert collected == [str(run / "witness.yml")]


def test_an_old_file_is_left_where_it_is(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    old = write(component / "output/witness.graphml", "old", mtime_ns=LONG_AGO)

    collected = components.collect_witness_files(witness_of("Dartagnan"), str(component), str(run), started)

    assert collected == []
    assert old.read_text() == "old"


def test_a_file_under_the_component_directory_is_moved_into_the_run_directory(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    source = write(component / "output/witness.graphml", "new", mtime_ns=started + 1)

    collected = components.collect_witness_files(witness_of("Dartagnan"), str(component), str(run), started)

    destination = run / "output/witness.graphml"
    assert collected == [str(destination)]
    assert destination.read_text() == "new"
    assert not source.exists()


def test_missing_files_are_ignored(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))

    for name in ("Goblint", "Dartagnan", "ULTIMATE Taipan", "Unlisted"):
        assert components.collect_witness_files(witness_of(name), str(component), str(run), started) == []


def test_a_component_collects_only_the_files_its_entry_names(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    write(run / "witness.graphml", mtime_ns=started + 1)  # Goblint writes only witness.yml
    wanted = write(run / "witness.yml", mtime_ns=started + 1)

    collected = components.collect_witness_files(witness_of("Goblint"), str(component), str(run), started)

    assert collected == [str(wanted)]


def test_ultimate_collects_graphml_and_yaml_witnesses(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
    write(run / "witness.graphml", mtime_ns=started + 1)
    write(run / "witness.yml", mtime_ns=started + 1)

    collected = components.collect_witness_files(witness_of("ULTIMATE GemCutter"), str(component),
                                         str(run), started)

    assert collected == [str(run / "witness.graphml"), str(run / "witness.yml")]


def test_a_component_without_an_entry_collects_by_name_pattern_under_its_directory(run_dirs):
    component, run = run_dirs
    started = components.start_time(str(run))
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

    collected = components.collect_witness_files(witness_of("Unlisted"), str(component), str(run), started)

    assert sorted(collected) == sorted(str(run / relative) for relative in wanted)
    for relative, source in wanted.items():
        assert not source.exists()
        assert (run / relative).exists()
    assert all(path.exists() for path in ignored)


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
