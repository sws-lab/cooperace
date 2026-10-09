"""Loading a conf: config.load_strategies and config.load, the trees they build
and the confs they refuse."""
import dataclasses
import json
import re
from pathlib import Path

import pytest

from src.cooperace import config
from src.cooperace.components import REGISTRY
from src.cooperace.config import Parallel, Sequence, Step

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_CONFS = sorted(
    [*ROOT.glob("conf/*.json"), *ROOT.glob("conf/combinations*/*.json"),
     *ROOT.glob("tests/integration/conf/*.json")])


@pytest.fixture
def known(runner):
    """The names of the components CoOpeRace can run."""
    return runner.registry


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


@pytest.mark.parametrize("path", SHIPPED_CONFS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_shipped_conf_is_the_strategy_for_no_data_race_only(path, known):
    conf = json.loads(path.read_text())

    assert config.load_strategies(conf, known) == {"no-data-race": config.load(conf, known)}


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

    with pytest.raises(config.ConfError, match=r"'(Goblint|Deagle)' is named more than once"):
        config.load(conf, known)


@pytest.mark.parametrize("limits", ["memoryLimits", "cpuTimeLimits"])
def test_load_refuses_a_limit_for_a_component_that_is_not_in_the_tree(known, limits):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}],
            limits: {"Goblint": 1000, "Deagle": 1000}}

    with pytest.raises(config.ConfError, match=f"{limits} has a limit for 'Deagle'"):
        config.load(conf, known)


def test_load_accepts_limits_for_components_in_nested_lists(known):
    conf = {"runType": "sequential",
            "tools": [{"Goblint": "all"}, [{"Deagle": "all"}]],
            "memoryLimits": {"Deagle": "70%"}, "cpuTimeLimits": {"Goblint": 30}}

    config.load(conf, known)


def test_load_refuses_a_component_it_does_not_know(known):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}, [{"Goblin": "all"}]]}

    with pytest.raises(config.ConfError, match="'Goblin' in the conf's tools is not a component"):
        config.load(conf, known)


@pytest.mark.parametrize("acceptance", ["unknown", "TRUE", "", None])
def test_load_refuses_an_acceptance_other_than_true_false_or_all(known, acceptance):
    conf = {"runType": "sequential", "tools": [{"Goblint": acceptance}]}

    with pytest.raises(config.ConfError, match="'Goblint' has acceptance"):
        config.load(conf, known)


def test_load_refuses_an_unknown_run_type(known):
    conf = {"runType": "interleaved", "tools": [{"Goblint": "all"}]}

    with pytest.raises(config.ConfError, match="runType is 'interleaved', expected 'sequential' or 'parallel'"):
        config.load(conf, known)


def test_every_refusal_of_load_is_a_ConfError_and_so_a_ValueError():
    assert issubclass(config.ConfError, ValueError)


def test_a_conf_error_in_the_tools_comes_before_the_run_type(known):
    conf = {"runType": "interleaved", "tools": [{"Goblint": "all"}, {"Goblint": "all"}]}

    with pytest.raises(config.ConfError, match="named more than once"):
        config.load(conf, known)


@pytest.mark.parametrize("key", ["runType", "tools"])
def test_load_refuses_a_conf_without_run_type_or_tools(known, key):
    conf = {"runType": "sequential", "tools": [{"Goblint": "all"}]}
    del conf[key]

    with pytest.raises(config.ConfError, match=f"the conf has no '{key}'"):
        config.load(conf, known)


@pytest.mark.parametrize("conf, message", [
    ([], "the conf must be a JSON object"),
    ({"runType": "sequential", "tools": {"Goblint": "all"}}, "the conf's tools must be a list"),
    ({"runType": "sequential", "tools": [[{"Goblint": "all"}], "Goblint"]},
     "neither an object nor a list: 'Goblint'"),
    ({"runType": "sequential", "tools": ["Goblint"]}, "neither an object nor a list"),
    ({"runType": "sequential", "tools": [{"Goblint": "all"}], "memoryLimits": [1]},
     "memoryLimits must be an object"),
], ids=["array", "tools-object", "nested-string", "string", "limits-array"])
def test_load_refuses_a_conf_of_the_wrong_shape(known, conf, message):
    with pytest.raises(config.ConfError, match=message):
        config.load(conf, known)


def test_component_names_lists_the_components_in_the_order_of_the_conf(known):
    conf = {"runType": "sequential",
            "tools": [{"Goblint": "all"}, [{"Deagle": "all"}, [{"Dartagnan": "all", "nacpa": "true"}]]]}

    assert config.component_names(config.load(conf, known)) == ["Goblint", "Deagle", "Dartagnan", "nacpa"]


# --- a strategy per property ---------------------------------------------------

SEQUENTIAL_GOBLINT = {"runType": "sequential", "tools": [{"Goblint": "all"}]}


def test_load_strategies_loads_the_strategy_of_each_property(known):
    conf = {"properties": {
        "valid-memsafety": {"runType": "parallel", "tools": [{"Goblint": "true"}, {"Dartagnan": "all"}],
                            "memoryLimits": {"Dartagnan": "70%"}},
        "no-data-race": {"runType": "sequential", "tools": [{"Goblint": "true"}, [{"Dartagnan": "all"}]],
                         "cpuTimeLimits": {"Goblint": 30}},
    }}

    assert config.load_strategies(conf, known) == {
        "valid-memsafety": Parallel((Step("Goblint", "true"), Step("Dartagnan", "all", "70%"))),
        "no-data-race": Sequence((Step("Goblint", "true", None, 30), Parallel((Step("Dartagnan", "all"),)))),
    }


def test_load_strategies_accepts_every_property_and_no_property(known):
    names = ["unreach-call", "no-overflow", "valid-memsafety", "no-data-race"]

    assert list(config.load_strategies({"properties": {name: SEQUENTIAL_GOBLINT for name in names}}, known)) == names
    assert config.load_strategies({"properties": {}}, known) == {}


def test_a_component_may_be_in_the_strategies_of_several_properties(known):
    conf = {"properties": {"unreach-call": SEQUENTIAL_GOBLINT, "no-overflow": SEQUENTIAL_GOBLINT}}

    assert set(config.load_strategies(conf, known)) == {"unreach-call", "no-overflow"}


@pytest.mark.parametrize("conf, message", [
    ([], "the conf must be a JSON object"),
    ({"properties": [SEQUENTIAL_GOBLINT]}, "the conf's properties must be an object"),
    ({"properties": {"no-data-race": SEQUENTIAL_GOBLINT}, **SEQUENTIAL_GOBLINT},
     "a conf with 'properties' has no other key, but this one has 'runType', 'tools'"),
    ({"properties": {"data-race": SEQUENTIAL_GOBLINT}},
     "the conf's properties has a strategy for 'data-race', expected one of unreach-call, no-overflow, "
     "valid-memsafety, no-data-race"),
    ({"properties": {"no-data-race.prp": SEQUENTIAL_GOBLINT}}, "strategy for 'no-data-race.prp'"),
    ({"properties": {"no-overflow": {"runType": "sequential"}}},
     "in the strategy for no-overflow: the conf has no 'tools'"),
    ({"properties": {"no-overflow": SEQUENTIAL_GOBLINT, "unreach-call": {"tools": [], "runType": "x"}}},
     "in the strategy for unreach-call: runType is 'x'"),
    ({"properties": {"no-overflow": [SEQUENTIAL_GOBLINT]}},
     "in the strategy for no-overflow: the conf must be a JSON object"),
], ids=["array", "properties-array", "properties-and-strategy", "unknown-property", "file-name",
        "strategy-without-tools", "second-strategy", "strategy-array"])
def test_load_strategies_refuses(known, conf, message):
    with pytest.raises(config.ConfError, match=re.escape(message)):
        config.load_strategies(conf, known)


@pytest.mark.parametrize("limits", ["memoryLimits", "cpuTimeLimits"])
def test_a_limit_applies_only_inside_the_strategy_that_names_it(known, limits):
    conf = {"properties": {"unreach-call": SEQUENTIAL_GOBLINT,
                           "no-overflow": {"runType": "sequential", "tools": [{"Dartagnan": "all"}],
                                           limits: {"Goblint": 30}}}}

    with pytest.raises(config.ConfError, match=f"in the strategy for no-overflow: {limits} has a limit for 'Goblint'"):
        config.load_strategies(conf, known)


@pytest.mark.parametrize("name", sorted(REGISTRY), ids=lambda name: name.replace(" ", "_"))
def test_every_component_can_have_both_limits_inside_a_strategy_for_a_property(known, name):
    conf = {"properties": {"no-overflow": {"runType": "sequential", "tools": [{"Goblint": "true"}, [{name: "all"}]]
                                           if name != "Goblint" else [{name: "all"}],
                                           "cpuTimeLimits": {name: 60}, "memoryLimits": {name: "70%"}}}}

    steps = steps_of(config.load_strategies(conf, known)["no-overflow"])

    assert Step(name, "all", "70%", 60) in steps
    assert all(step.memory_limit is None and step.cpu_time_limit is None for step in steps if step.component != name)
