"""The status of a component run (componentStatus) and which verdicts an
acceptance accepts (confirmVerdict)."""
import signal
import subprocess

import pytest


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


def status_of(coop, status, returncode, stdout="output"):
    actor = StatusActor(status)
    result = subprocess.CompletedProcess(["fake"], returncode, stdout, "")
    return coop.componentStatus(actor, ["fake"], result)


# What componentStatus returns for the status that determine_result gives and
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
def test_componentStatus(coop, status, returncode, expected):
    assert status_of(coop, status, returncode) == expected


def test_componentStatus_gives_determine_result_the_real_exit_code(coop):
    actor = StatusActor("unknown")
    result = subprocess.CompletedProcess(["fake", "-x"], 3, "  line 1\nline 2\n\n", "")

    coop.componentStatus(actor, ["fake", "-x"], result)

    (run,) = actor.runs
    assert list(run.cmdline) == ["fake", "-x"]
    assert (run.exit_code.value, run.exit_code.signal) == (3, None)
    assert list(run.output) == ["line 1", "line 2"]


def test_componentStatus_gives_determine_result_the_signal_of_an_ended_component(coop):
    actor = StatusActor("unknown")
    result = subprocess.CompletedProcess(["fake"], -signal.SIGTERM, "", "")

    coop.componentStatus(actor, ["fake"], result)

    (run,) = actor.runs
    assert (run.exit_code.value, run.exit_code.signal) == (None, signal.SIGTERM)


# --- confirmVerdict ---------------------------------------------------------

@pytest.mark.parametrize("acceptance, accepts_true, accepts_false", [
    ("all", True, True),
    ("true", True, False),
    ("false", False, True),
])
def test_confirmVerdict_by_acceptance(coop, acceptance, accepts_true, accepts_false):
    assert bool(coop.confirmVerdict(acceptance, "true", "true")) is accepts_true
    assert bool(coop.confirmVerdict(acceptance, "false", "false")) is accepts_false


def test_confirmVerdict_does_not_match_the_other_verdict(coop):
    assert not coop.confirmVerdict("all", "false", "true")
    assert not coop.confirmVerdict("all", "true", "false")
    assert not coop.confirmVerdict("all", "unknown", "true")
    assert not coop.confirmVerdict("all", "unknown", "false")


def test_confirmVerdict_matches_a_verdict_that_contains_the_expected_one(coop):
    assert coop.confirmVerdict("true", "true(no-data-race)", "true")
    assert not coop.confirmVerdict("true", "false(no-data-race)", "false")
