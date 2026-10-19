# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The verdict state and the rules for running (verdicts.py), without any
component: the meet per bracket and the ranking of brackets, the witness
supplier, the sub-property, and Schedule on a simulated clock.

The oracle test checks the criterion of conf/README.md, "When a tool is
stopped", on random strategies and answers: the Result of a Schedule, which
stops components early, equals what `reference` computes from the answers of
every component run to its end. `reference` is written from the README's
rules on its own and shares no code with verdicts.py."""
import math
import random
from typing import NamedTuple

import pytest

from src.cooperace import config
from src.cooperace.verdicts import (
    CONTRADICTION,
    ENDED,
    FALSE,
    PENDING,
    RUNNING,
    SKIPPED,
    TRUE,
    UNKNOWN,
    Answer,
    Decision,
    Schedule,
    Verdicts,
)

PROPERTIES = ("no-data-race", "unreach-call", "no-overflow", "valid-memsafety")
# Where a witness can count, and where one is required (witnesses.FORMATS).
COUNTS = {("no-data-race", FALSE), ("unreach-call", TRUE), ("unreach-call", FALSE),
          ("no-overflow", TRUE), ("no-overflow", FALSE), ("valid-memsafety", FALSE)}
REQUIRED = COUNTS - {("valid-memsafety", FALSE)}


def load(strategy):
    """The config.Strategy of the conf strategy `strategy`, with every name in
    its phases known."""
    known = {name for phase in strategy["phases"]
             for name in (phase["tools"] if isinstance(phase, dict) else phase)}
    return config.load(strategy, known)


def verdicts_of(strategy, property_name="no-data-race", **answers):
    """Verdicts of `strategy` with `answers` (component name, "_" for "-",
    to Answer) recorded."""
    verdicts = Verdicts(load(strategy), property_name)
    for name, answer in answers.items():
        verdicts.record(name.replace("_", "-"), answer)
    return verdicts


DATA_RACE = {"phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 60}, ["Goblint", "Dartagnan"]],
             "cpuTimeLimits": {"sv-sanitizers": 30},
             "answers": [["Goblint:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
             "witnesses": ["sv-sanitizers", "Dartagnan"]}


# --- the meet and the brackets -----------------------------------------------

def test_no_answer_is_no_decision():
    assert verdicts_of(DATA_RACE).decision() is None
    assert verdicts_of(DATA_RACE).result().verdict == UNKNOWN


def test_an_answer_without_an_entry_is_ignored():
    verdicts = Verdicts(load(DATA_RACE), "no-data-race")

    assert not verdicts.record("Goblint", Answer(FALSE))
    assert verdicts.answers == {}
    assert verdicts.record("Goblint", Answer(TRUE))


def test_the_highest_bracket_with_an_answer_decides():
    verdicts = verdicts_of(DATA_RACE, Goblint=Answer(TRUE), Dartagnan=Answer(FALSE, witness=True))

    assert verdicts.decision() == Decision(0, TRUE)
    assert verdicts.result().verdict == TRUE
    assert verdicts.result().tool == "Goblint"


def test_true_and_false_in_one_bracket_are_a_contradiction_naming_both():
    strategy = {"phases": [["Goblint", "Dartagnan", "Zed"]], "answers": [["Goblint:true", "Dartagnan", "Zed"]],
                "witnesses": []}
    verdicts = verdicts_of(strategy, Goblint=Answer(TRUE), Dartagnan=Answer(FALSE, witness=True), Zed=Answer(TRUE))

    result = verdicts.result()
    assert result.verdict == CONTRADICTION
    assert [(entry.tool, entry.verdict) for entry in result.disagreeing] == [
        ("Goblint", TRUE), ("Zed", TRUE), ("Dartagnan", FALSE)]


def test_a_witness_less_answer_counts_in_the_meet_and_cannot_supply():
    # Dartagnan's false without a witness decides above a true, and the
    # result is unknown since no-data-race's false needs a witness.
    strategy = {"phases": [["Dartagnan", "Goblint"]], "answers": [["Dartagnan:false"], ["Goblint:true"]],
                "witnesses": ["Dartagnan"]}
    verdicts = verdicts_of(strategy, Dartagnan=Answer(FALSE), Goblint=Answer(TRUE))

    assert verdicts.decision() == Decision(0, FALSE)
    assert verdicts.result().verdict == UNKNOWN
    assert verdicts.result().decision == Decision(0, FALSE)


# --- the witness supplier and the sub-property --------------------------------

def test_the_first_tool_of_witnesses_with_a_witness_supplies_it_from_any_bracket():
    verdicts = verdicts_of(DATA_RACE, sv_sanitizers=Answer(FALSE), Dartagnan=Answer(FALSE, witness=True))

    result = verdicts.result()
    assert (result.verdict, result.tool, result.witness) == (FALSE, "Dartagnan", True)


def test_without_a_listed_supplier_an_answer_of_the_deciding_bracket_supplies_it():
    strategy = {"phases": [["B", "A"]], "answers": [["B:false", "A:false"]], "witnesses": []}
    verdicts = verdicts_of(strategy, A=Answer(FALSE, witness=True), B=Answer(FALSE, witness=True))

    assert verdicts.supplier_order(FALSE, 0) == ["A", "B"]
    assert verdicts.result().tool == "A"


def test_no_witness_is_delivered_where_none_can_count():
    strategy = {"phases": [["A"]], "answers": [["A"]], "witnesses": ["A"]}

    result = verdicts_of(strategy, "no-data-race", A=Answer(TRUE, witness=True)).result()
    assert (result.verdict, result.witness) == (TRUE, False)

    result = verdicts_of(strategy, "valid-memsafety", A=Answer(TRUE, witness=True)).result()
    assert (result.verdict, result.witness) == (TRUE, False)


def test_a_valid_memsafety_false_needs_no_witness_but_delivers_one():
    strategy = {"phases": [["A", "B"]], "answers": [["A:false", "B:false"]], "witnesses": ["A", "B"]}

    without = verdicts_of(strategy, "valid-memsafety", B=Answer(FALSE, "valid-free")).result()
    assert (without.verdict, without.tool, without.witness, without.subproperty) == (FALSE, "B", False, "valid-free")

    supplied = verdicts_of(strategy, "valid-memsafety", A=Answer(FALSE, "valid-deref"),
                           B=Answer(FALSE, "valid-free", witness=True)).result()
    assert (supplied.tool, supplied.witness, supplied.subproperty) == ("B", True, "valid-free")


def test_two_sub_properties_in_one_bracket_are_no_contradiction():
    strategy = {"phases": [["Deagle", "sv-sanitizers"]], "answers": [["Deagle:false", "sv-sanitizers:false"]],
                "witnesses": ["Deagle", "sv-sanitizers"]}
    verdicts = verdicts_of(strategy, "valid-memsafety", Deagle=Answer(FALSE, "valid-deref"),
                           sv_sanitizers=Answer(FALSE, "valid-free"))

    result = verdicts.result()
    assert (result.verdict, result.tool, result.subproperty) == (FALSE, "Deagle", "valid-deref")


# --- keeps_running ------------------------------------------------------------

def test_every_tool_keeps_running_before_a_decision():
    verdicts = verdicts_of(DATA_RACE)

    assert all(verdicts.keeps_running(tool, verdicts.strategy.tools) for tool in verdicts.strategy.tools)


def test_after_a_witnessed_false_only_the_higher_bracket_keeps_running():
    verdicts = verdicts_of(DATA_RACE, sv_sanitizers=Answer(FALSE, witness=True))
    live = {"Goblint", "Dartagnan"}

    assert verdicts.keeps_running("Goblint", live)        # rule 1
    assert not verdicts.keeps_running("Dartagnan", live)


def test_after_a_witness_less_false_a_listed_tool_keeps_running_for_the_witness():
    verdicts = verdicts_of(DATA_RACE, sv_sanitizers=Answer(FALSE))

    assert verdicts.keeps_running("Dartagnan", {"Goblint", "Dartagnan"})  # rule 3


def test_a_tool_that_could_contradict_keeps_running():
    strategy = {"phases": [["A", "B", "C"]], "answers": [["A:true", "B:false", "C:true"]], "witnesses": []}
    verdicts = verdicts_of(strategy, A=Answer(TRUE))

    assert verdicts.keeps_running("B", {"B", "C"})        # rule 2
    assert not verdicts.keeps_running("C", {"B", "C"})


def test_the_unreach_call_example_of_v_star():
    # sv-sanitizers' false with a witness is decided while Deagle, whose true
    # is trusted more, runs. Deagle could answer true without a witness, and
    # Dartagnan's true with one would then be delivered.
    strategy = {"phases": [["Deagle", "sv-sanitizers", "Dartagnan"]],
                "answers": [["Deagle:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
                "witnesses": ["sv-sanitizers", "Dartagnan"]}
    verdicts = verdicts_of(strategy, "unreach-call", sv_sanitizers=Answer(FALSE, "unreach-call", witness=True))

    assert verdicts.keeps_running("Dartagnan", {"Deagle", "Dartagnan"})
    # Once Deagle has ended without an answer, v is false alone.
    assert not verdicts.keeps_running("Dartagnan", {"Dartagnan"})


# --- Schedule on a simulated clock --------------------------------------------

class Behaviour(NamedTuple):
    """How a simulated component runs: it ends `duration` seconds after it
    starts with `answer` (an Answer or None), or never by itself if
    `duration` is None."""

    duration: float | None
    answer: Answer | None = None


def simulate(strategy, property_name, behaviours):
    """Runs the config.Strategy `strategy` with a Schedule on a simulated
    clock, each component as its Behaviour in `behaviours` says, and returns
    the Result and the components started, in order. Checks on the way that
    no component starts twice and that the Schedule's RUNNING components are
    those started and not yet ended or stopped."""
    schedule = Schedule(Verdicts(strategy, property_name))
    now = 0.0
    ends = {}
    started = []

    def act(actions):
        for tool in actions.stop:
            del ends[tool]
        for tool in actions.start:
            assert tool not in started
            started.append(tool)
            duration = behaviours[tool].duration
            ends[tool] = math.inf if duration is None else now + duration
        assert set(ends) == {tool for tool, state in schedule.states.items() if state == RUNNING}

    act(schedule.begin(now))
    while not schedule.over:
        deadline = schedule.deadline()
        deadline = math.inf if deadline is None else deadline
        tool, end = min(ends.items(), key=lambda item: item[1])
        if end <= deadline:
            now = end
            del ends[tool]
            act(schedule.ended(tool, behaviours[tool].answer, now))
        else:
            assert deadline < math.inf, "a component never ends and its phase has no wall-time limit"
            now = deadline
            act(schedule.time_passed(now))
    assert PENDING not in schedule.states.values()
    return schedule.verdicts.result(), started


def test_the_data_race_example_with_a_witness_dartagnan_never_starts():
    behaviours = {"sv-sanitizers": Behaviour(0.3, Answer(FALSE, witness=True)),
                  "Goblint": Behaviour(5, None), "Dartagnan": Behaviour(1, Answer(FALSE, witness=True))}

    result, started = simulate(load(DATA_RACE), "no-data-race", behaviours)

    assert (result.verdict, result.tool) == (FALSE, "sv-sanitizers")
    assert started == ["sv-sanitizers", "Goblint"]


def test_the_data_race_example_without_a_witness_dartagnan_starts_in_phase_2():
    behaviours = {"sv-sanitizers": Behaviour(0.3, Answer(FALSE)),
                  "Goblint": Behaviour(5, None), "Dartagnan": Behaviour(1, Answer(FALSE, witness=True))}

    result, started = simulate(load(DATA_RACE), "no-data-race", behaviours)

    assert (result.verdict, result.tool, result.witness) == (FALSE, "Dartagnan", True)
    assert started == ["sv-sanitizers", "Goblint", "Dartagnan"]


def test_a_phase_tool_that_never_ends_is_stopped_at_the_wall_time_limit():
    behaviours = {"sv-sanitizers": Behaviour(None), "Goblint": Behaviour(100, None),
                  "Dartagnan": Behaviour(1, Answer(FALSE, witness=True))}
    schedule = Schedule(Verdicts(load(DATA_RACE), "no-data-race"))

    assert schedule.begin(0).start == ("sv-sanitizers", "Goblint")
    assert schedule.deadline() == 60
    assert schedule.time_passed(59.9) == ((), (), (), None)
    actions = schedule.time_passed(60)
    assert actions.stop == ("sv-sanitizers",) and actions.timed_out == ("sv-sanitizers",)
    assert actions.start == ("Dartagnan",) and actions.phase == 0
    assert schedule.states["Goblint"] == RUNNING and schedule.phase == 1

    result, _ = simulate(load(DATA_RACE), "no-data-race", behaviours)
    assert (result.verdict, result.tool) == (FALSE, "Dartagnan")


def test_a_tool_not_needed_when_its_phase_comes_is_skipped():
    schedule = Schedule(Verdicts(load(DATA_RACE), "no-data-race"))
    schedule.begin(0)
    schedule.ended("sv-sanitizers", Answer(FALSE, witness=True), 1)

    schedule.ended("Goblint", None, 2)

    assert schedule.states == {"sv-sanitizers": ENDED, "Goblint": ENDED, "Dartagnan": SKIPPED}
    assert schedule.over


# --- the oracle ---------------------------------------------------------------

def reference(strategy, property_name, answers):
    """What CoOpeRace prints when every component has run to its end with
    `answers` (component to Answer, None for no verdict), by the rules of
    conf/README.md: (verdict, the component whose witness is delivered or
    None, the sub-property of a false). The verdict is "true", "false",
    "unknown" or "contradiction"."""
    entries = {(entry.tool, entry.verdict): index
               for index, bracket in enumerate(strategy.answers) for entry in bracket}
    counted = {tool: answer for tool, answer in answers.items()
               if answer is not None and (tool, answer.verdict) in entries}
    for index in range(len(strategy.answers)):
        meet = {answer.verdict for tool, answer in counted.items() if entries[(tool, answer.verdict)] == index}
        if len(meet) == 2:
            return CONTRADICTION, None, None
        if meet:
            (verdict,) = meet
            break
    else:
        return UNKNOWN, None, None
    order = [tool for tool in strategy.witnesses if any(t == tool and v == verdict for t, v in entries)]
    order += sorted(tool for (tool, v), i in entries.items() if i == index and v == verdict and tool not in order)
    agreeing = [tool for tool in order if tool in counted and counted[tool].verdict == verdict]
    supplier = None
    if (property_name, verdict) in COUNTS:
        supplier = next((tool for tool in agreeing if counted[tool].witness), None)
    if supplier is None and (property_name, verdict) in REQUIRED:
        return UNKNOWN, None, None
    if supplier is not None:
        source = supplier
    else:
        source = next((tool for tool in agreeing if counted[tool].subproperty is not None), agreeing[0])
    return verdict, supplier, counted[source].subproperty if verdict == FALSE else None


def subproperty(rng, property_name):
    """A sub-property of a false, as witnesses.subproperty_of gives it from
    components.reported_result: none for no-data-race, the property for
    unreach-call and no-overflow, and one of three for valid-memsafety."""
    if property_name == "no-data-race":
        return None
    if property_name == "valid-memsafety":
        return rng.choice(["valid-deref", "valid-free", "valid-memtrack"])
    return property_name


def random_case(rng):
    """A random strategy of up to 5 components in up to 3 phases, a property,
    and a Behaviour for each component. A component that ends by itself does
    so before 10 s, and every wall-time limit is at least 10 s, so that
    whether a component answers does not depend on when its phase started; a
    component that never ends is drawn only when every phase has a limit."""
    property_name = rng.choice(PROPERTIES)
    names = [f"T{index}" for index in range(rng.randint(1, 5))]
    count = rng.randint(1, 3)
    spans = {}
    for name in names:
        first = rng.randrange(count)
        spans[name] = range(first, rng.randint(first, count - 1) + 1)
    phases = [[name for name in names if index in spans[name]] for index in range(count)]
    phases = [tools for tools in phases if tools]
    limits = [rng.choice([None, rng.uniform(10, 20)]) for _ in phases]
    phases = [tools if limit is None else {"tools": tools, "wallTimeLimit": limit}
              for tools, limit in zip(phases, limits, strict=True)]
    brackets = [[] for _ in range(rng.randint(1, 4))]
    for name in names:
        kind = rng.choice(["both", "split", "true", "false"])
        if kind == "both":
            rng.choice(brackets).append(name)
        elif kind == "split":
            rng.choice(brackets).append(f"{name}:true")
            rng.choice(brackets).append(f"{name}:false")
        else:
            rng.choice(brackets).append(f"{name}:{kind}")
    brackets = [bracket for bracket in brackets if bracket]
    witnesses = rng.sample(names, rng.randint(0, len(names)))
    strategy = load({"phases": phases, "answers": brackets, "witnesses": witnesses})
    hangs = all(limit is not None for limit in limits)
    behaviours = {}
    for name in names:
        if hangs and rng.random() < 0.15:
            behaviours[name] = Behaviour(None)
            continue
        verdict = rng.choice([TRUE, FALSE, FALSE, None])
        answer = None
        if verdict is not None:
            witness = (property_name, verdict) in COUNTS and rng.random() < 0.6
            answer = Answer(verdict, subproperty(rng, property_name) if verdict == FALSE else None, witness)
        behaviours[name] = Behaviour(rng.uniform(0, 10), answer)
    return strategy, property_name, behaviours


def test_the_reference_agrees_with_the_examples():
    strategy = load(DATA_RACE)
    answers = {"sv-sanitizers": Answer(FALSE), "Goblint": None, "Dartagnan": Answer(FALSE, witness=True)}

    assert reference(strategy, "no-data-race", answers) == (FALSE, "Dartagnan", None)


@pytest.mark.parametrize("seed", range(40))
def test_stopping_early_prints_what_running_every_tool_to_the_end_prints(seed):
    rng = random.Random(seed)
    for _ in range(100):
        strategy, property_name, behaviours = random_case(rng)
        result, _ = simulate(strategy, property_name, behaviours)

        expected = reference(strategy, property_name, {tool: b.answer for tool, b in behaviours.items()})
        got = (result.verdict, result.tool if result.witness else None,
               result.subproperty if result.verdict == FALSE else None)
        assert got == expected, (strategy, property_name, behaviours)
