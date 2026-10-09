# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The version that `cooperace --version` prints (cli.version_string): the
file VERSION beside the launcher, else git describe, else "unknown"."""
import os
import subprocess
from pathlib import Path

import pytest

from src.cooperace import cli

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_git_environment(monkeypatch):
    """Removes GIT_DIR, GIT_INDEX_FILE and the other GIT_* variables, which git
    sets for a command it runs (`git rebase --exec`, a hook): with them, the
    git commands of these tests act on the repository of the checkout instead
    of the one in tmp_path."""
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                   check=True, capture_output=True)


def test_version_file_is_read(tmp_path):
    (tmp_path / "VERSION").write_text("svcomp27 abc1234\nignored second line\n")
    assert cli.version_string(tmp_path) == "svcomp27 abc1234"


def test_version_file_wins_over_git(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "VERSION").write_text("svcomp27 abc1234\n")
    assert cli.version_string(tmp_path) == "svcomp27 abc1234"


def test_git_describe_without_version_file(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "f").write_text("x")
    git(tmp_path, "add", "f")
    git(tmp_path, "commit", "-q", "-m", "first")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert cli.version_string(tmp_path) == head
    (tmp_path / "f").write_text("y")
    assert cli.version_string(tmp_path) == head + "-dirty"


def test_unknown_outside_a_checkout_without_version_file(tmp_path):
    assert cli.version_string(tmp_path) == "unknown"


def test_empty_version_file_falls_through(tmp_path):
    (tmp_path / "VERSION").write_text("\n")
    assert cli.version_string(tmp_path) == "unknown"


def test_launcher_prints_version_from_another_directory(tmp_path):
    (tmp_path / "VERSION").write_text("not read: only the launcher's own directory counts\n")
    out = subprocess.run([str(ROOT / "cooperace"), "--version"], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.startswith("CoOpeRace ")
    assert "not read" not in out.stdout
