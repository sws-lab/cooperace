"""Process control: stopProcessGroups, processExited and ComponentGroup."""
import time

from support import group_members, wait_until

from src.cooperace.processes import (
    STOP_GRACE_SECONDS,
    ComponentGroup,
    processExited,
    stopProcessGroups,
)

# Leader and a child of the same process group, both sleeping. The child
# outlives the leader if only the leader is signalled.
LEADER_AND_CHILD = "sleep 30 & exec sleep 30"


def start_leader_and_child(spawn):
    leader = spawn(["sh", "-c", LEADER_AND_CHILD])
    assert wait_until(lambda: len(group_members(leader.pid)) == 2)
    return leader


def test_stopProcessGroups_ends_leader_and_child(spawn):
    leader = start_leader_and_child(spawn)

    stopProcessGroups([leader])

    assert wait_until(lambda: leader.poll() is not None)
    assert leader.returncode < 0
    assert wait_until(lambda: group_members(leader.pid) == [])


def test_stopProcessGroups_ends_child_that_outlives_the_leader(spawn):
    leader = spawn(["sh", "-c", "sleep 30 & exit 0"])
    leader.wait(timeout=10)
    # The leader is gone and reaped, the child is left in its group.
    assert wait_until(lambda: len(group_members(leader.pid)) == 1)

    # Same call as for a running leader: the group is signalled by its id.
    stopProcessGroups([leader])

    assert wait_until(lambda: group_members(leader.pid) == [])


def test_stopProcessGroups_ends_several_groups(spawn):
    leaders = [start_leader_and_child(spawn) for _ in range(2)]

    stopProcessGroups(leaders)

    for leader in leaders:
        assert wait_until(lambda leader=leader: group_members(leader.pid) == [])


def test_stopProcessGroups_kills_group_that_ignores_sigterm(spawn):
    leader = spawn(["sh", "-c", "trap '' TERM; sleep 30 & while :; do sleep 1; done"])
    assert wait_until(lambda: len(group_members(leader.pid)) >= 2)

    started = time.monotonic()
    stopProcessGroups([leader])
    elapsed = time.monotonic() - started

    assert wait_until(lambda: leader.poll() is not None)
    assert leader.returncode == -9
    # SIGKILL came after the grace period for the leader to exit on SIGTERM.
    assert STOP_GRACE_SECONDS <= elapsed < STOP_GRACE_SECONDS + 5
    assert wait_until(lambda: group_members(leader.pid) == [])


def test_stopProcessGroups_ignores_a_group_that_is_gone(spawn):
    process = spawn(["true"])
    process.wait(timeout=10)

    stopProcessGroups([process])


def test_stopProcessGroups_with_no_processes_returns_at_once():
    started = time.monotonic()
    stopProcessGroups([])
    assert time.monotonic() - started < 1


def test_processExited_for_running_zombie_and_reaped(spawn):
    process = spawn(["sleep", "30"])
    assert wait_until(lambda: not processExited(process.pid), timeout=2)
    assert processExited(process.pid) is False

    process.kill()
    # Killed but not yet waited for: a zombie, which counts as exited.
    assert wait_until(lambda: processExited(process.pid))
    process.wait()
    # Reaped: /proc/<pid> is gone.
    assert processExited(process.pid) is True


def test_processExited_for_a_pid_that_does_not_exist(spawn):
    process = spawn(["true"])
    process.wait(timeout=10)
    assert processExited(process.pid) is True


def test_group_add_records_a_process_until_it_is_removed():
    group = ComponentGroup()
    process = object()

    assert group.add(process) is True
    assert group.processes == {process}
    group.remove(process)
    assert group.processes == set()
    group.remove(process)  # removing twice is harmless


def test_group_add_refuses_after_stop():
    group = ComponentGroup()
    group.stop()
    process = object()

    assert group.stopped is True
    assert group.add(process) is False
    assert group.processes == set()


def test_group_stop_ends_its_processes(spawn):
    group = ComponentGroup()
    leader = start_leader_and_child(spawn)
    assert group.add(leader)

    group.stop()

    assert wait_until(lambda: group_members(leader.pid) == [])


def test_subgroup_created_after_parent_stopped_is_stopped_at_once():
    parent = ComponentGroup()
    parent.stop()

    child = ComponentGroup(parent)

    assert child.stopped is True
    assert child.add(object()) is False
    assert parent.subgroups == [child]


def test_subgroup_created_before_parent_stopped_is_stopped_with_it():
    parent = ComponentGroup()
    child = ComponentGroup(parent)
    grandchild = ComponentGroup(child)
    assert not (child.stopped or grandchild.stopped)

    parent.stop()

    assert child.stopped and grandchild.stopped


def test_stopping_a_subgroup_leaves_the_parent_running():
    parent = ComponentGroup()
    child = ComponentGroup(parent)

    child.stop()

    assert child.stopped is True
    assert parent.stopped is False
    assert parent.add(object()) is True


def test_markStopped_collects_processes_of_subgroups():
    parent = ComponentGroup()
    child = ComponentGroup(parent)
    grandchild = ComponentGroup(child)
    in_parent, in_child, in_grandchild = object(), object(), object()
    parent.add(in_parent)
    child.add(in_child)
    grandchild.add(in_grandchild)

    collected = parent.markStopped()

    assert set(collected) == {in_parent, in_child, in_grandchild}
    assert parent.stopped and child.stopped and grandchild.stopped
    # markStopped signals nothing: the processes are still recorded.
    assert in_child in child.processes


def test_stop_ends_processes_in_subgroups(spawn):
    parent = ComponentGroup()
    child = ComponentGroup(parent)
    leader = start_leader_and_child(spawn)
    assert child.add(leader)

    parent.stop()

    assert wait_until(lambda: group_members(leader.pid) == [])
