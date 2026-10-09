"""Loading a conf: config.load, the tree it builds and the confs it refuses."""
import dataclasses
import json
from pathlib import Path

import pytest

from src.cooperace import config
from src.cooperace.config import Parallel, Sequence, Step

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_CONFS = sorted(
    [*ROOT.glob("conf/*.json"), *ROOT.glob("conf/combinations*/*.json"),
     *ROOT.glob("tests/integration/conf/*.json")])


@pytest.fixture
def known(coop):
    """The names of the components CoOpeRace can run."""
    return coop.tools


def steps_of(node):
    """The Steps of the tree `node`, in the order of the conf."""
    if isinstance(node, Step):
        return [node]
    return [step for child in node.steps for step in steps_of(child)]


# --- the confs in the repository ---------------------------------------------

def test_the_repository_has_the_confs_this_file_checks():
    names = {path.relative_to(ROOT).as_posix() for path in SHIPPED_CONFS}
    assert "conf/svcomp26.json" in names
    assert "tests/integration/conf/only-goblint.json" in names
    assert len(names) > 30


@pytest.mark.parametrize("path", SHIPPED_CONFS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_shipped_conf_loads(path, known):
    conf = json.loads(path.read_text())

    root = config.load(conf, known)

    expected = Sequence if conf["runType"] == "sequential" else Parallel
    assert isinstance(root, expected)
    assert steps_of(root)


def test_svcomp26_loads_to_its_tree(known):
    conf = json.loads((ROOT / "conf/svcomp26.json").read_text())

    assert config.load(conf, known) == Sequence((
        Step("Goblint", "true", None, 30),
        Parallel((Step("Dartagnan", "all"), Step("ULTIMATE Automizer", "all", "70%"))),
    ))


# --- the tree ----------------------------------------------------------------

@pytest.mark.parametrize("run_type, outer, inner", [
    ("sequential", Sequence, Parallel),
    ("parallel", Parallel, Sequence),
])
def test_load_takes_the_kind_from_the_run_type_and_alternates_it_with_depth(
        known, run_type, outer, inner):
    conf = {"runType": run_type,
            "tools": [{"Goblint": "all"}, [{"Deagle": "all"}, [{"Dartagnan": "all"}]]]}

    root = config.load(conf, known)

    assert root == outer((Step("Goblint", "all"),
                          inner((Step("Deagle", "all"), outer((Step("Dartagnan", "all"),))))))


def test_load_records_the_acceptance_of_every_component(known):
    conf = {"runType": "sequential",
            "tools": [{"Goblint": "true"}, [{"Dartagnan": "false"}, {"Deagle": "all"}]]}

    root = config.load(conf, known)

    assert {step.component: step.accept for step in steps_of(root)} == {
        "Goblint": "true", "Dartagnan": "false", "Deagle": "all"}


def test_an_object_with_several_keys_gives_one_step_per_key_in_order(known):
    conf = {"runType": "parallel", "tools": [{"Goblint": "all", "Deagle": "true"}]}

    assert config.load(conf, known) == Parallel((Step("Goblint", "all"), Step("Deagle", "true")))


def test_empty_lists_load(known):
    conf = {"runType": "sequential", "tools": [[]]}

    assert config.load(conf, known) == Sequence((Parallel(()),))


def test_load_gives_each_step_only_its_own_limits(known):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}, {"Dartagnan": "all"}],
            "cpuTimeLimits": {"Dartagnan": 5}, "memoryLimits": {"Dartagnan": 2**20}}

    goblint, dartagnan = config.load(conf, known).steps

    assert (goblint.memory_limit, goblint.cpu_time_limit) == (None, None)
    assert (dartagnan.memory_limit, dartagnan.cpu_time_limit) == (2**20, 5)


def test_load_keeps_the_limits_as_the_conf_gives_them(known):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}, {"Deagle": "all"}],
            "memoryLimits": {"Goblint": "70%", "Deagle": "123456"}, "cpuTimeLimits": {"Goblint": 30}}

    goblint, deagle = config.load(conf, known).steps

    assert (goblint.memory_limit, goblint.cpu_time_limit) == ("70%", 30)
    assert deagle.memory_limit == "123456"


def test_a_step_is_frozen():
    step = Step("Goblint", "all")

    with pytest.raises(dataclasses.FrozenInstanceError):
        step.accept = "true"


def test_load_prints_nothing(known, capsys):
    """The standard output of a run starts with the first component's lines;
    parseConf once printed the list of tool objects, with their addresses."""
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}, [{"Deagle": "all"}]]}

    config.load(conf, known)

    assert capsys.readouterr().out == ""


# --- confs that load refuses ---------------------------------------------------

@pytest.mark.parametrize("tools", [
    [{"Goblint": "all"}, {"Goblint": "true"}],
    [{"Goblint": "all"}, [{"Deagle": "all"}, {"Goblint": "all"}]],
    [[{"Goblint": "all"}, [{"Deagle": "all"}]], [{"Deagle": "all"}]],
    [{"Goblint": "all", "Deagle": "all"}, {"Deagle": "false"}],
], ids=["flat", "list-in-list", "two-lists", "one-element-two-keys"])
def test_load_refuses_a_component_named_twice(known, tools):
    conf = {"runType": "parallel", "tools": tools}

    with pytest.raises(ValueError, match=r"'(Goblint|Deagle)' is named more than once"):
        config.load(conf, known)


@pytest.mark.parametrize("limits", ["memoryLimits", "cpuTimeLimits"])
def test_load_refuses_a_limit_for_a_component_that_is_not_in_the_tree(known, limits):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}],
            limits: {"Goblint": 1000, "Deagle": 1000}}

    with pytest.raises(ValueError, match=f"{limits} has a limit for 'Deagle'"):
        config.load(conf, known)


def test_load_accepts_limits_for_components_in_nested_lists(known):
    conf = {"runType": "sequential",
            "tools": [{"Goblint": "all"}, [{"Deagle": "all"}]],
            "memoryLimits": {"Deagle": "70%"}, "cpuTimeLimits": {"Goblint": 30}}

    config.load(conf, known)


def test_load_refuses_a_component_it_does_not_know(known):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}, [{"Goblin": "all"}]]}

    with pytest.raises(ValueError, match="'Goblin' in the conf's tools is not a component"):
        config.load(conf, known)


@pytest.mark.parametrize("acceptance", ["unknown", "TRUE", "", None])
def test_load_refuses_an_acceptance_other_than_true_false_or_all(known, acceptance):
    conf = {"runType": "sequential", "tools": [{"Goblint": acceptance}]}

    with pytest.raises(ValueError, match="'Goblint' has acceptance"):
        config.load(conf, known)


def test_load_refuses_an_unknown_run_type_with_RunTypeError(known):
    conf = {"runType": "interleaved", "tools": [{"Goblint": "all"}]}

    with pytest.raises(config.RunTypeError,
                       match="execution type in conf file is incorrect. "
                             "Must be 'parallel' or 'sequential'"):
        config.load(conf, known)
    assert issubclass(config.RunTypeError, ValueError)


def test_a_conf_error_in_the_tools_comes_before_the_run_type(known):
    conf = {"runType": "interleaved", "tools": [{"Goblint": "all"}, {"Goblint": "all"}]}

    with pytest.raises(ValueError, match="named more than once") as raised:
        config.load(conf, known)
    assert not isinstance(raised.value, config.RunTypeError)


@pytest.mark.parametrize("key", ["runType", "tools"])
def test_a_conf_without_run_type_or_tools_raises_KeyError(known, key):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}]}
    del conf[key]

    with pytest.raises(KeyError, match=key):
        config.load(conf, known)
