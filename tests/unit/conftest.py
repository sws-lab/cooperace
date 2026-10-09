"""Imports the package src/cooperace, and the tool-info module of every
component in its registry, with the repository root as the working
directory, because src/cooperace/__init__.py puts lib/*.whl (the bundled
BenchExec) on sys.path relative to the working directory, and a module is
read from the wheel by that relative path when it is first imported. The
tests can then be run from any directory."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_start_directory = os.getcwd()
os.chdir(ROOT)
try:
    sys.path.insert(0, str(ROOT))
    import src.cooperace.components

    for spec in src.cooperace.components.REGISTRY.values():
        importlib.import_module(spec.module)
finally:
    os.chdir(_start_directory)

from src.cooperace.components import ComponentRunner
from src.cooperace.processes import ComponentGroup


@pytest.fixture
def make_runner():
    """Returns a function that makes a ComponentRunner for a dummy task."""

    def make(data_model="ILP32"):
        return ComponentRunner("/dev/null", "/dev/null", data_model)

    return make


@pytest.fixture
def group():
    """A ComponentGroup to run steps in, stopped at the end of the test."""
    group = ComponentGroup()
    yield group
    group.stop()


@pytest.fixture
def runner(make_runner):
    return make_runner()


@pytest.fixture
def spawn():
    """Returns a function that starts a command as the leader of a new
    session, with no input and its output discarded. The process group of
    every command started is killed and the leader reaped at the end of the
    test."""
    started = []

    def start(command, **kwargs):
        process = subprocess.Popen(
            command,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        started.append(process)
        return process

    yield start
    for process in started:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        process.wait()
