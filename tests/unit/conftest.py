# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Imports the package src/cooperace, and the tool-info module of every
component in its registry. The package finds the bundled BenchExec (lib/*.whl)
from its own location, so the tests can be run from any directory.

A test that runs a component, as CoOpeRace does, in BenchExec's container
takes the fixture `container`, which skips it where that container cannot be
made: outside runexec and benchexec on Ubuntu 24.04, and on macOS. To run
every test, run pytest under runexec from the checkout:

    runexec --read-only-dir / --overlay-dir . --output /tmp/unit-tests.log -- python3 -m pytest tests/unit

and read the result at the end of /tmp/unit-tests.log."""
import functools
import importlib
import os
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


@functools.cache
def container_works_here() -> bool:
    """Whether BenchExec's container can be made here (execution.container_works
    on tools/), found once per test session."""
    if sys.platform != "linux":
        return False
    from src.cooperace import TOOL_DIR, execution

    return execution.container_works(os.path.join(TOOL_DIR, "tools"))


@pytest.fixture
def container():
    """Skips the test unless BenchExec's container can be made here."""
    if not container_works_here():
        pytest.skip("runs a component in BenchExec's container, which needs runexec here (see conftest.py)")


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
