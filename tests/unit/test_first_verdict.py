# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The first-verdict mode ("firstVerdict": true, conf/README.md "The
first-verdict mode"): the loader's checks, the rules of verdicts.Verdicts and
Schedule in the mode, run_strategy with the fake StepRunner of
test_strategy, and random scenarios through cli.run with the real
run_component on components faked one level below it, as in
test_no_synthesized_verdicts.

The scenarios check that the result is the verdict of the component whose
accepted verdict ended first, delivered right when it ended (not at a phase
end, another component's end or the budget), with that component's own
witness only; that a verdict without an entry, a verdict of a run ended by a
limit, and a verdict whose required witness does not pass are no verdict;
and that a run without an accepted verdict gives exactly `unknown`."""
import json
import queue
import random
import threading
import time
from pathlib import Path

import pytest
from benchexec import result as bresult
from support import CORRECTNESS_WITNESS, PASSING_WITNESS
from test_strategy import Script, printed, run
from test_witnesslint import FAKE_LINTER

from src.cooperace import cli, components, config, execution, strategy, witnesslint
from src.cooperace.components import REGISTRY, ComponentCommand, ComponentRunner, confirm_verdict, reported_result
from src.cooperace.config import ConfError, Entry
from src.cooperace.processes import ComponentGroup
from src.cooperace.verdicts import FALSE, TRUE, UNKNOWN, Answer, Schedule, Verdicts
from src.cooperace.witnesses import needs_witness, passes, specification, subproperty_of, witness_format

ROOT = Path(__file__).resolve().parents[2]
PROPERTY_FILES = {name: ROOT / "tests" / "properties" / f"{name}.prp"
                  for name in ("no-data-race", "unreach-call", "no-overflow", "valid-memsafety")}
SIX = ["Goblint", "IKOS-ConC", "Dartagnan", "sv-sanitizers", "ULTIMATE GemCutter", "Deagle"]

FV = {"firstVerdict": True,
      "phases": [{"tools": ["Goblint", "sv-sanitizers"], "wallTimeLimit": 45}, ["Dartagnan"]],
      "cpuTimeLimits": {"Goblint": 30}, "memoryLimits": {"Dartagnan": "80%"}, "runCpuTimeBudget": 870,
      "answers": [["Goblint:true", "sv-sanitizers:false", "Dartagnan"]],
      "ignoreIfCalls": {"Dartagnan": ["pthread_barrier_init"]}}


def strip(strategy, *keys):
    return {key: value for key, value in strategy.items() if key not in keys}


# --- the loader ------------------------------------------------------------------

def test_a_first_verdict_strategy_loads_without_witnesses():
    loaded = config.load(FV, REGISTRY)

    assert loaded.first_verdict
    assert loaded.answers == (frozenset({Entry("Goblint", TRUE), Entry("sv-sanitizers", FALSE),
                                         Entry("Dartagnan", TRUE), Entry("Dartagnan", FALSE)}),)
    assert loaded.witnesses == () and loaded.veto_windows == {}
    assert loaded.step("Goblint") == config.Step("Goblint", "all", None, 30, 870, True)
    assert loaded.position("Dartagnan") == (1, 0) and loaded.position("sv-sanitizers") == (0, 1)


def test_first_verdict_false_or_absent_is_the_mode_of_brackets():
    plain = {"phases": [["Goblint"]], "answers": [["Goblint"]], "witnesses": []}

    assert config.load(plain, REGISTRY) == config.load({**plain, "firstVerdict": False}, REGISTRY)
    assert not config.load(plain, REGISTRY).first_verdict
    assert config.load(plain, REGISTRY).step("Goblint").first_verdict is False


def test_guarded_keeps_the_mode():
    guarded, dropped = config.guarded(config.load(FV, REGISTRY), {"pthread_barrier_init"})

    assert guarded.first_verdict and guarded.tools == ("Goblint", "sv-sanitizers")
    assert set(dropped) == {Entry("Dartagnan", TRUE), Entry("Dartagnan", FALSE)}


@pytest.mark.parametrize("strategy, message", [
    ({**FV, "answers": [["Goblint:true"], ["sv-sanitizers:false", "Dartagnan"]]},
     "firstVerdict true and 2 brackets in answers"),
    ({**FV, "answers": []}, "firstVerdict true and 0 brackets in answers"),
    ({**FV, "witnesses": []}, "the strategy has firstVerdict true and 'witnesses'"),
    ({**FV, "witnesses": ["Dartagnan"]}, "the strategy has firstVerdict true and 'witnesses'"),
    ({**FV, "vetoWindows": {"Goblint:true": ["Dartagnan"]}}, "the strategy has firstVerdict true and 'vetoWindows'"),
    ({**FV, "firstVerdict": 1}, "firstVerdict is 1, expected true or false"),
    ({**FV, "firstVerdict": "true"}, "firstVerdict is 'true', expected true or false"),
    (strip(FV, "answers"), "the strategy has no 'answers'"),
    (strip(FV, "phases"), "the strategy has no 'phases'"),
    ({**FV, "answers": [["Goblint:true", "sv-sanitizers:false"]]}, "component 'Dartagnan' is in a phase but has no "
                                                                   "entry"),
])
def test_the_loader_refuses_what_the_mode_forbids(strategy, message):
    with pytest.raises(ConfError, match=message):
        config.load(strategy, REGISTRY)


def test_load_strategies_refuses_a_mode_with_brackets_in_any_property():
    conf = {"properties": {"no-data-race": FV, "unreach-call": {**FV, "answers": [["Goblint"], ["Dartagnan",
                                                                                                "sv-sanitizers"]]}}}

    with pytest.raises(ConfError, match="in the strategy for unreach-call: .*2 brackets"):
        config.load_strategies(conf, REGISTRY)


# --- Verdicts and Schedule ---------------------------------------------------------

def schedule(strategy, property_name):
    verdicts = Verdicts(config.load(strategy, REGISTRY), property_name)
    return verdicts, Schedule(verdicts)


def test_the_first_accepted_answer_stops_every_other_component_and_starts_none():
    verdicts, sched = schedule(FV, "no-data-race")
    assert sched.begin(0).start == ("Goblint", "sv-sanitizers")

    actions = sched.ended("sv-sanitizers", Answer(FALSE, None, True), 1)

    assert actions.stop == ("Goblint",) and actions.start == ()
    assert sched.over and sched.states["Dartagnan"] == "skipped"
    assert verdicts.result().tool == "sv-sanitizers" and verdicts.result().witness


def test_an_answer_without_an_entry_is_ignored_and_the_schedule_goes_on():
    verdicts, sched = schedule(FV, "no-data-race")
    sched.begin(0)

    actions = sched.ended("Goblint", Answer(FALSE, None, True), 1)   # Goblint has only Goblint:true

    assert verdicts.first is None and verdicts.result().verdict == UNKNOWN
    assert actions.stop == () and sched.states["sv-sanitizers"] == "running"


def test_a_required_witness_that_does_not_pass_is_no_verdict_and_a_later_answer_decides():
    verdicts, sched = schedule(FV, "no-data-race")
    sched.begin(0)
    sched.ended("sv-sanitizers", Answer(FALSE, None, False), 1)      # false needs a witness in no-data-race
    assert verdicts.first is None and not sched.over

    actions = sched.ended("Goblint", None, 2)                         # ends phase 1 without a verdict
    assert actions.start == ("Dartagnan",)
    sched.ended("Dartagnan", Answer(FALSE, None, True), 3)
    result = verdicts.result()
    assert (result.verdict, result.tool, result.witness) == (FALSE, "Dartagnan", True)
    assert sched.over


def test_a_verdict_whose_witness_is_not_required_is_accepted_without_one():
    strategy = {"firstVerdict": True, "phases": [["Deagle", "ULTIMATE GemCutter"]],
                "answers": [["Deagle:false", "ULTIMATE GemCutter"]]}
    verdicts, sched = schedule(strategy, "valid-memsafety")
    sched.begin(0)

    actions = sched.ended("Deagle", Answer(FALSE, "valid-deref", False), 1)

    assert actions.stop == ("ULTIMATE GemCutter",)
    result = verdicts.result()
    assert (result.verdict, result.tool, result.witness, result.subproperty) == (FALSE, "Deagle", False,
                                                                                   "valid-deref")


def test_no_answer_gives_unknown_and_a_later_answer_is_never_recorded():
    verdicts, sched = schedule(FV, "no-data-race")
    sched.begin(0)
    assert verdicts.result().verdict == UNKNOWN
    sched.ended("Goblint", Answer(TRUE), 1)

    assert not verdicts.record("sv-sanitizers", Answer(FALSE, None, True))
    assert verdicts.answers.keys() == {"Goblint"}


# --- run_strategy with the fake StepRunner -------------------------------------------

DATA_RACE_FV = {"firstVerdict": True,
                "phases": [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 60}, ["Dartagnan"]],
                "answers": [["Goblint:true", "sv-sanitizers:false", "Dartagnan"]]}
MEMSAFETY_FV = {"firstVerdict": True, "phases": [["Deagle", "sv-sanitizers"]],
                "answers": [["Deagle", "sv-sanitizers:false"]]}


def test_the_first_answer_is_delivered_at_once_although_a_better_witness_would_follow(capsys):
    started = time.monotonic()
    result, runner = run(MEMSAFETY_FV, "valid-memsafety",
                         Deagle=Script(0.05, FALSE, witness=False, subproperty="valid-deref"),
                         sv_sanitizers=Script(0.6, FALSE, witness=True, subproperty="valid-deref"))
    lines = printed(capsys)

    assert result == "false(valid-deref)"
    assert time.monotonic() - started < 0.4
    assert runner.stopped == ["sv-sanitizers"]
    assert ("deliver", []) in runner.calls
    assert lines[-1] == "CoOpeRace result from: Deagle"
    assert lines[-2].startswith("CoOpeRace first verdict: Deagle false (mode first-verdict), ")
    assert lines[-2].endswith(", no witness delivered")


def test_the_first_answer_s_own_witness_is_delivered(capsys):
    result, runner = run(DATA_RACE_FV, "no-data-race", sv_sanitizers=Script(0.05, FALSE, witness=True),
                         Goblint=Script(0.6, TRUE), Dartagnan=Script(0.01, FALSE, witness=True))
    lines = printed(capsys)

    assert result == "false"
    assert ("deliver", ["sv-sanitizers.witness.yml"]) in runner.calls
    assert runner.started == ["sv-sanitizers", "Goblint"] and runner.stopped == ["Goblint"]
    assert "CoOpeRace answer: sv-sanitizers false, bracket 1, witness sv-sanitizers.witness.yml " \
           "(passes the check)" in lines


def test_a_required_witness_that_fails_is_ignored_and_the_later_answer_decides_when_it_comes(capsys):
    started = time.monotonic()
    result, runner = run(DATA_RACE_FV, "no-data-race", sv_sanitizers=Script(0.05, FALSE, witness=False),
                         Goblint=Script(0.1), Dartagnan=Script(0.3, FALSE, witness=True))
    elapsed = time.monotonic() - started
    lines = printed(capsys)

    assert result == "false"
    assert 0.35 <= elapsed < 0.7
    assert ("deliver", ["Dartagnan.witness.yml"]) in runner.calls
    assert lines[-1] == "CoOpeRace result from: Dartagnan"
    assert any(line.startswith("CoOpeRace answer: sv-sanitizers false, bracket 1, no witness file, which the "
                               "verdict needs: ignored") for line in lines)


def test_a_required_witness_that_fails_with_nobody_else_answering_gives_unknown(capsys):
    result, runner = run(DATA_RACE_FV, "no-data-race", sv_sanitizers=Script(0.05, FALSE, witness=False),
                         Goblint=Script(0.1), Dartagnan=Script(0.1))
    lines = printed(capsys)

    assert result == "unknown"
    assert ("deliver", []) in runner.calls
    assert lines[-1] == ("CoOpeRace first verdict: none (mode first-verdict): no component gave an accepted "
                         "verdict: the result is unknown")


def test_a_verdict_without_an_entry_is_ignored(capsys):
    result, runner = run(DATA_RACE_FV, "no-data-race", sv_sanitizers=Script(0.02, TRUE),
                         Goblint=Script(0.05, FALSE, witness=True), Dartagnan=Script(0.05, TRUE))

    assert result == "true"
    assert runner.started == ["sv-sanitizers", "Goblint", "Dartagnan"]
    assert printed(capsys)[-1] == "CoOpeRace result from: Dartagnan"


def test_the_witness_of_an_ignored_answer_is_never_delivered_with_the_deciding_one(capsys):
    result, runner = run(DATA_RACE_FV, "no-data-race", sv_sanitizers=Script(0.05),
                         Goblint=Script(0.02, FALSE, witness=True), Dartagnan=Script(0.05, TRUE))

    assert result == "true"
    assert ("deliver", []) in runner.calls
    assert printed(capsys)[-1] == "CoOpeRace result from: Dartagnan"


class BatchingQueue(queue.Queue):
    """A queue whose get waits (at most 2 s) until it holds two events, so
    that run_strategy sees two answers at the same time."""

    def get(self, block=True, timeout=None):
        deadline = time.monotonic() + 2
        while self.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.002)
        return super().get(block, timeout)


@pytest.mark.parametrize("seed", range(6))
def test_of_two_answers_seen_at_once_the_first_by_phase_then_place_decides(seed, monkeypatch, capsys):
    monkeypatch.setattr(strategy.queue, "Queue", BatchingQueue)
    rng = random.Random(seed)
    both = {"firstVerdict": True, "phases": [["Goblint", "sv-sanitizers"]],
            "answers": [["Goblint:true", "sv-sanitizers:false"]]}
    #sv-sanitizers ends first, or both at once; Goblint comes first in the phase and decides
    result, runner = run(both, "no-data-race", sv_sanitizers=Script(rng.uniform(0, 0.02), FALSE, witness=True),
                         Goblint=Script(0.03, TRUE))

    assert result == "true"
    lines = printed(capsys)
    assert lines[-1] == "CoOpeRace result from: Goblint"
    assert "CoOpeRace answer: sv-sanitizers false, after CoOpeRace stopped it: ignored" in lines


# --- random scenarios through cli.run --------------------------------------------------

STATUSES = [bresult.RESULT_TRUE_PROP, bresult.RESULT_FALSE_PROP, bresult.RESULT_FALSE_REACH,
            bresult.RESULT_FALSE_OVERFLOW, bresult.RESULT_FALSE_DATARACE, bresult.RESULT_FALSE_DEREF,
            bresult.RESULT_FALSE_FREE, bresult.RESULT_FALSE_MEMTRACK, bresult.RESULT_UNKNOWN, "ERROR (1)",
            "TIMEOUT", "OUT OF MEMORY"]
SCENARIOS = 25
# Seconds within which two ends count as at the same time, and within which the
# result must be delivered after the decider's end.
TIE = 0.03
DELIVERY = 0.5
LINT_TIE = 0.3


def witness_text(property_name, verdict, status, tool):
    """A witness that witnesses.passes accepts for `verdict` on `property_name`
    with the sub-property of `status` (where the table gives a format), marked
    as `tool`'s."""
    formulas = " ".join(specification(property_name, verdict, subproperty_of(status) or property_name) or ())
    if verdict == FALSE:
        text = PASSING_WITNESS.read_text().replace("CHECK( init(main()), LTL(G ! data-race) )", formulas)
    else:
        text = CORRECTNESS_WITNESS.format(specification=formulas)
    return f"# written by {tool}\n{text}"


def random_strategy(rng):
    tools = rng.sample(SIX, rng.randint(2, 5))
    phases, rest = [], list(tools)
    while rest:
        size = rng.randint(1, len(rest))
        part, rest = rest[:size], rest[size:]
        phases.append({"tools": part, "wallTimeLimit": rng.choice([0.25, 3.0])} if rng.random() < 0.6 else part)
    answers = [rng.choice([tool, f"{tool}:true", f"{tool}:false"]) for tool in tools]
    return {"firstVerdict": True, "phases": phases, "answers": [answers], "runCpuTimeBudget": 870}


class FakeComponents:
    """execution.execute_run, components.component_status and the budget,
    faked for `behaviour` (tool to (delay, status, termination reason,
    witness kind)); `ended` records, for each component that ended by itself,
    its status, reason, witness text and end time."""

    def __init__(self, behaviour, budget_after, property_name):
        self.behaviour = behaviour
        self.budget_after = budget_after
        self.property_name = property_name
        self.ended = {}
        self.lock = threading.Lock()
        self.start = time.monotonic()

    def tool(self):
        return threading.current_thread().name.removeprefix("CoOpeRace run of ")

    def execute_run(self, cmdline, cwd, environments, group, *, output_dir, **_limits):
        tool = self.tool()
        delay, status, reason, witness = self.behaviour[tool][:4]
        marker = self.behaviour[tool][4] if len(self.behaviour[tool]) > 4 else None
        if status == "hang":
            raise execution.ComponentHang("start timed out")
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and not group.stopped:
            time.sleep(0.002)
        if group.stopped:
            return execution.FinishedRun(cmdline, -9, "", termination_reason="killed")
        text = None
        if witness is not None:
            verdict = TRUE if status == bresult.RESULT_TRUE_PROP else FALSE
            text = (witness_text(self.property_name, verdict, status, tool) if witness == "passing"
                    else f"# written by {tool}\n- not: a witness\n")
            if marker is not None:
                text = f"# lint: {marker}\n{text}"
            path = Path(output_dir) / REGISTRY[tool].witness.files[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        with self.lock:
            self.ended[tool] = (status, reason, text, time.monotonic())
        return execution.FinishedRun(cmdline, 0 if reason is None else -9, "", cpu_time=delay, wall_time=delay,
                                     termination_reason=reason)

    def component_status(self, actor, cmdline, tool_result, termination_reason=None):
        return self.behaviour[self.tool()][1]

    def run_cpu_seconds(self):
        if self.budget_after is None or time.monotonic() - self.start < self.budget_after:
            return 0.0
        return 1e6


def accepted(root, property_name, tool, status, reason, text, lint=False):
    """The verdict of a component that ended by itself with `status`, if the
    first-verdict mode accepts it, else None; and whether its witness counts:
    by the internal check, or, with `lint`, for a required witness, by the
    fake linter of test_witnesslint (the internal check where it crashes)."""
    for verdict in (TRUE, FALSE):
        if confirm_verdict("all", status, verdict, property_name):
            break
    else:
        return None, False
    sub = subproperty_of(reported_result(property_name, status))
    passing = text is not None and witness_format(property_name, verdict, sub) is not None and passes(
        text, property_name, verdict, sub)
    if lint and text is not None and needs_witness(property_name, verdict):
        if "# lint: invalid" in text:
            passing = False
        elif "# lint: crash" not in text:
            passing = True
    if reason is not None or root.bracket_of(tool, verdict) is None:
        return None, passing
    if needs_witness(property_name, verdict) and not passing:
        return None, passing
    return verdict, passing


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(strategy, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(execution, "task_limit_supported", lambda: True)


@pytest.mark.parametrize("lint", [False, True], ids=["internal-check", "witnesslint"])
@pytest.mark.parametrize("property_name", list(PROPERTY_FILES))
def test_random_scenarios_deliver_the_chronologically_first_accepted_verdict_with_its_own_witness(
        property_name, lint, tmp_path, monkeypatch, capsys, fast):
    check_scenarios(property_name, random_strategy, f"first verdict {property_name}" + (" lint" if lint else ""), lint,
                    tmp_path, monkeypatch, capsys)


# The first-verdict conf that scripts/svcomp-dist.sh packs with CONF_VARIANT=fv-theta
FV_THETA = json.loads((ROOT / "conf" / "svcomp27-fv-theta.json").read_text())
# The first-verdict confs of the repository, by variant: fv-theta and the test conf saf, fv-theta with SAF first
FV_CONFS = {"fv-theta": FV_THETA, "saf": json.loads((ROOT / "conf" / "svcomp27-saf.json").read_text())}
# Its wall-time limits scaled to the scenarios' delays (1200 s becomes 1.2 s, 37 s 0.037 s)
FV_THETA_WALL_SCALE = 0.001


def scaled(conf_strategy, scale):
    """`conf_strategy` with the wall-time limits of its phases multiplied by `scale`."""
    phases = [{**phase, "wallTimeLimit": phase["wallTimeLimit"] * scale}
              if isinstance(phase, dict) and "wallTimeLimit" in phase else phase
              for phase in conf_strategy["phases"]]
    return {**conf_strategy, "phases": phases}


@pytest.mark.parametrize("variant", list(FV_CONFS))
def test_the_fv_theta_conf_is_in_the_mode_in_every_property(variant):
    strategies = config.load_strategies(FV_CONFS[variant], REGISTRY)

    assert set(strategies) == set(PROPERTY_FILES)
    assert all(loaded.first_verdict for loaded in strategies.values())
    assert all(len(loaded.answers) == 1 for loaded in strategies.values())


@pytest.mark.parametrize("variant", list(FV_CONFS))
@pytest.mark.parametrize("lint", [False, True], ids=["internal-check", "witnesslint"])
@pytest.mark.parametrize("property_name", list(PROPERTY_FILES))
def test_random_scenarios_on_the_fv_theta_conf_deliver_the_first_accepted_verdict_with_its_own_witness(
        property_name, lint, variant, tmp_path, monkeypatch, capsys, fast):
    properties = FV_CONFS[variant]["properties"]
    conf_strategy = {key: value for key, value in properties[property_name].items() if key != "witnessLint"}
    conf_strategy = scaled(conf_strategy, FV_THETA_WALL_SCALE)
    #The seed of fv-theta is kept, so that its scenarios are those of before
    seed = ("fv-theta" if variant == "fv-theta" else f"fv-{variant}") + f" {property_name}" + (" lint" if lint else "")
    check_scenarios(property_name, lambda _rng: conf_strategy, seed, lint, tmp_path, monkeypatch, capsys)


def check_scenarios(property_name, make_strategy, seed, lint, tmp_path, monkeypatch, capsys):
    """Runs SCENARIOS random behaviours of the components of the strategies
    `make_strategy(rng)` (with witnessLint if `lint`) through cli.run and checks the
    mode's rules on each."""
    task = tmp_path / "task.c"
    task.write_text("int main(void) { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    rng = random.Random(seed)
    #The lint runs before CoOpeRace sees the answer, so ends that close count as at the same time
    tie = LINT_TIE if lint else TIE
    if lint:
        linter = tmp_path / "linter"
        linter.mkdir()
        (linter / "witnesslinter.py").write_text(FAKE_LINTER)
        monkeypatch.setattr(witnesslint, "LINTER_DIR", str(linter))
        monkeypatch.setattr(witnesslint, "_usable", None)
    counts = {"definite": 0, "unknown": 0, "late-rejected": 0}

    for number in range(SCENARIOS):
        conf_strategy = {**make_strategy(rng), **({"witnessLint": True} if lint else {})}
        conf = {"properties": {property_name: conf_strategy}}
        root = config.load(conf_strategy, REGISTRY)
        names = {REGISTRY[tool].tool().name(): tool for tool in root.tools}
        delays = rng.sample([round(0.04 * k, 3) for k in range(1, 25)], len(root.tools))
        behaviour = {}
        for tool, delay in zip(root.tools, delays, strict=True):
            kind = rng.choices(["status", "limit", "hang"], [12, 3, 1])[0]
            status = rng.choice(STATUSES[:8]) if kind == "limit" else rng.choice(STATUSES)
            behaviour[tool] = (delay, "hang" if kind == "hang" else status,
                               rng.choice(["cputime", "memory"]) if kind == "limit" else None,
                               rng.choice(["passing", "failing", None]),
                               rng.choice([None, None, "invalid", "crash"]) if lint else None)
        fakes = FakeComponents(behaviour, rng.choice([None, None, rng.uniform(0.1, 1.0)]), property_name)
        monkeypatch.setattr(execution, "execute_run", fakes.execute_run)
        monkeypatch.setattr(components, "component_status", fakes.component_status)
        monkeypatch.setattr(strategy, "run_cpu_seconds", fakes.run_cpu_seconds)
        runner = ComponentRunner(str(task), str(PROPERTY_FILES[property_name]), "ILP32", property_name=property_name)
        runner.installation_problems = lambda _names: []
        runner.container_problems = lambda: []
        runner.probe_versions = lambda _names: None
        runner.command = lambda actor, spec, witness_dir, memory=None: ComponentCommand(
            ["component"], str(tmp_path), {})
        delivered_at = []

        def deliver(files, delivered_at=delivered_at, original=runner.deliver):
            delivered_at.append(time.monotonic())
            original(files)

        runner.deliver = deliver

        result = cli.run(conf, runner, ComponentGroup())

        out = capsys.readouterr().out
        lines = out.splitlines()
        where = f"scenario {number} of {property_name}: {conf_strategy}, {behaviour}, budget {fakes.budget_after}"
        ignored_after_stop = {line.removeprefix("CoOpeRace answer: ").split(", after ")[0].rsplit(" ", 1)[0]
                              for line in lines if line.endswith(", after CoOpeRace stopped it: ignored")}
        verdicts = {tool: accepted(root, property_name, tool, *fakes.ended[tool][:3], lint=lint)
                    for tool in fakes.ended}
        #An answer CoOpeRace never saw came after the run had ended (by a decision or a limit),
        #while the witness was checked; it is treated as one that came after a stop
        observed = {tool for tool in fakes.ended
                    if any(line.startswith(f"CoOpeRace answer: {tool} ") for line in lines)}
        ignored_after_stop |= set(fakes.ended) - observed
        candidates = {tool: fakes.ended[tool][3] for tool, (verdict, _) in verdicts.items()
                      if verdict is not None and tool not in ignored_after_stop}
        witness = tmp_path / "witness.yml"
        if not candidates:
            assert result == "unknown", where
            assert not witness.exists(), where
            assert lines[-1].startswith("CoOpeRace first verdict: none (mode first-verdict)"), where
            counts["unknown"] += 1
            continue
        counts["definite"] += 1
        deciders = [names[line.removeprefix("CoOpeRace result from: ")] for line in lines
                    if line.startswith("CoOpeRace result from: ")]
        assert len(deciders) == 1, where
        decider = deciders[0]
        first = min(candidates.values())
        assert decider in candidates and candidates[decider] <= first + tie, where
        #A component that answered later was stopped, or its answer came after the decision
        for tool in ignored_after_stop:
            if verdicts.get(tool, (None,))[0] is not None:
                assert fakes.ended[tool][3] >= candidates[decider] - tie, where
                counts["late-rejected"] += 1
        status, _reason, text, ended_at = fakes.ended[decider]
        verdict, passing = verdicts[decider]
        assert result == reported_result(property_name, status), where
        assert delivered_at and delivered_at[0] - ended_at < DELIVERY, where
        if witness_format(property_name, verdict) is not None and passing:
            assert witness.read_text() == text, where
        else:
            assert not witness.exists(), where
        assert any(line.startswith(f"CoOpeRace first verdict: {decider} {verdict} (mode first-verdict), cputime=")
                   for line in lines), where
        if lint and text is not None and needs_witness(property_name, verdict):
            linted = [line for line in lines if line.startswith((f"CoOpeRace: WitnessLint on {decider}'s witness",
                                                                  "CoOpeRace: WARNING: WitnessLint failed technically "
                                                                  f"on {decider}'s witness"))]
            assert linted, (where, [line for line in lines if "WitnessLint" in line or "answer" in line])
            counts["linted"] = counts.get("linted", 0) + 1

    assert counts["definite"] > 0 and counts["unknown"] > 0, counts


def test_a_verdict_of_a_run_ended_by_a_limit_counts_outside_the_mode_but_not_in_it(tmp_path, monkeypatch, capsys,
                                                                                   fast):
    task = tmp_path / "task.c"
    task.write_text("int main(void) { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    plain = {"phases": [["Goblint"]], "answers": [["Goblint"]], "witnesses": []}
    results = []
    for conf_strategy in (plain, {"firstVerdict": True, **strip(plain, "witnesses")}):
        fakes = FakeComponents({"Goblint": (0.01, bresult.RESULT_TRUE_PROP, "cputime", None)}, None, "no-data-race")
        monkeypatch.setattr(execution, "execute_run", fakes.execute_run)
        monkeypatch.setattr(components, "component_status", fakes.component_status)
        runner = ComponentRunner(str(task), str(PROPERTY_FILES["no-data-race"]), "ILP32",
                                 property_name="no-data-race")
        runner.installation_problems = lambda _names: []
        runner.container_problems = lambda: []
        runner.probe_versions = lambda _names: None
        runner.command = lambda actor, spec, witness_dir, memory=None: ComponentCommand(
            ["component"], str(tmp_path), {})
        results.append(cli.run({"properties": {"no-data-race": conf_strategy}}, runner, ComponentGroup()))

    assert results == ["true", "unknown"]
    assert ("CoOpeRace: Goblint true from a run that ended by terminationreason=cputime: no verdict in the "
            "first-verdict mode") in capsys.readouterr().out


def test_the_readme_example_of_the_mode_loads():
    text = (ROOT / "conf" / "README.md").read_text()
    block = text.split("## The first-verdict mode", 1)[1].split("```json", 1)[1].split("```", 1)[0]

    strategies = config.load_strategies(json.loads(block), REGISTRY)
    assert all(loaded.first_verdict for loaded in strategies.values())


@pytest.mark.parametrize("linter, delivered", [
    ('print("witnesslint finished with exit code 1")\nimport sys\nsys.exit(1)\n', True),   # says invalid to all
    (None, False),                                                                       # the fake: invalid here
])
def test_a_linter_that_fails_its_self_test_leaves_the_decision_to_the_internal_check(
        linter, delivered, tmp_path, monkeypatch, capsys, fast):
    task = tmp_path / "task.c"
    task.write_text("int main(void) { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "linter"
    directory.mkdir()
    (directory / "witnesslinter.py").write_text(linter or FAKE_LINTER)
    monkeypatch.setattr(witnesslint, "LINTER_DIR", str(directory))
    monkeypatch.setattr(witnesslint, "_usable", None)
    #Goblint's true has a witness that passes the internal check; the fake linter calls it invalid
    fakes = FakeComponents({"Goblint": (0.01, bresult.RESULT_TRUE_PROP, None, "passing", "invalid")}, None,
                           "unreach-call")
    monkeypatch.setattr(execution, "execute_run", fakes.execute_run)
    monkeypatch.setattr(components, "component_status", fakes.component_status)
    runner = ComponentRunner(str(task), str(PROPERTY_FILES["unreach-call"]), "ILP32", property_name="unreach-call")
    runner.installation_problems = lambda _names: []
    runner.container_problems = lambda: []
    runner.probe_versions = lambda _names: None
    runner.command = lambda actor, spec, witness_dir, memory=None: ComponentCommand(
        ["component"], str(tmp_path), {})
    conf = {"properties": {"unreach-call": {"firstVerdict": True, "witnessLint": True, "phases": [["Goblint"]],
                                            "answers": [["Goblint"]]}}}

    result = cli.run(conf, runner, ComponentGroup())

    out = capsys.readouterr().out
    assert result == ("true" if delivered else "unknown")
    assert (tmp_path / "witness.yml").exists() == delivered
    assert ("CoOpeRace: WARNING: WitnessLint is not used" in out) == delivered
