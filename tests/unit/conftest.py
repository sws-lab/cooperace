"""Imports the package src/cooperace with the repository root as the working
directory, because its __init__.py puts lib/*.whl (the bundled BenchExec) on
sys.path relative to the working directory at import. The tests can then be
run from any directory."""
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
    import src.cooperace.components  # noqa: F401
finally:
    os.chdir(_start_directory)

from src.cooperace.components import Cooperace


@pytest.fixture
def make_coop():
    """Returns a function that makes a Cooperace for a dummy task and the given
    conf (a sequential conf without components by default). Every instance
    that was made has its root group stopped at the end of the test."""
    made = []

    def make(conf=None, data_model="ILP32"):
        if conf is None:
            conf = {"runType": "sequential", "tools": []}
        coop = Cooperace("/dev/null", "/dev/null", data_model, conf)
        made.append(coop)
        return coop

    yield make
    for coop in made:
        coop.root_group.stop()


@pytest.fixture
def coop(make_coop):
    return make_coop()


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
