"""Imports the package src/cooperace, and the tool-info module of every
component in its registry. The package finds the bundled BenchExec (lib/*.whl)
from its own location, so the tests can be run from any directory."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT))
import src.cooperace.components  # noqa: E402

for spec in src.cooperace.components.REGISTRY.values():
    importlib.import_module(spec.module)

from src.cooperace.components import ComponentRunner  # noqa: E402
from src.cooperace.processes import ComponentGroup  # noqa: E402


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
