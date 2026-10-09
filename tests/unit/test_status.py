"""The status of a component run (component_status) and which verdicts an
acceptance accepts (confirm_verdict)."""
import signal
import subprocess

import pytest

from src.cooperace import components


class StatusActor:
    """A tool-info stand-in whose determine_result returns `status` and
    keeps the Run it was given."""

    def __init__(self, status):
        self.status = status
        self.runs = []

    def name(self):
        return "Fake"

    def determine_result(self, run):
        self.runs.append(run)
        return self.status


def status_of(status, returncode, stdout="output"):
    actor = StatusActor(status)
    result = subprocess.CompletedProcess(["fake"], returncode, stdout, "")
    return components.component_status(actor, ["fake"], result)


# What component_status returns for the status that determine_result gives and
# the exit code of the component (negative: ended by that signal). A status
# that is not "unknown", "ERROR" or "done" is kept as it is.
STATUS_TABLE = [
    # determine_result's status, returncode, status of the run
    ("true", 0, "true"),
    ("false", 0, "false"),
    ("false", 1, "false"),
    ("true", -signal.SIGTERM, "true"),
    ("EXCEPTION (SetDomain.Unsupported)", 2, "EXCEPTION (SetDomain.Unsupported)"),
    ("unknown", 0, "unknown"),
    ("unknown", 1, "unknown"),
    ("unknown", 2, "unknown"),
    ("done", 0, "done"),
    ("done", 1, "ERROR (1)"),
    ("done", 2, "ERROR (2)"),
    ("ERROR", 0, "ERROR"),
    ("ERROR", 1, "ERROR (1)"),
    ("ERROR", 3, "ERROR (3)"),
    ("unknown", -signal.SIGABRT, "ABORTED"),
    ("done", -signal.SIGABRT, "ABORTED"),
    ("unknown", -signal.SIGSEGV, "SEGMENTATION FAULT"),
    ("ERROR", -signal.SIGSEGV, "SEGMENTATION FAULT"),
    ("unknown", -signal.SIGTERM, "KILLED"),
    ("ERROR", -signal.SIGTERM, "KILLED"),
    ("unknown", -signal.SIGXCPU, "KILLED BY SIGNAL 24"),
    ("ERROR", -signal.SIGXCPU, "KILLED BY SIGNAL 24"),
    ("unknown", -signal.SIGKILL, "KILLED BY SIGNAL 9"),
]


@pytest.mark.parametrize("status, returncode, expected", STATUS_TABLE)
def test_component_status(status, returncode, expected):
    assert status_of(status, returncode) == expected


def test_component_status_gives_determine_result_the_real_exit_code():
    actor = StatusActor("unknown")
    result = subprocess.CompletedProcess(["fake", "-x"], 3, "  line 1\nline 2\n\n", "")

    components.component_status(actor, ["fake", "-x"], result)

    (run,) = actor.runs
    assert list(run.cmdline) == ["fake", "-x"]
    assert (run.exit_code.value, run.exit_code.signal) == (3, None)
    assert list(run.output) == ["line 1", "line 2"]


def test_component_status_gives_determine_result_the_signal_of_an_ended_component():
    actor = StatusActor("unknown")
    result = subprocess.CompletedProcess(["fake"], -signal.SIGTERM, "", "")

    components.component_status(actor, ["fake"], result)

    (run,) = actor.runs
    assert (run.exit_code.value, run.exit_code.signal) == (None, signal.SIGTERM)


# --- confirm_verdict ---------------------------------------------------------

@pytest.mark.parametrize("acceptance, accepts_true, accepts_false", [
    ("all", True, True),
    ("true", True, False),
    ("false", False, True),
])
def test_confirm_verdict_by_acceptance(acceptance, accepts_true, accepts_false):
    assert bool(components.confirm_verdict(acceptance, "true", "true")) is accepts_true
    assert bool(components.confirm_verdict(acceptance, "false", "false")) is accepts_false


def test_confirm_verdict_does_not_match_the_other_verdict():
    assert not components.confirm_verdict("all", "false", "true")
    assert not components.confirm_verdict("all", "true", "false")
    assert not components.confirm_verdict("all", "unknown", "true")
    assert not components.confirm_verdict("all", "unknown", "false")


def test_confirm_verdict_matches_a_verdict_that_contains_the_expected_one():
    assert components.confirm_verdict("true", "true(no-data-race)", "true")
    assert not components.confirm_verdict("true", "false(no-data-race)", "false")
