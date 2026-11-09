# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Loading a conf: config.load_strategies and config.load, the Strategy they
build, the questions a Strategy answers, and the confs they refuse."""
import copy
import dataclasses
import json
import re
from pathlib import Path

import pytest

from src.cooperace import config
from src.cooperace.components import REGISTRY
from src.cooperace.config import Entry, Phase, Step, Strategy

ROOT = Path(__file__).resolve().parents[2]
# conf/witnesses.json is the witness table, not a conf (test_witness_check.py).
SHIPPED_CONFS = sorted(path for path in [*ROOT.glob("conf/*.json"), *ROOT.glob("tests/integration/conf/*.json")]
                       if path != ROOT / "conf" / "witnesses.json")

# Two strategies of the conf of CoOpeRace's SV-COMP 2027 submission, which the
# tests below load as examples of the conf's semantics.
NO_DATA_RACE = {
    "phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45}, ["Goblint", "Dartagnan"]],
    "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
    "answers": [["Goblint:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
    "witnesses": ["sv-sanitizers", "Dartagnan"],
}
VALID_MEMSAFETY = {
    "phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45}, ["Deagle"]],
    "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
    "answers": [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"]],
    "witnesses": ["Deagle", "sv-sanitizers"],
}
# The same two strategies of the default conf, conf/svcomp27.json, which
# conf/README.md shows: IKOS-ConC in no-data-race, Deagle alone in phase 2 of valid-memsafety.
DEFAULT_NO_DATA_RACE = {
    "phases": [{"tools": ["sv-sanitizers", "Goblint", "IKOS-ConC"], "wallTimeLimit": 45},
               {"tools": ["Goblint", "IKOS-ConC"], "wallTimeLimit": 120}, ["Dartagnan"]],
    "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30, "IKOS-ConC": 60},
    "memoryLimits": {"sv-sanitizers": "30%", "Goblint": "35%", "IKOS-ConC": "30%", "Dartagnan": "80%"},
    "answers": [["Goblint:true", "IKOS-ConC:true"], ["IKOS-ConC:false", "sv-sanitizers:false"], ["Dartagnan"]],
    "witnesses": ["IKOS-ConC", "sv-sanitizers", "Dartagnan"],
    "ignoreIfCalls": {"Dartagnan": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy"],
                      "Dartagnan:true": ["pthread_mutexattr_settype"]},
}
DEFAULT_VALID_MEMSAFETY = {
    "phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45}, ["Deagle"]],
    "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
    "memoryLimits": {"sv-sanitizers": "45%", "Goblint": "45%", "Deagle": "60%"},
    "answers": [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"]],
    "witnesses": ["Deagle", "sv-sanitizers"],
    "ignoreIfCalls": {"Deagle": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy",
                                 "pthread_once", "pthread_mutexattr_settype"]},
}

# One component in one phase, trusted for both verdicts.
GOBLINT = {"phases": [["Goblint"]], "answers": [["Goblint"]], "witnesses": []}


@pytest.fixture
def known(runner):
    """The names of the components CoOpeRace can run."""
    return runner.registry


def with_changes(strategy, **changes):
    """A deep copy of the strategy `strategy` with the keys of `changes` set;
    a value of None deletes the key."""
    changed = copy.deepcopy(strategy)
    for key, value in changes.items():
        if value is None:
            del changed[key]
        else:
            changed[key] = value
    return changed


# --- the confs in the repository ---------------------------------------------

def test_the_repository_has_the_confs_this_file_checks():
    names = {path.relative_to(ROOT).as_posix() for path in SHIPPED_CONFS}

    assert {"conf/svcomp27.json", "tests/integration/conf/only-goblint.json",
            "tests/integration/conf/goblint-1s.json"} <= names


@pytest.mark.parametrize("path", SHIPPED_CONFS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_shipped_conf_loads(path, known):
    strategies = config.load_strategies(json.loads(path.read_text()), known)

    assert strategies
    assert all(isinstance(strategy, Strategy) for strategy in strategies.values())


def test_the_conf_readme_shows_the_default_conf_whose_strategies_this_file_loads():
    text = (ROOT / "conf" / "README.md").read_text()
    block = re.search(r"## The default conf: `conf/svcomp27.json`\n\n.*?```json\n(.*?)```", text, re.DOTALL).group(1)
    shipped = json.loads((ROOT / "conf" / "svcomp27.json").read_text())

    assert json.loads(block) == shipped
    assert shipped["properties"]["no-data-race"] == {**DEFAULT_NO_DATA_RACE, "runCpuTimeBudget": 870}
    assert shipped["properties"]["valid-memsafety"] == {**DEFAULT_VALID_MEMSAFETY, "runCpuTimeBudget": 870}


# --- two strategies of the default conf ---------------------------------------

def test_the_no_data_race_example_loads(known):
    strategy = config.load(NO_DATA_RACE, known)

    assert strategy == Strategy(
        phases=(Phase(("sv-sanitizers", "Goblint"), 45), Phase(("Goblint", "Dartagnan"))),
        answers=(frozenset({Entry("Goblint", "true")}),
                 frozenset({Entry("sv-sanitizers", "false")}),
                 frozenset({Entry("Dartagnan", "true"), Entry("Dartagnan", "false")})),
        witnesses=("sv-sanitizers", "Dartagnan"),
        cpu_time_limits={"sv-sanitizers": 30, "Goblint": 30},
        memory_limits={},
    )


def test_the_valid_memsafety_example_loads(known):
    strategy = config.load(VALID_MEMSAFETY, known)

    assert strategy == Strategy(
        phases=(Phase(("sv-sanitizers", "Goblint"), 45), Phase(("Deagle",))),
        answers=(frozenset({Entry("Goblint", "true")}),
                 frozenset({Entry("Deagle", "false"), Entry("sv-sanitizers", "false")}),
                 frozenset({Entry("Deagle", "true")})),
        witnesses=("Deagle", "sv-sanitizers"),
        cpu_time_limits={"sv-sanitizers": 30, "Goblint": 30},
        memory_limits={},
    )


def test_load_strategies_loads_the_strategy_of_each_property(known):
    conf = {"properties": {"no-data-race": NO_DATA_RACE, "valid-memsafety": VALID_MEMSAFETY}}

    assert config.load_strategies(conf, known) == {
        "no-data-race": config.load(NO_DATA_RACE, known),
        "valid-memsafety": config.load(VALID_MEMSAFETY, known),
    }


# --- what a Strategy answers -----------------------------------------------------

def test_tools_lists_each_component_once_in_the_order_of_the_phases(known):
    assert config.load(NO_DATA_RACE, known).tools == ("sv-sanitizers", "Goblint", "Dartagnan")


def test_phases_of_and_in_phase_give_the_phases_of_a_component(known):
    strategy = config.load(NO_DATA_RACE, known)

    assert strategy.phases_of("Goblint") == range(0, 2)
    assert strategy.phases_of("sv-sanitizers") == range(0, 1)
    assert strategy.phases_of("Dartagnan") == range(1, 2)
    assert strategy.phases_of("Deagle") == range(0)
    assert [strategy.in_phase("Goblint", index) for index in (-1, 0, 1, 2)] == [False, True, True, False]
    assert strategy.in_phase("Dartagnan", 1) and not strategy.in_phase("sv-sanitizers", 1)


def test_bracket_of_gives_the_bracket_of_a_verdict_of_a_component(known):
    strategy = config.load(VALID_MEMSAFETY, known)

    assert strategy.bracket_of("Goblint", "true") == 0
    assert strategy.bracket_of("Goblint", "false") is None
    assert strategy.bracket_of("Deagle", "false") == 1
    assert strategy.bracket_of("sv-sanitizers", "false") == 1
    assert strategy.bracket_of("Deagle", "true") == 2


def test_witness_rank_gives_the_place_in_witnesses(known):
    strategy = config.load(NO_DATA_RACE, known)

    assert [strategy.witness_rank(name) for name in ("sv-sanitizers", "Dartagnan", "Goblint")] == [0, 1, None]


def test_step_gives_a_component_its_own_limits_and_accepts_both_verdicts(known):
    strategy = config.load(with_changes(NO_DATA_RACE, memoryLimits={"Dartagnan": "70%"}), known)

    assert strategy.step("sv-sanitizers") == Step("sv-sanitizers", "all", None, 30)
    assert strategy.step("Dartagnan") == Step("Dartagnan", "all", "70%", None)
    assert strategy.step("Goblint") == Step("Goblint", "all", None, 30)


def test_an_entry_without_a_verdict_is_an_entry_for_both(known):
    strategy = config.load(with_changes(GOBLINT, answers=[["Goblint"]]), known)

    assert strategy.answers == (frozenset({Entry("Goblint", "true"), Entry("Goblint", "false")}),)


def test_a_component_may_have_its_two_verdicts_in_two_brackets(known):
    strategy = config.load(with_changes(GOBLINT, answers=[["Goblint:false"], ["Goblint:true"]]), known)

    assert (strategy.bracket_of("Goblint", "false"), strategy.bracket_of("Goblint", "true")) == (0, 1)


def test_a_component_with_a_space_in_its_name_can_have_an_entry(known):
    strategy = {"phases": [["ULTIMATE Automizer"]], "answers": [["ULTIMATE Automizer:false"]], "witnesses": []}

    assert config.load(strategy, known).bracket_of("ULTIMATE Automizer", "false") == 0


def test_witnesses_may_be_empty_and_limits_may_be_absent(known):
    strategy = config.load(GOBLINT, known)

    assert (strategy.witnesses, strategy.cpu_time_limits, strategy.memory_limits) == ((), {}, {})
    assert strategy.phases == (Phase(("Goblint",), None),)


def test_load_keeps_the_limits_as_the_conf_gives_them(known):
    strategy = config.load(with_changes(GOBLINT, memoryLimits={"Goblint": "12.5%"}, cpuTimeLimits={"Goblint": 0.5}),
                           known)

    assert strategy.step("Goblint") == Step("Goblint", "all", "12.5%", 0.5)


def test_a_strategy_is_frozen(known):
    strategy = config.load(GOBLINT, known)

    with pytest.raises(dataclasses.FrozenInstanceError):
        strategy.witnesses = ("Goblint",)


def test_load_prints_nothing(known, capsys):
    config.load(NO_DATA_RACE, known)

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("name", sorted(REGISTRY), ids=lambda name: name.replace(" ", "_"))
def test_every_component_can_be_in_a_strategy_with_both_limits(known, name):
    strategy = {"phases": [[name]], "answers": [[name]], "witnesses": [name],
                "cpuTimeLimits": {name: 60}, "memoryLimits": {name: "70%"}}

    assert config.load(strategy, known).step(name) == Step(name, "all", "70%", 60)


# --- the refusals of the format, one test each ----------------------------------

def refuses(strategy, known, message):
    """Asserts that load refuses `strategy` with a ConfError whose message
    contains `message`."""
    with pytest.raises(config.ConfError, match=re.escape(message)):
        config.load(strategy, known)


def test_an_entry_naming_a_component_in_no_phase_is_refused(known):
    refuses(with_changes(NO_DATA_RACE, answers=[["Goblint:true"], ["sv-sanitizers:false"], ["Dartagnan"],
                                                ["Deagle:false"]]),
            known, "answers has an entry for 'Deagle', which is in no phase")


def test_a_component_of_a_phase_without_an_entry_is_refused(known):
    refuses(with_changes(NO_DATA_RACE, answers=[["Goblint:true"], ["sv-sanitizers:false"]], witnesses=[]),
            known, "component 'Dartagnan' is in a phase but has no entry in answers")


def test_a_component_in_two_phases_that_are_not_consecutive_is_refused(known):
    refuses(with_changes(GOBLINT, phases=[["Goblint"], ["Dartagnan"], ["Goblint"]],
                         answers=[["Goblint"], ["Dartagnan"]]),
            known, "component 'Goblint' is in phases 1, 3, which are not consecutive")


@pytest.mark.parametrize("answers, where", [
    ([["Goblint:true"], ["Goblint:true"]], "Goblint:true in bracket 1 and in bracket 2"),
    ([["Goblint"], ["Goblint:false"]], "Goblint:false in bracket 1 and in bracket 2"),
    ([["Goblint:true", "Goblint"]], "Goblint:true again in bracket 1"),
], ids=["two-brackets", "both-and-one", "one-bracket"])
def test_one_verdict_of_a_component_in_two_entries_is_refused(known, answers, where):
    refuses(with_changes(GOBLINT, answers=answers), known, f"answers has an entry for {where}")


def test_a_witnesses_component_without_an_entry_is_refused(known):
    refuses(with_changes(NO_DATA_RACE, witnesses=["sv-sanitizers", "Deagle"]),
            known, "witnesses names 'Deagle', which has no entry in answers")


@pytest.mark.parametrize("changes, message", [
    ({"cpuTimeLimits": {"Goblint": 0}}, "cpuTimeLimits gives 'Goblint' the limit 0"),
    ({"cpuTimeLimits": {"Goblint": -5}}, "cpuTimeLimits gives 'Goblint' the limit -5"),
    ({"cpuTimeLimits": {"Goblint": "30"}}, "cpuTimeLimits gives 'Goblint' the limit '30'"),
    ({"cpuTimeLimits": {"Goblint": True}}, "cpuTimeLimits gives 'Goblint' the limit True"),
    ({"cpuTimeLimits": {"Goblint": float("inf")}}, "cpuTimeLimits gives 'Goblint' the limit inf"),
    ({"cpuTimeLimits": {"Goblint": None}}, "cpuTimeLimits gives 'Goblint' the limit None"),
    ({"memoryLimits": {"Goblint": 0}}, "memoryLimits gives 'Goblint' the limit 0"),
    ({"memoryLimits": {"Goblint": "4GB"}}, "memoryLimits gives 'Goblint' the limit '4GB'"),
    ({"memoryLimits": {"Goblint": "0%"}}, "memoryLimits gives 'Goblint' the limit '0%'"),
    ({"memoryLimits": {"Goblint": "150%"}}, "memoryLimits gives 'Goblint' the limit '150%'"),
    ({"memoryLimits": {"Goblint": "-5%"}}, "memoryLimits gives 'Goblint' the limit '-5%'"),
    ({"phases": [{"tools": ["Goblint"], "wallTimeLimit": 0}]}, "phase 1 has the wallTimeLimit 0"),
    ({"phases": [{"tools": ["Goblint"], "wallTimeLimit": "60"}]}, "phase 1 has the wallTimeLimit '60'"),
    ({"phases": [{"tools": ["Goblint"], "wallTimeLimit": None}]}, "phase 1 has the wallTimeLimit None"),
], ids=["cpu-zero", "cpu-negative", "cpu-string", "cpu-bool", "cpu-infinite", "cpu-null", "memory-zero",
        "memory-unit", "memory-zero-percent", "memory-over-100-percent", "memory-negative-percent",
        "wall-zero", "wall-string", "wall-null"])
def test_a_limit_that_is_not_a_positive_number_is_refused(known, changes, message):
    refuses(with_changes(GOBLINT, **changes), known, message)


@pytest.mark.parametrize("phase", [
    {"tools": ["Goblint"], "cpuTimeLimit": 30},
    {"tools": ["Goblint"], "wallTimeLimit": 60, "name": "blitz"},
], ids=["cpu-time-limit", "name"])
def test_a_phase_object_with_another_key_is_refused(known, phase):
    other = next(key for key in phase if key not in ("tools", "wallTimeLimit"))

    refuses(with_changes(GOBLINT, phases=[phase]), known,
            f"phase 1 has the key '{other}'; a phase object has only 'tools' and 'wallTimeLimit'")


@pytest.mark.parametrize("strategy, key", [
    ({"runType": "sequential", "tools": [{"Goblint": "all"}]}, "runType"),
    ({"tools": [{"Goblint": "all"}]}, "tools"),
    ({**GOBLINT, "runType": "parallel"}, "runType"),
], ids=["tree", "tools-only", "mixed"])
def test_a_strategy_in_the_tree_format_is_refused_with_a_message_naming_the_new_format(known, strategy, key):
    refuses(strategy, known, f"the strategy has '{key}', a key of the tree format, which CoOpeRace does not read; "
                             "a strategy has 'phases', 'answers' and 'witnesses'")


def test_a_conf_in_the_tree_format_is_refused_with_a_message_naming_the_new_format(known):
    conf = {"runType": "sequential", "tools": [{"Goblint": "true"}, [{"Dartagnan": "all"}]]}

    with pytest.raises(config.ConfError, match=re.escape(
            "the conf has 'runType', a key of the tree format, which CoOpeRace does not read; "
            "a strategy has 'phases', 'answers' and 'witnesses', as conf/README.md says")):
        config.load_strategies(conf, known)


def test_a_tree_strategy_inside_properties_is_refused_naming_the_property_and_the_new_format(known):
    conf = {"properties": {"no-data-race": {"runType": "sequential", "tools": [{"Goblint": "all"}]}}}

    with pytest.raises(config.ConfError, match=re.escape(
            "in the strategy for no-data-race: the strategy has 'runType', a key of the tree format")):
        config.load_strategies(conf, known)


# --- further refusals of load ---------------------------------------------------------

@pytest.mark.parametrize("strategy, message", [
    ([], "the strategy must be a JSON object"),
    ({**GOBLINT, "verdicts": []}, "the strategy has the key 'verdicts', expected only 'phases', 'answers', "
                                  "'witnesses', 'cpuTimeLimits', 'memoryLimits', 'runCpuTimeBudget'"),
    (with_changes(GOBLINT, phases=None), "the strategy has no 'phases'"),
    (with_changes(GOBLINT, answers=None), "the strategy has no 'answers'"),
    (with_changes(GOBLINT, witnesses=None), "the strategy has no 'witnesses'"),
    (with_changes(GOBLINT, phases=[]), "phases must be a non-empty list of phases"),
    (with_changes(GOBLINT, phases={"tools": ["Goblint"]}), "phases must be a non-empty list of phases"),
    (with_changes(GOBLINT, phases=["Goblint"]), "phase 1 must be a list of component names or an object"),
    (with_changes(GOBLINT, phases=[["Goblint"], []]), "phase 2 has no component"),
    (with_changes(GOBLINT, phases=[{"tools": []}]), "phase 1 has no component"),
    (with_changes(GOBLINT, phases=[{"wallTimeLimit": 60}]), "phase 1 has no 'tools'"),
    (with_changes(GOBLINT, phases=[["Goblint", "Goblint"]]), "phase 1 names 'Goblint' more than once"),
    (with_changes(GOBLINT, phases=[[{"Goblint": "all"}]]), "phase 1 must be a list of component names"),
    (with_changes(GOBLINT, phases=[["Goblnt"]]), "component 'Goblnt' in phases is not a component CoOpeRace can run"),
    (with_changes(GOBLINT, answers={"Goblint": "true"}), "answers must be a list of brackets"),
    (with_changes(GOBLINT, answers=[["Goblint"], []]), "bracket 2 of answers must be a non-empty list of entries"),
    (with_changes(GOBLINT, answers=["Goblint"]), "bracket 1 of answers must be a non-empty list of entries"),
    (with_changes(GOBLINT, answers=[["Goblint:all"]]), "bracket 1 of answers has the entry 'Goblint:all'"),
    (with_changes(GOBLINT, answers=[["Goblint:True"]]), "bracket 1 of answers has the entry 'Goblint:True'"),
    (with_changes(GOBLINT, answers=[["Goblint:"]]), "bracket 1 of answers has the entry 'Goblint:'"),
    (with_changes(GOBLINT, answers=[[":true"]]), "bracket 1 of answers has the entry ':true'"),
    (with_changes(GOBLINT, answers=[[""]]), "bracket 1 of answers has the entry ''"),
    (with_changes(GOBLINT, answers=[[{"Goblint": "all"}]]), "bracket 1 of answers has the entry {'Goblint': 'all'}"),
    (with_changes(GOBLINT, witnesses="Goblint"), "witnesses must be a list of component names"),
    (with_changes(GOBLINT, witnesses=["Goblint", "Goblint"]), "witnesses names 'Goblint' more than once"),
    (with_changes(GOBLINT, cpuTimeLimits={"Deagle": 5}),
     "cpuTimeLimits has a limit for 'Deagle', which is in no phase"),
    (with_changes(GOBLINT, memoryLimits={"Deagle": "70%"}),
     "memoryLimits has a limit for 'Deagle', which is in no phase"),
    (with_changes(GOBLINT, memoryLimits=[1]), "the strategy's memoryLimits must be an object"),
], ids=["array", "unknown-key", "no-phases", "no-answers", "no-witnesses", "empty-phases", "phases-object",
        "phase-string", "empty-phase", "empty-phase-object", "phase-object-without-tools", "twice-in-a-phase",
        "phase-of-objects", "unknown-component", "answers-object", "empty-bracket", "bracket-string",
        "verdict-all", "verdict-capital", "verdict-empty", "name-empty", "entry-empty", "entry-object",
        "witnesses-string", "witnesses-twice", "cpu-limit-key", "memory-limit-key", "limits-array"])
def test_load_refuses(known, strategy, message):
    refuses(strategy, known, message)


def test_every_refusal_of_load_is_a_ConfError_and_so_a_ValueError():
    assert issubclass(config.ConfError, ValueError)


# --- a strategy per property ---------------------------------------------------

def test_load_strategies_accepts_every_property_and_no_property(known):
    names = ["unreach-call", "no-overflow", "valid-memsafety", "no-data-race"]

    assert list(config.load_strategies({"properties": {name: GOBLINT for name in names}}, known)) == names
    assert config.load_strategies({"properties": {}}, known) == {}


@pytest.mark.parametrize("conf, message", [
    ([], "the conf must be a JSON object"),
    (GOBLINT, "the conf has no 'properties', the object that maps each property to its strategy"),
    ({"properties": [GOBLINT]}, "the conf's properties must be an object"),
    ({"properties": {"no-data-race": GOBLINT}, "phases": [["Goblint"]]},
     "a conf has no key but 'properties', but this one has 'phases'"),
    ({"properties": {"data-race": GOBLINT}},
     "the conf's properties has a strategy for 'data-race', expected one of unreach-call, no-overflow, "
     "valid-memsafety, no-data-race"),
    ({"properties": {"no-data-race.prp": GOBLINT}}, "strategy for 'no-data-race.prp'"),
    ({"properties": {"no-overflow": GOBLINT, "unreach-call": with_changes(GOBLINT, witnesses=None)}},
     "in the strategy for unreach-call: the strategy has no 'witnesses'"),
    ({"properties": {"no-overflow": [GOBLINT]}},
     "in the strategy for no-overflow: the strategy must be a JSON object"),
], ids=["array", "bare-strategy", "properties-array", "properties-and-other-key", "unknown-property",
        "file-name", "second-strategy", "strategy-array"])
def test_load_strategies_refuses(known, conf, message):
    with pytest.raises(config.ConfError, match=re.escape(message)):
        config.load_strategies(conf, known)


@pytest.mark.parametrize("limits", ["memoryLimits", "cpuTimeLimits"])
def test_a_limit_applies_only_inside_the_strategy_that_names_it(known, limits):
    conf = {"properties": {"unreach-call": GOBLINT,
                           "no-overflow": {"phases": [["Dartagnan"]], "answers": [["Dartagnan"]], "witnesses": [],
                                           limits: {"Goblint": 30}}}}

    with pytest.raises(config.ConfError,
                       match=f"in the strategy for no-overflow: {limits} has a limit for 'Goblint'"):
        config.load_strategies(conf, known)


# --- runCpuTimeBudget ---------------------------------------------------------------

def test_the_run_cpu_time_budget_is_loaded_and_absent_by_default(known):
    assert config.load(GOBLINT, known).run_cpu_time_budget is None
    assert config.load({**GOBLINT, "runCpuTimeBudget": 870}, known).run_cpu_time_budget == 870


@pytest.mark.parametrize("budget", [0, -1, "870", None, True], ids=["zero", "negative", "string", "null", "bool"])
def test_a_run_cpu_time_budget_that_is_not_a_positive_number_is_refused(known, budget):
    refuses({**GOBLINT, "runCpuTimeBudget": budget}, known,
            f"runCpuTimeBudget is {budget!r}, which is not a positive number of seconds")


def test_the_svcomp27_conf_gives_every_property_a_budget_below_the_competitions_900_s(known):
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    assert {name: s.run_cpu_time_budget for name, s in strategies.items()} == dict.fromkeys(strategies, 870)


def test_the_svcomp27_conf_limits_the_memory_of_each_tool_that_runs_until_the_budget(known):
    """In every phase of conf/svcomp27.json, each tool without a CPU-time limit
    of its own runs until the run's budget, so it has a memory limit, a
    percentage of the run's, and the limits of the tools of one phase add up
    to at most 95%. Two of them together then cannot reach the run's limit,
    which would end the whole run OUT OF MEMORY (BenchExec sets the run
    cgroup's memory.oom.group) and lose the answers already decided. Dartagnan
    in particular, a GraalVM native image whose heap grows to 80% of the
    memory it sees, reached 10 GB at full limits without answering."""
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    for name, strategy in strategies.items():
        for phase in strategy.phases:
            uncapped = [tool for tool in phase.tools if tool not in strategy.cpu_time_limits]
            limits = {tool: strategy.memory_limits.get(tool) for tool in uncapped}
            assert all(isinstance(limit, str) and limit.endswith("%") for limit in limits.values()), (name, limits)
            assert sum(float(limit[:-1]) for limit in limits.values()) <= 95, (name, limits)
        if "Dartagnan" in strategy.tools:
            assert "Dartagnan" in strategy.memory_limits, name


def test_the_svcomp27_conf_limits_the_memory_of_every_phase_1_tool(known):
    """In conf/svcomp27.json every tool of phase 1 has a memory limit too, a
    percentage of the run's, and those of phase 1 add up to at most 95%.
    These tools have CPU-time limits, so the rule above does not cover them;
    yet on pthread-lp64-twins/sssc12-2-lp64 (valid-memsafety) sv-sanitizers
    alone filled the 3 GB of the run within 34 s, and the whole run was
    killed with Goblint's answer already in."""
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    for name, strategy in strategies.items():
        tools = strategy.phases[0].tools
        limits = {tool: strategy.memory_limits.get(tool) for tool in tools}
        assert all(isinstance(limit, str) and limit.endswith("%") for limit in limits.values()), (name, limits)
        assert sum(float(limit[:-1]) for limit in limits.values()) <= 95, (name, limits)


def test_the_svcomp27_conf_limits_the_memory_of_every_phase_to_95_percent(known):
    """In every phase of conf/svcomp27.json the memory limits of all its tools,
    those with a CPU-time limit included, add up to at most 95% of the run's:
    the tools of a phase run at the same time, and a run over its limit is
    killed whole. On the pre-run's single core a tool with a CPU-time limit of
    30 s runs a minute or more of wall time; in no-data-race Dartagnan (80%),
    started beside Goblint (35%) and IKOS-ConC (30%), filled its 2.4 GB of
    3 GB within 26 s of CPU time on ldv-linux-3.14-races/*cafe_ccic* while
    Goblint still ran."""
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    for name, strategy in strategies.items():
        for phase in strategy.phases:
            limits = {tool: strategy.memory_limits.get(tool) for tool in phase.tools}
            assert all(isinstance(limit, str) and limit.endswith("%") for limit in limits.values()), (name, limits)
            assert sum(float(limit[:-1]) for limit in limits.values()) <= 95, (name, limits)


def test_the_svcomp27_conf_ends_every_phase_of_tools_with_cpu_time_limits_by_a_wall_time_limit(known):
    """In conf/svcomp27.json a phase whose tools all have CPU-time limits,
    so that none runs on to the run's CPU-time budget, has a wall-time limit:
    a tool that waits without using CPU time, or a RunExecutor that does not
    return, would otherwise keep the next phase from starting until the
    run's own wall-time limit, which ends it without a verdict. After phase
    1 the limit must not stop a tool before its CPU-time limit even on one
    core that the phase's tools share: it is at least 1.1 s of wall time per
    second of their CPU-time limits together, plus 1 s for each (BenchExec
    ends a tool up to about 1 s after its limit). Phase 1's 45 s stop only
    tools of phase 1 alone; conf/README.md says why they need no more."""
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    for name, strategy in strategies.items():
        for index, phase in enumerate(strategy.phases):
            if not all(tool in strategy.cpu_time_limits for tool in phase.tools):
                continue
            assert phase.wall_time_limit is not None, (name, index + 1)
            if index > 0:
                cpu = sum(strategy.cpu_time_limits[tool] for tool in phase.tools)
                assert phase.wall_time_limit >= 1.1 * cpu + len(phase.tools), (name, index + 1, cpu)


def test_the_svcomp27_conf_keeps_the_task_limits_of_every_phase_to_4096(known):
    """In every phase of conf/svcomp27.json the task limits of its tools
    (ComponentSpec.task_limit) add up to at most 4096: within a run limited
    to 5000 tasks, as README.md runs CoOpeRace under runexec, CoOpeRace
    keeps 900 of its own even when every tool of a phase reaches its limit,
    as sv-sanitizers does on a program that creates threads without bound."""
    strategies = config.load_strategies(json.loads((ROOT / "conf" / "svcomp27.json").read_text()), known)
    for name, strategy in strategies.items():
        for phase in strategy.phases:
            limits = {tool: REGISTRY[tool].task_limit for tool in phase.tools}
            assert all(isinstance(limit, int) and limit > 0 for limit in limits.values()), (name, limits)
            assert sum(limits.values()) <= 4096, (name, limits)


# --- ignoreIfCalls ---------------------------------------------------------------

GUARDED = {**VALID_MEMSAFETY,
           "phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45}, ["Deagle", "Dartagnan"]],
           "answers": [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"], ["Dartagnan"]],
           "witnesses": ["Deagle", "sv-sanitizers", "Dartagnan"],
           "memoryLimits": {"Deagle": "50%"},
           "ignoreIfCalls": {"Deagle": ["pthread_once"], "Dartagnan:true": ["pthread_mutexattr_settype"]}}


def test_ignore_if_calls_loads_a_name_as_its_entries_in_answers(known):
    strategy = config.load(GUARDED, known)

    assert strategy.ignore_if_calls == {Entry("Deagle", "true"): ("pthread_once",),
                                        Entry("Deagle", "false"): ("pthread_once",),
                                        Entry("Dartagnan", "true"): ("pthread_mutexattr_settype",)}


def test_a_name_in_ignore_if_calls_covers_only_the_verdicts_it_has(known):
    strategy = config.load({**GOBLINT, "answers": [["Goblint:true"]], "ignoreIfCalls": {"Goblint": ["f"]}}, known)

    assert strategy.ignore_if_calls == {Entry("Goblint", "true"): ("f",)}


@pytest.mark.parametrize("value, message", [
    ([], "ignoreIfCalls must be an object, not []"),
    ({"Goblint": []}, "ignoreIfCalls gives 'Goblint' [], which is not a non-empty list of C function names"),
    ({"Goblint": ["not a name"]}, "which is not a non-empty list of C function names"),
    ({"Goblint:maybe": ["f"]}, "ignoreIfCalls has the key 'Goblint:maybe'"),
    ({"Deagle": ["f"]}, "ignoreIfCalls names Deagle:true, which is in no bracket of answers"),
])
def test_a_malformed_ignore_if_calls_is_refused(known, value, message):
    with pytest.raises(config.ConfError, match=re.escape(message)):
        config.load({**GOBLINT, "ignoreIfCalls": value}, known)


def test_called_functions_finds_calls_and_not_declarations():
    text = """extern int pthread_once (pthread_once_t *__once_control, void (*__init_routine) (void));
extern int
pthread_mutexattr_settype (pthread_mutexattr_t *__attr, int __kind);
int main(void) {
  pthread_mutexattr_init(&a);
  if (pthread_mutexattr_settype(&a, 1) != 0) return 1;
  (void) pthread_barrier_wait(&b);
  return pthread_barrier_init(&b, 0, 2);
}
"""
    names = ["pthread_once", "pthread_mutexattr_settype", "pthread_barrier_wait", "pthread_barrier_init"]
    assert config.called_functions(text, names) == {"pthread_mutexattr_settype", "pthread_barrier_wait",
                                                    "pthread_barrier_init"}


def test_guarded_drops_the_entries_and_a_component_left_without_one(known):
    strategy = config.load(GUARDED, known)

    kept, dropped = config.guarded(strategy, {"pthread_once"})

    assert dropped == {Entry("Deagle", "true"): ("pthread_once",), Entry("Deagle", "false"): ("pthread_once",)}
    assert kept.answers == (frozenset({Entry("Goblint", "true")}), frozenset({Entry("sv-sanitizers", "false")}),
                            frozenset({Entry("Dartagnan", "true"), Entry("Dartagnan", "false")}))
    assert kept.phases == (Phase(("sv-sanitizers", "Goblint"), 45), Phase(("Dartagnan",)))
    assert kept.witnesses == ("sv-sanitizers", "Dartagnan")
    assert kept.memory_limits == {}
    assert config.guarded(strategy, {"other"}) == (strategy, {})


def test_guarded_keeps_a_component_with_another_entry(known):
    kept, dropped = config.guarded(config.load(GUARDED, known), {"pthread_mutexattr_settype"})

    assert dropped == {Entry("Dartagnan", "true"): ("pthread_mutexattr_settype",)}
    assert kept.answers[-1] == frozenset({Entry("Dartagnan", "false")})
    assert "Dartagnan" in kept.tools


def test_guarded_gives_none_when_every_entry_is_dropped(known):
    strategy = config.load({**GOBLINT, "ignoreIfCalls": {"Goblint": ["f"]}}, known)

    assert config.guarded(strategy, {"f"}) == (None, {Entry("Goblint", "true"): ("f",),
                                                      Entry("Goblint", "false"): ("f",)})
