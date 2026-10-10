# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""ComponentGroup: recording the running components, stopping them with
their group and its subgroups, and refusing new ones once stopped."""
from src.cooperace.processes import ComponentGroup


class FakeRun:
    """A component recorded as an object with stop(), as execution.ExecutorRun is."""

    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


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


def test_group_stop_calls_stop_of_each_component_once():
    group = ComponentGroup()
    runs = [FakeRun(), FakeRun()]
    for run in runs:
        assert group.add(run)

    group.stop()

    assert [run.stops for run in runs] == [1, 1]


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


def test_stop_ends_components_in_subgroups_and_refuses_new_ones():
    parent = ComponentGroup()
    child = ComponentGroup(parent)
    in_parent, in_child = FakeRun(), FakeRun()
    assert parent.add(in_parent) and child.add(in_child)

    parent.stop()

    assert (in_parent.stops, in_child.stops) == (1, 1)
    assert child.add(FakeRun()) is False
