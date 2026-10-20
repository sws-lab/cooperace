# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""scripts/sv-comp/required-packages.py on fake fm-tools entries, a fake
tools.txt and a fake own-packages.txt in tmp_path: the union of the packages
of the versions whose DOIs tools.txt names, the warning for a component whose
images are not docker.io/ubuntu:26.04, the failure for a DOI no version has,
and `--check` against an entry with the same and with other packages. No
network."""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sv-comp" / "required-packages.py"
spec = importlib.util.spec_from_file_location("required_packages", SCRIPT)
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

UBUNTU = ["docker.io/ubuntu:26.04"]
ENTRIES = {
    "goblint": {
        "versions": [
            {
                "version": "svcomp27-validation",
                "doi": "10.5281/zenodo.1",
                "benchexec_toolinfo_options": ["${witness}"],
                "required_ubuntu_packages": ["validator-only"],
                "base_container_images": UBUNTU,
            },
            {
                "version": "svcomp27",
                "doi": "10.5281/zenodo.1",
                "required_ubuntu_packages": ["cpp", "python3"],
                "base_container_images": UBUNTU,
            },
            {
                "version": "svcomp26",
                "doi": "10.5281/zenodo.2",
                "required_ubuntu_packages": ["old-package"],
                "base_container_images": UBUNTU,
            },
        ]
    },
    "dartagnan": {
        "versions": [
            {
                "version": "svcomp27",
                "doi": "10.5281/zenodo.3",
                "required_ubuntu_packages": ["clang"],
                "base_container_images": UBUNTU,
            },
        ]
    },
    "deagle": {"versions": [{"version": "svcomp27", "doi": "10.5281/zenodo.4"}]},
}
OWN = "# why\npython3  # the launcher\n\npython3-yaml  # PyYAML\n"


def cooperace_entry(packages):
    return {
        "versions": [{"version": "svcomp27", "required_ubuntu_packages": packages, "base_container_images": UBUNTU}],
        "competition_participations": [{"competition": "SV-COMP 2027", "tool_version": "svcomp27"}],
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Writes ENTRIES as an fm-tools checkout, a tools.txt naming goblint
    svcomp27, dartagnan and deagle, and own-packages.txt; returns a function
    that runs the script's main with the given arguments and returns its exit
    status."""
    data = tmp_path / "fm-tools" / "data"
    data.mkdir(parents=True)
    for name, entry in ENTRIES.items():
        (data / f"{name}.yml").write_text(yaml.safe_dump(entry))
    (tmp_path / "tools.txt").write_text(
        "goblint: 10.5281/zenodo.1\ndartagnan: 10.5281/zenodo.3\ndeagle: 10.5281/zenodo.4\n"
    )
    (tmp_path / "own.txt").write_text(OWN)

    def run(*args):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "required-packages.py",
                "--fm-tools-dir",
                str(tmp_path / "fm-tools"),
                "--tools",
                str(tmp_path / "tools.txt"),
                "--own",
                str(tmp_path / "own.txt"),
                *args,
            ],
        )
        return rp.main()

    run.dir = tmp_path
    return run


def test_parse_own_packages():
    assert rp.parse_own_packages(OWN) == ["python3", "python3-yaml"]


def test_union_of_the_versions_tools_txt_names(setup, capsys):
    assert setup() == 0
    out = capsys.readouterr().out
    # the verifier of goblint's DOI, not its validator and not svcomp26
    assert out.splitlines() == ["clang", "cpp", "python3", "python3-yaml"]


def test_why_and_yaml(setup, capsys):
    assert setup("--why") == 0
    assert capsys.readouterr().out.splitlines() == [
        "clang: dartagnan",
        "cpp: goblint",
        "python3: goblint, own",
        "python3-yaml: own",
    ]
    assert setup("--yaml") == 0
    assert capsys.readouterr().out == (
        "    required_ubuntu_packages:\n      - clang\n      - cpp\n      - python3\n      - python3-yaml\n"
    )


def test_a_component_without_the_ubuntu_image_is_flagged(setup, capsys):
    assert setup() == 0
    err = capsys.readouterr().err
    assert "deagle svcomp27: its base_container_images are none" in err
    assert "goblint" not in err and "dartagnan" not in err


def test_image_warning():
    assert rp.image_warning("a", {"version": "v", "base_container_images": UBUNTU}) is None
    assert "docker.io/ubuntu:24.04" in rp.image_warning(
        "a", {"version": "v", "base_container_images": ["docker.io/ubuntu:24.04"]}
    )
    assert "full_container_images x" in rp.image_warning(
        "a", {"version": "v", "base_container_images": UBUNTU, "full_container_images": ["x"]}
    )


def test_a_doi_no_version_has_names_the_component(setup, capsys):
    (setup.dir / "tools.txt").write_text("goblint: 10.5281/zenodo.1\ndartagnan: 10.5281/zenodo.99\n")
    assert setup() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dartagnan: fm-tools lists 10.5281/zenodo.99 for no verifier version" in captured.err


def test_a_validator_only_doi_is_no_version(setup, capsys):
    validator = {
        "versions": [{"version": "v", "doi": "10.5281/zenodo.5", "benchexec_toolinfo_options": ["${witness}"]}]
    }
    (setup.dir / "fm-tools" / "data" / "goblint.yml").write_text(yaml.safe_dump(validator))
    (setup.dir / "tools.txt").write_text("goblint: 10.5281/zenodo.5\n")
    assert setup() == 1
    assert "goblint: fm-tools lists 10.5281/zenodo.5 for no verifier version" in capsys.readouterr().err


def test_a_missing_entry_names_the_component(setup, capsys):
    (setup.dir / "tools.txt").write_text("absent: 10.5281/zenodo.1\n")
    assert setup() == 1
    assert "absent: cannot read the fm-tools entry" in capsys.readouterr().err


def test_check_equal(setup, capsys):
    entry = setup.dir / "cooperace.yml"
    entry.write_text(yaml.safe_dump(cooperace_entry(["python3-yaml", "clang", "cpp", "python3"])))
    assert setup("--check", str(entry)) == 0
    assert "are those computed from tools.txt" in capsys.readouterr().out


def test_check_different(setup, capsys):
    entry = setup.dir / "cooperace.yml"
    entry.write_text(yaml.safe_dump(cooperace_entry(["clang", "cpp", "python3", "gcc-multilib"])))
    assert setup("--check", str(entry)) == 1
    out = capsys.readouterr().out
    assert "  missing: python3-yaml (declared by own)" in out
    assert "  extra:   gcc-multilib" in out
    assert "missing: clang" not in out


def test_check_refuses_an_entry_without_a_participation_version(setup, capsys):
    entry = setup.dir / "cooperace.yml"
    entry.write_text(yaml.safe_dump({"versions": [], "competition_participations": []}))
    assert setup("--check", str(entry)) == 1
    assert "no participation of SV-COMP 2027" in capsys.readouterr().err
