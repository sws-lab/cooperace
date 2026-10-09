"""Defects of src/cooperace.py known when the suite was written, one strict
xfail each. A test here states the behaviour the code should have; it fails
today, and fixing the defect makes it pass, which strict xfail reports as an
error until the marker is removed."""
import threading
import time

import pytest

from src import cooperace
from src.cooperace import NO_OUTCOME, Outcome


@pytest.mark.xfail(strict=True, reason=(
    "D1: an exception while a sequential step is set up (for example "
    "ToolNotFoundException from actor.executable) propagates out of "
    "runSequential and execute, so the later steps never run; in a parallel "
    "branch runBranch catches it"))
def test_D1_a_failing_step_does_not_stop_the_later_steps_of_a_sequence(make_coop):
    """Expected: runSequential([failing, true]) treats the failing step as
    having no verdict and returns the second step's `true`."""
    coop = make_coop({"runType": "sequential",
                      "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})

    def runActor(actor):
        if actor.name() == "Goblint":
            raise RuntimeError("Could not find executable")
        coop.local.witness_files = []
        return "true"

    coop.runActor = runActor
    _, tools = coop.parseConf()

    assert coop.runSequential(tools) == Outcome("true", "Deagle", [])


@pytest.mark.xfail(strict=True, reason=(
    "D2: runBranch catches Exception only; a branch that ends with a "
    "BaseException (SystemExit) never puts an outcome, and runParallel waits "
    "for it forever when no other branch reports an accepted verdict"))
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_D2_a_branch_ending_with_systemexit_does_not_hang_runParallel(make_coop):
    """Expected: runParallel returns once the other branch has reported, here
    NO_OUTCOME because that branch has no verdict. It is run in a daemon
    thread so that the hang is a failure and not a stuck test run."""
    coop = make_coop({"runType": "parallel",
                      "tools": [{"Goblint": "all"}, {"Deagle": "all"}]})

    def runOne(actor):
        if actor.name() == "Goblint":
            raise SystemExit(3)
        time.sleep(0.3)
        return NO_OUTCOME

    coop.runOne = runOne
    _, tools = coop.parseConf()
    result = {}
    thread = threading.Thread(target=lambda: result.update(outcome=coop.runParallel(tools)),
                              daemon=True)
    thread.start()
    thread.join(2)

    assert not thread.is_alive(), "runParallel is still waiting after 2 s"
    assert result["outcome"] == NO_OUTCOME


@pytest.mark.xfail(strict=True, reason=(
    "D4: a percentage memory limit with no cgroup limit found gives no limit "
    "and prints nothing, so the run looks like one without the key"))
def test_D4_a_percentage_limit_without_a_cgroup_limit_says_that_no_limit_applies(
        make_coop, monkeypatch, capsys):
    """Expected: withResourceLimits prints a line starting with
    "Memory limit of Goblint:" that says no limit was applied; the command is
    still returned unchanged."""
    monkeypatch.setattr(cooperace, "run_memory_limit", lambda: None)
    coop = make_coop({"runType": "sequential", "tools": [],
                      "memoryLimits": {"Goblint": "70%"}})
    command = ["echo", "hello"]

    assert coop.withResourceLimits("Goblint", command) is command
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("Memory limit of Goblint:") for line in lines)
