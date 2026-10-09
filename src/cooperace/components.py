"""The components CoOpeRace runs (REGISTRY) and how one is run: its command
line from its BenchExec tool-info module, its limits, its status and
verdict, its witness files, and the block of protocol lines it prints
(print_component_run). ComponentRunner is the strategy.StepRunner the
strategy runs steps with. The only module of the package that imports
BenchExec.

The standard output is read by programs, so its lines are a protocol: for
each component run, "---<name> logs---", its output, "---end of <name>
logs---", "Tool name: <name> Status: <status> Exit code: <code>" and, unless
CoOpeRace stopped it, "Tool name: <name> Result: <verdict>"; "Memory limit
of <name>: ..." and "CPU-time limit of <name>: ..." before a component with
a limit starts. strategy.execute adds "CoOpeRace result from: <name>" and
"CoOpeRace stopped by signal <n>", cli "CoOpeRace verdict: <verdict>".
A component that cannot be run or crashes is reported in its block and is a
step without a verdict (run_step). A component that is not installed is
found out before any component starts (ComponentRunner.missing_executables),
and cli ends CoOpeRace for it with an error on stderr and no verdict line."""
from __future__ import annotations

import importlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
from collections.abc import Mapping
from dataclasses import dataclass

from benchexec import result as bresult
from benchexec import util as butil
from benchexec.tools.template import BaseTool2, ToolNotFoundException

from . import TOOL_DIR
from .config import Step
from .processes import ComponentGroup, run_in_session, run_memory_limit, with_rlimits
from .strategy import NO_OUTCOME, Outcome


@dataclass(frozen=True)
class WitnessSpec:
    """Which files of a component run are its witness. `directory` is "run" if
    `options` tell the component to write them into the directory run_component
    makes for the run ({dir} in an option stands for it), and "component" if
    the component writes them under its own directory, the working directory
    of its run. `files` are their paths relative to that directory, or None
    for every file there whose name contains "witness" and ends in graphml or
    yml (witness_files_under). collect_witness_files takes only those
    modified during the run, and witness_files_to_file_root delivers a graphml
    file as witness.graphml and a YAML file as witness.yml. A component that
    writes a new witness file, or a new format, needs only a new WitnessSpec
    in its registry entry."""

    directory: str
    options: tuple[str, ...] = ()
    files: tuple[str, ...] | None = None


#For a component without a WitnessSpec of its own: the name pattern, under its own directory
DEFAULT_WITNESS = WitnessSpec("component")

#Ultimate.py --witness-dir; witness.yml is written beside the graphml for "true"
_ULTIMATE_WITNESS = WitnessSpec("run", ("--witness-dir", "{dir}"), ("witness.graphml", "witness.yml"))


@dataclass(frozen=True)
class ComponentSpec:
    """A component CoOpeRace can run. `name` is its name in the conf, `module`
    the name of its BenchExec tool-info module, imported when the component is
    first run, `directory` its directory under tools/ (where
    download-tools.py unpacks it), `options` the options it is run with, before
    those of `witness`, the WitnessSpec of its witness files."""

    name: str
    module: str
    directory: str
    options: tuple[str, ...] = ()
    witness: WitnessSpec = DEFAULT_WITNESS

    def tool(self) -> BaseTool2:
        """A new object of the tool-info module's class Tool. The module is
        imported here, from the BenchExec wheel that src/cooperace/__init__.py
        puts first on sys.path, whatever the working directory is."""
        return importlib.import_module(self.module).Tool()


# The components CoOpeRace can run, by their name in the conf. A new
# component needs only an entry here.
REGISTRY = {spec.name: spec for spec in (
    # The options of Goblint's own SV-COMP entry (benchexec_toolinfo_options
    # of version svcomp26 in its fm-tools file goblint.yml). The portfolio
    # path is relative to Goblint's directory, the working directory of its
    # run. Goblint prints "SV-COMP result: ..." only when run through this
    # portfolio. Keep in step with the Goblint version in tools.txt.
    #Witness: witness.yaml.path; the svcomp26 configuration writes no graphml witness
    ComponentSpec("Goblint", "benchexec.tools.goblint", "goblint",
                  options=("--portfolio-conf", "conf/svcomp26/seq.txt"),
                  witness=WitnessSpec("run", ("--set", "witness.yaml.path", "{dir}/witness.yml"),
                                      ("witness.yml",))),
    ComponentSpec("Deagle", "benchexec.tools.deagle", "deagle"),
    #Dartagnan-SVCOMP.sh sets DAT3M_OUTPUT to output/ in its working directory,
    #which must be Dartagnan's directory, and has no option for another one
    ComponentSpec("Dartagnan", "benchexec.tools.dartagnan", "dartagnan",
                  witness=WitnessSpec("component", (), ("output/witness.graphml",))),
    ComponentSpec("ULTIMATE Automizer", "benchexec.tools.ultimateautomizer", "uautomizer",
                  options=("--full-output",), witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE GemCutter", "benchexec.tools.ultimategemcutter", "ugemcutter",
                  options=("--full-output",), witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE Taipan", "benchexec.tools.ultimatetaipan", "utaipan",
                  options=("--full-output",), witness=_ULTIMATE_WITNESS),
    ComponentSpec("nacpa", "benchexec.tools.nacpa", "nacpa"),
    ComponentSpec("CPAchecker", "benchexec.tools.cpachecker", "CPAchecker-4.0-unix"),
    # The tool-info module's name() is "SV-sanitizers", not "sv-sanitizers",
    # and run_component looks a component's entry up by name(), as the tables this
    # registry replaced did. A run of sv-sanitizers therefore raises KeyError
    # before the component starts, and is reported with status "ERROR
    # (KeyError: 'SV-sanitizers')" and result unknown.
    ComponentSpec("sv-sanitizers", "benchexec.tools.sv-sanitizers", "sv-sanitizers"),
    ComponentSpec("RacerF", "benchexec.tools.racerf", "racerf"),
)}

# The values of a task's `data_model` option (and of `--arch`) that the
# components' BenchExec tool-info modules understand. SV-COMP task definitions
# without a `data_model` are ILP32 programs, so that is what `--arch` defaults to.
DATA_MODELS = ("ILP32", "LP64")
DEFAULT_DATA_MODEL = "ILP32"

# Held while a protocol line or block is printed, so that the output of
# components running in parallel is not interleaved.
PRINT_LOCK = threading.Lock()


class ComponentRunner:
    """Runs the components of the steps of a strategy on one task: the
    StepRunner that strategy.execute is given. It holds the task (`file`,
    `property_file`, `data_model`), the components it can run (`registry`),
    the directory that holds their directories (`tools_dir`, tools/ in
    TOOL_DIR, wherever the working directory is) and, between prepare and cleanup, the work directory
    with one directory per component run (`work_dir`).

    `data_model` is the value of `--arch`: one of DATA_MODELS, or None when
    the option was not given, which means DEFAULT_DATA_MODEL (reported on
    stderr). Any other value raises ValueError: given to the components as it
    is, the Goblint and ULTIMATE tool-info modules raise
    UnsupportedFeatureException inside run_component and Dartagnan's ignores
    it. `registry` is copied, so that the unit tests can add stub components
    to the copy."""

    def __init__(self, file: str, property_file: str, data_model: str | None,
                 registry: Mapping[str, ComponentSpec] = REGISTRY):
        if data_model is None:
            print(f"CoOpeRace: no --arch given, assuming {DEFAULT_DATA_MODEL}", file=sys.stderr)
            data_model = DEFAULT_DATA_MODEL
        elif data_model not in DATA_MODELS:
            raise ValueError(f"unsupported data model {data_model!r}, expected one of {', '.join(DATA_MODELS)}")
        self.file = file
        self.property_file = os.path.abspath(property_file)
        self.data_model = data_model

        #The components this runner can run, by name
        self.registry = dict(registry)
        #The directory that holds the components' directories
        self.tools_dir = os.path.join(TOOL_DIR, "tools")
        self.work_dir = None

    def missing_executables(self, names: list[str]) -> list[str]:
        """One message for each component of `names` (conf names, keys of
        `registry`) whose tool-info module's `executable` raises
        ToolNotFoundException for the component's directory under `tools_dir`:
        the component's name and the exception's message on one line. Empty
        if every component can be found. Starts no component. Any other exception from
        making the tool-info object or finding the executable is not
        reported here: run_step reports it as an error of that step."""
        problems = []
        for name in names:
            spec = self.registry[name]
            try:
                locator = BaseTool2.ToolLocator(tool_directory=os.path.join(self.tools_dir, spec.directory))
                spec.tool().executable(locator)
            except ToolNotFoundException as error:
                problems.append(f"component {name!r}: {' '.join(str(error).split())}")
            except Exception:
                #Reported with its traceback when run_step makes the tool-info object
                pass
        return problems

    def prepare(self) -> None:
        """Removes the witness files an earlier run delivered to the working
        directory and makes the work directory, which holds one directory per
        component run, made in run_component."""
        remove_old_witness_files()
        self.work_dir = tempfile.mkdtemp(prefix="cooperace-")

    def deliver(self, witness_files: list[str]) -> None:
        """Delivers `witness_files` with witness_files_to_file_root."""
        witness_files_to_file_root(witness_files)

    def cleanup(self) -> None:
        """Removes the work directory and everything in it."""
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def run_step(self, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the config.Step `step` with run_component in the ComponentGroup
        `group` and returns the Outcome run_component returns.

        An Exception from making the tool-info object (ComponentSpec.tool,
        which imports its module) or from run_component, from setting the run up
        (for example ToolNotFoundException from the tool-info module's
        `executable`) or from the run, makes this step a step without a
        verdict: the traceback goes to stderr, the component's block is
        printed with status "ERROR (<exception class>: <message>)" and result
        "unknown", and NO_OUTCOME is returned, so that the next step of a
        sequence runs. The block names the component by the tool-info
        object's name(), or by the conf's name if there is no such object.
        StopSignal is a BaseException and propagates."""
        name = step.component
        try:
            actor = self.registry[step.component].tool()
            name = actor.name()
            return self.run_component(actor, step, group)
        except Exception as error:
            traceback.print_exc()
            print_component_run(name, subprocess.CompletedProcess(None, None, "", ""),
                                f"ERROR ({type(error).__name__}: {error})", "unknown")
            return NO_OUTCOME

    def run_component(self, actor: BaseTool2, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the component `actor` of the config.Step `step` on the task in
        the ComponentGroup `group`, with the step's limits, and prints its
        block. Returns the Outcome of its verdict, with the witness files it
        wrote during this run, if `step.accept` accepts the verdict and `group`
        was not stopped, and NO_OUTCOME otherwise.

        The component's entry in the registry is looked up by `actor.name()`;
        see the entry of sv-sanitizers in REGISTRY."""
        spec = self.registry[actor.name()]
        tool_location = os.path.join(self.tools_dir, spec.directory)

        tool_locator = BaseTool2.ToolLocator(tool_directory=tool_location)
        executable = actor.executable(tool_locator)

        cwd = os.path.dirname(executable)

        task = BaseTool2.Task.with_files(
            input_files=[self.file],
            property_file=self.property_file,
            options={"data_model": self.data_model,
                     "language": "C"},
        )

        #A directory of this run, in which the component's witnesses end up
        witness_dir = tempfile.mkdtemp(prefix=actor.name().replace(" ", "_") + "-", dir=self.work_dir)
        options = list(spec.options) + witness_options(spec.witness, witness_dir)

        cmdline = actor.cmdline(
            executable,
            options,
            task,
            BaseTool2.ResourceLimits()
        )

        started = start_time(witness_dir)
        tool_result = run_in_session(
            command=with_resource_limits(step, cmdline),
            cwd=cwd,
            group=group
            )
        #Also for a stopped component, so that no file it wrote stays in its directory
        witness_files = collect_witness_files(spec.witness, cwd, witness_dir, started)

        if group.stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used.
            #This check is also why component_status never gets a returncode of
            #None: run_in_session returns None only when the group was already
            #stopped, and a group never becomes unstopped.
            print_component_run(actor.name(), tool_result, "stopped by CoOpeRace", None)
            return NO_OUTCOME

        status = component_status(actor, cmdline, tool_result)
        verdict = status.lower()

        if confirm_verdict(step.accept, verdict, "true"):
            verdict = "true"
        elif confirm_verdict(step.accept, verdict, "false"):
            verdict = "false"
        else:
            verdict = "unknown"

        print_component_run(actor.name(), tool_result, status, verdict)

        if verdict == "unknown":
            return NO_OUTCOME
        return Outcome(verdict, actor.name(), witness_files)


def component_memory_limit(step: Step) -> int | None:
    """Memory limit in bytes for the config.Step `step`, from its
    `memory_limit` (the conf's `memoryLimits`), or None.

    `memoryLimits` maps a component name to a number of bytes or to a
    percentage of the run's memory limit (`"70%"`, see run_memory_limit).
    A percentage gives None when the run has no memory limit.
    """
    value = step.memory_limit
    if isinstance(value, str) and value.endswith("%"):
        run_limit = run_memory_limit()
        if run_limit is None:
            return None
        return int(run_limit * float(value[:-1]) / 100)
    return None if value is None else int(value)


def component_cpu_time_limit(step: Step) -> int | None:
    """CPU-time limit in seconds for the config.Step `step`, from its
    `cpu_time_limit` (the conf's `cpuTimeLimits`), or None."""
    value = step.cpu_time_limit
    return None if value is None else int(value)


def with_resource_limits(step: Step, command: list[str]) -> list[str]:
    """`command`, started with the limits of the config.Step `step`
    (processes.with_rlimits): RLIMIT_DATA set to its memory limit
    (component_memory_limit) and RLIMIT_CPU to its CPU-time limit
    (component_cpu_time_limit). Prints "Memory limit of <component>: <n>
    bytes (RLIMIT_DATA)" and "CPU-time limit of <component>: <n> s
    (RLIMIT_CPU)" for the limits it sets. A percentage for which the run
    has no memory limit applies no limit and prints "Memory limit of
    <component>: none (no cgroup memory limit found for "<value>")"."""
    tool_name = step.component
    memory = component_memory_limit(step)
    if memory is not None:
        message = f"Memory limit of {tool_name}: {memory} bytes (RLIMIT_DATA)"
        with PRINT_LOCK:
            print(message, flush=True)
    else:
        value = step.memory_limit
        if isinstance(value, str) and value.endswith("%"):
            #A percentage that run_memory_limit could not resolve: no
            #limit applies, and the run says so
            with PRINT_LOCK:
                print(f"Memory limit of {tool_name}: none "
                      f"(no cgroup memory limit found for \"{value}\")", flush=True)
    cpu = component_cpu_time_limit(step)
    if cpu is not None:
        with PRINT_LOCK:
            print(f"CPU-time limit of {tool_name}: {cpu} s (RLIMIT_CPU)", flush=True)
    return with_rlimits(command, memory, cpu)


def component_status(actor: BaseTool2, cmdline: list[str],
                     tool_result: subprocess.CompletedProcess) -> str:
    """The status BenchExec would give this run of `actor` (a
    subprocess.CompletedProcess from run_in_session): `actor.determine_result`
    on the output and the real exit code, and for an unspecific result
    (unknown, error or done) the refinement of benchexec.model, which
    names the signal that ended the component or, for an error, the exit
    code. A component that crashes is so reported as, for example,
    "EXCEPTION (SetDomain.Unsupported)" or "ERROR (1)", which
    confirm_verdict does not accept."""
    returncode = tool_result.returncode
    if returncode < 0:
        exit_code = butil.ProcessExitCode.create(signal=-returncode)
    else:
        exit_code = butil.ProcessExitCode.create(value=returncode)
    run = BaseTool2.Run(
        cmdline=cmdline,
        exit_code=exit_code,
        output=BaseTool2.RunOutput(tool_result.stdout.strip().split("\n")),
        termination_reason=""
    )
    status = actor.determine_result(run)

    if status in bresult.RESULT_LIST_OTHER:
        if exit_code.signal == signal.SIGABRT:
            status = "ABORTED"
        elif exit_code.signal == signal.SIGSEGV:
            status = "SEGMENTATION FAULT"
        elif exit_code.signal == signal.SIGTERM:
            status = "KILLED"
        elif exit_code.signal:
            status = f"KILLED BY SIGNAL {exit_code.signal}"
        elif exit_code.value and status != bresult.RESULT_UNKNOWN:
            status = f"{bresult.RESULT_ERROR} ({exit_code.value})"
    return status


def confirm_verdict(tool_acceptance_criteria: str, verdict: str, expected_verdict: str) -> bool | None:
    """Whether `verdict` (a component's status in lower case) is
    `expected_verdict` ("true" or "false") for an acceptance
    `tool_acceptance_criteria` ("true", "false" or "all") that accepts it.
    `verdict` matches if it contains `expected_verdict`, as
    "false(no-data-race)" contains "false". Returns None, which is false,
    if it does not."""
    if verdict.__contains__(expected_verdict):
        if tool_acceptance_criteria == "all" or tool_acceptance_criteria == expected_verdict:
            return True
        else:
            return False


def print_component_run(name: str, tool_result: subprocess.CompletedProcess, status: str,
                        verdict: str | None) -> None:
    """Prints the output of a component run between "---<name> logs---"
    and "---end of <name> logs---", then the line "Tool name: <name>
    Status: <status> Exit code: <exit code>" and, unless `verdict` is None,
    "Tool name: <name> Result: <verdict>", all at once, so that the
    output of components running in parallel is not interleaved."""
    returncode = tool_result.returncode
    if returncode is None:
        exit_code = "none, not started"
    elif returncode < 0:
        exit_code = f"signal {-returncode}"
    else:
        exit_code = str(returncode)
    lines = [f"---{name} logs---", tool_result.stdout.rstrip("\n"), f"---end of {name} logs---",
             f"Tool name: {name} Status: {status} Exit code: {exit_code}"]
    if verdict is not None:
        lines.append(f"Tool name: {name} Result: {verdict}")
    with PRINT_LOCK:
        print("\n".join(lines), flush=True)


def witness_options(witness: WitnessSpec, witness_dir: str) -> list[str]:
    """The `options` of the WitnessSpec `witness`, with {dir} replaced by
    `witness_dir`."""
    return [option.replace("{dir}", witness_dir) for option in witness.options]


def start_time(witness_dir: str) -> int:
    """A time stamp of now, taken from the file system as the modification
    time of a new file in `witness_dir`, so that it compares exactly with
    the modification times of the files a component writes afterwards."""
    marker = os.path.join(witness_dir, ".started")
    open(marker, "w").close()
    return os.stat(marker).st_mtime_ns


def collect_witness_files(witness: WitnessSpec, cwd: str, witness_dir: str, started: int) -> list[str]:
    """Returns the witness files of the component run that began at
    `started`, all in `witness_dir`: the files that the component's
    WitnessSpec `witness` names, in `witness_dir` or under its own
    directory `cwd`, that were modified at or after `started`. Those under
    `cwd` are moved to the same relative path in `witness_dir`. Any other
    file, one left by an earlier run or shipped with the component
    (Goblint's smoketests/*witness*.yml), is neither returned nor
    touched.

    The comparison of modification times assumes that `witness_dir` (where
    `started` was taken, by start_time) and the directory a component
    writes to take their modification times from the same clock at the
    same granularity, which holds on Linux for ext4, tmpfs and overlayfs;
    on a file system with one-second time stamps, a witness written in the
    second of `started` would be dropped."""
    source = witness_dir if witness.directory == "run" else cwd
    if witness.files is None:
        candidates = witness_files_under(source)
    else:
        candidates = [os.path.join(source, file) for file in witness.files]

    collected = []
    for file in candidates:
        try:
            if os.stat(file).st_mtime_ns < started:
                continue
        except OSError:
            continue
        if source != witness_dir:
            destination = os.path.join(witness_dir, os.path.relpath(file, source))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.move(file, destination)
            file = destination
        collected.append(file)
    return collected


def witness_files_under(tool_dir: str) -> list[str]:
    """The files under `tool_dir` whose name contains "witness" in any case
    and ends in graphml or yml."""
    witness_files = []

    for root, _dirs, files in os.walk(tool_dir):
        for file in files:
            if 'witness' in file.lower() and (file.endswith("graphml") or file.endswith("yml")):
                witness_files.append(os.path.join(root, file))

    return witness_files


def witness_files_to_file_root(witness_files: list[str]) -> None:
    """Copies the witness files of one component run, from
    collect_witness_files, to the working directory under the name of their
    format: a graphml file as witness.graphml, a YAML file as witness.yml."""
    for file in witness_files:
        if file.endswith(".graphml"):
            name = "witness.graphml"
        elif file.endswith(".yml") or file.endswith(".yaml"):
            name = "witness.yml"
        else:
            continue
        shutil.copy2(file, os.path.join(os.getcwd(), name))


def remove_old_witness_files() -> None:
    """Removes witness.graphml and witness.yml, the names
    witness_files_to_file_root delivers witnesses under, from the working
    directory, so that one left there by an earlier run is not delivered
    with this run's verdict."""
    for name in ("witness.graphml", "witness.yml"):
        try:
            os.remove(os.path.join(os.getcwd(), name))
        except FileNotFoundError:
            pass
