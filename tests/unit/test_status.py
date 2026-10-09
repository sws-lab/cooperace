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

# For each status that component_status can return, whether the acceptance
# "true", "false" or "all" accepts it as the verdict true and as the verdict false:
# (status, accepted as true by "true", "false", "all", accepted as false by "true",
# "false", "all"). Only true is a true verdict, and only false(no-data-race) and
# plain false are false verdicts.
ACCEPTANCES = ("true", "false", "all")
ACCEPTANCE_TABLE = [
    ("true", (True, False, True), (False, False, False)),
    ("false(no-data-race)", (False, False, False), (False, True, True)),
    ("false", (False, False, False), (False, True, True)),
    ("false(unreach-call)", (False, False, False), (False, False, False)),
    ("false(no-overflow)", (False, False, False), (False, False, False)),
    ("false(valid-deref)", (False, False, False), (False, False, False)),
    ("false(termination)", (False, False, False), (False, False, False)),
    ("unknown", (False, False, False), (False, False, False)),
    ("done", (False, False, False), (False, False, False)),
    ("TIMEOUT", (False, False, False), (False, False, False)),
    ("ERROR", (False, False, False), (False, False, False)),
    ("ERROR (1)", (False, False, False), (False, False, False)),
    ("EXCEPTION (SetDomain.Unsupported)", (False, False, False), (False, False, False)),
    ("KILLED BY SIGNAL 24", (False, False, False), (False, False, False)),
    # A status is compared as BenchExec returns it, not lower-cased
    ("TRUE", (False, False, False), (False, False, False)),
    ("False", (False, False, False), (False, False, False)),
    # A status that merely contains a verdict is not that verdict
    ("true(no-data-race)", (False, False, False), (False, False, False)),
    ("not true", (False, False, False), (False, False, False)),
    ("unknown (false)", (False, False, False), (False, False, False)),
]


@pytest.mark.parametrize("status, as_true, as_false", ACCEPTANCE_TABLE)
def test_confirm_verdict_accepts_only_the_statuses_of_a_verdict(status, as_true, as_false):
    accepted_as_true = tuple(components.confirm_verdict(a, status, "true") for a in ACCEPTANCES)
    accepted_as_false = tuple(components.confirm_verdict(a, status, "false") for a in ACCEPTANCES)

    assert accepted_as_true == as_true
    assert accepted_as_false == as_false


def test_confirm_verdict_returns_a_bool():
    for status, _as_true, _as_false in ACCEPTANCE_TABLE:
        for acceptance in ACCEPTANCES:
            for expected in ("true", "false"):
                assert isinstance(components.confirm_verdict(acceptance, status, expected), bool)
