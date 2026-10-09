# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The components CoOpeRace runs (REGISTRY) and how one is run: its command
line, working directory and environment from its BenchExec tool-info module
(ComponentRunner.command), its limits, its status and verdict, its witness
files, and the block of protocol lines it prints (print_component_run). ComponentRunner is the strategy.StepRunner the
strategy runs steps with. The only module of the package that imports
BenchExec.

The standard output is read by programs, so its lines are a protocol: for
each component run, "---<name> logs---", its output, "---end of <name>
logs---", "Tool name: <name> Status: <status> Exit code: <code>" and, unless
CoOpeRace stopped it, "Tool name: <name> Result: <verdict>"; "Memory limit
of <name>: ..." and "CPU-time limit of <name>: ..." before a component with
a limit starts. strategy.execute adds "CoOpeRace result from: <name>" and
"CoOpeRace stopped by signal <n>", cli "CoOpeRace verdict: <result>",
where <result> is "unknown" or reported_result of the returned status.
A component that cannot be run or crashes is reported in its block and is a
step without a verdict (run_step). A component that is not installed, or
whose installed version is not the one whose options tools-options.json
holds, is found out before any component starts
(ComponentRunner.installation_problems), and cli ends CoOpeRace for it with
an error on stderr and no verdict line.

What a component is run with comes from two places. REGISTRY, here, holds
what is CoOpeRace's own: the component's name in the conf, its tool-info
module, its fm-tools name (its directory under tools/) and its WitnessSpec.
tools-options.json in TOOL_DIR holds what comes from the component's fm-tools
entry: the DOI it is installed from and the `benchexec_toolinfo_options` of
that version, which scripts/download-tools.py writes when it installs the
components of tools.txt and tools-pool.txt (read_component_versions). The
options are read from that file, never from fm-tools while CoOpeRace runs."""
from __future__ import annotations

import importlib
import json
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

from benchexec import model as bmodel
from benchexec import result as bresult
from benchexec import util as butil
from benchexec.tools.template import BaseTool2, ToolNotFoundException

from . import TOOL_DIR
from .config import Step
from .processes import ComponentGroup, run_in_session, run_memory_limit, with_rlimits
from .properties import FORMULAS, NO_DATA_RACE, NO_OVERFLOW, UNREACH_CALL, VALID_MEMSAFETY
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
    first run, `directory` its name in fm-tools, which is its directory under
    tools/ (where download-tools.py unpacks it) and its key in
    tools-options.json, and `witness` the WitnessSpec of its witness files.
    The options it is run with are those of its ComponentVersion, before
    those of `witness`."""

    name: str
    module: str
    directory: str
    witness: WitnessSpec = DEFAULT_WITNESS

    def tool(self) -> BaseTool2:
        """A new object of the tool-info module's class Tool. The module is
        imported here, from the BenchExec wheel that src/cooperace/__init__.py
        puts first on sys.path, whatever the working directory is."""
        return importlib.import_module(self.module).Tool()


# The components CoOpeRace can run, by their name in the conf. A new
# component needs an entry here and a line in tools.txt (the components of
# the SV-COMP archive) or tools-pool.txt (the others), from which
# download-tools.py writes its options into tools-options.json.
REGISTRY = {spec.name: spec for spec in (
    # Goblint prints "SV-COMP result: ..." only when run through the portfolio
    # its fm-tools options name (--portfolio-conf conf/svcomp26/seq.txt, a path
    # relative to Goblint's directory, the working directory of its run).
    #Witness: witness.yaml.path; the svcomp26 configuration writes no graphml witness
    ComponentSpec("Goblint", "benchexec.tools.goblint", "goblint",
                  witness=WitnessSpec("run", ("--set", "witness.yaml.path", "{dir}/witness.yml"),
                                      ("witness.yml",))),
    ComponentSpec("Deagle", "benchexec.tools.deagle", "deagle"),
    #Dartagnan-SVCOMP.sh sets DAT3M_OUTPUT to output/ in its working directory,
    #which must be Dartagnan's directory, and has no option for another one
    ComponentSpec("Dartagnan", "benchexec.tools.dartagnan", "dartagnan",
                  witness=WitnessSpec("component", (), ("output/witness.graphml",))),
    ComponentSpec("ULTIMATE Automizer", "benchexec.tools.ultimateautomizer", "uautomizer",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE GemCutter", "benchexec.tools.ultimategemcutter", "ugemcutter",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE Taipan", "benchexec.tools.ultimatetaipan", "utaipan",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("nacpa", "benchexec.tools.nacpa", "nacpa"),
    ComponentSpec("CPAchecker", "benchexec.tools.cpachecker", "cpachecker"),
    ComponentSpec("sv-sanitizers", "benchexec.tools.sv-sanitizers", "sv-sanitizers"),
    ComponentSpec("RacerF", "benchexec.tools.racerf", "racerf"),
)}

# The file in TOOL_DIR that holds the ComponentVersion of every component of
# tools.txt and tools-pool.txt, written by scripts/download-tools.py.
OPTIONS_FILE = "tools-options.json"

# The file in a component's directory that records the DOI it was installed
# from, written by scripts/download-tools.py after the installation.
DOI_RECORD = ".doi"


@dataclass(frozen=True)
class ComponentVersion:
    """The version of a component that tools-options.json records: `doi`, the
    DOI of the archive it is installed from, `version`, the name of the
    version of its fm-tools entry with that DOI, and `options`, the
    `benchexec_toolinfo_options` of that version, which the component is run
    with."""

    doi: str
    version: str
    options: tuple[str, ...]


class OptionsFileError(Exception):
    """Raised by read_component_versions for a tools-options.json that cannot
    be read or does not have the format download-tools.py writes; its message
    names the file and the problem."""


def read_component_versions(path: str) -> dict[str, ComponentVersion]:
    """The ComponentVersion of each component of the options file `path`, by
    its fm-tools name (a ComponentSpec's `directory`). The file is a JSON
    object with one object per component, holding "doi" and "version" (strings)
    and "options" (a list of strings); further keys are ignored. Raises
    OptionsFileError for a file that cannot be read or has another format."""
    try:
        with open(path) as file:
            data = json.load(file)
    except (OSError, ValueError) as error:
        raise OptionsFileError(f"cannot read the options of the components {path}: {error}") from error
    if not isinstance(data, dict):
        raise OptionsFileError(f"{path}: expected a JSON object, not {type(data).__name__}")
    versions = {}
    for name, record in data.items():
        if not (isinstance(record, dict) and isinstance(record.get("doi"), str)
                and isinstance(record.get("version"), str) and isinstance(record.get("options"), list)
                and all(isinstance(option, str) for option in record["options"])):
            raise OptionsFileError(f"{path}: the entry of {name} is not an object with a \"doi\", "
                                   f"a \"version\" and a list of strings \"options\": {record!r}")
        versions[name] = ComponentVersion(record["doi"], record["version"], tuple(record["options"]))
    return versions


def installed_doi(tool_location: str) -> str | None:
    """The DOI recorded in the component directory `tool_location` (its
    DOI_RECORD), or None if there is no record."""
    try:
        with open(os.path.join(tool_location, DOI_RECORD)) as file:
            return file.read().strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class ComponentCommand:
    """How a component run is started: the command line `cmdline`, the
    working directory `cwd`, and the environment `env`, or None for the
    environment of CoOpeRace itself."""

    cmdline: list[str]
    cwd: str
    env: dict[str, str] | None


def run_environment(environments: Mapping[str, Mapping[str, str]]) -> dict[str, str] | None:
    """The environment of a component run, given `environments`, the result of
    its tool-info module's `environment(executable)`, built as BenchExec's
    RunExecutor._setup_environment builds it: with "keepEnv", only the
    variables it names are kept from CoOpeRace's environment, else all are;
    "newEnv" sets variables and "additionalEnv" appends to them. Returns None,
    CoOpeRace's own environment, for an empty `environments`, which is what
    BaseTool2.environment returns and what every module of REGISTRY returns
    in BenchExec 3.31. What RunExecutor sets for every run (HOME, TMPDIR and
    the like) is not set here: CoOpeRace's own run already has them."""
    if not any(environments.values()) and environments.get("keepEnv") is None:
        return None
    if environments.get("keepEnv") is not None:
        env = {key: os.environ[key] for key in environments["keepEnv"] if key in os.environ}
    else:
        env = dict(os.environ)
    env.update(environments.get("newEnv", {}))
    for key, value in environments.get("additionalEnv", {}).items():
        env[key] = os.environ.get(key, "") + value
    return env


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
    `property_file`, `data_model`, and `property_name`, the name of the
    property in `property_file`, a key of properties.FORMULAS, which cli
    recognizes; no-data-race if not given), the components it can run
    (`registry`), their versions and options (`versions`, by fm-tools name),
    the directory that holds their directories (`tools_dir`, tools/ in
    TOOL_DIR, wherever the working directory is) and, between prepare and
    cleanup, the work directory with one directory per component run
    (`work_dir`).

    `data_model` is the value of `--arch`: one of DATA_MODELS, or None when
    the option was not given, which means DEFAULT_DATA_MODEL (reported on
    stderr). Any other value raises ValueError: given to the components as it
    is, the Goblint and ULTIMATE tool-info modules raise
    UnsupportedFeatureException inside run_component and Dartagnan's ignores
    it. A `property_name` that is not a key of properties.FORMULAS raises
    ValueError. `registry` is copied, so that the unit tests can add stub
    components to the copy. `versions` is copied too; None reads them from
    OPTIONS_FILE in TOOL_DIR (read_component_versions, which raises
    OptionsFileError)."""

    def __init__(self, file: str, property_file: str, data_model: str | None,
                 registry: Mapping[str, ComponentSpec] = REGISTRY,
                 versions: Mapping[str, ComponentVersion] | None = None,
                 property_name: str = NO_DATA_RACE):
        if property_name not in FORMULAS:
            raise ValueError(f"unknown property {property_name!r}, expected one of {', '.join(FORMULAS)}")
        if data_model is None:
            print(f"CoOpeRace: no --arch given, assuming {DEFAULT_DATA_MODEL}", file=sys.stderr)
            data_model = DEFAULT_DATA_MODEL
        elif data_model not in DATA_MODELS:
            raise ValueError(f"unsupported data model {data_model!r}, expected one of {', '.join(DATA_MODELS)}")
        self.file = file
        self.property_file = os.path.abspath(property_file)
        self.property_name = property_name
        self.data_model = data_model

        #The components this runner can run, by name
        self.registry = dict(registry)
        #The installed version and the options of each component, by fm-tools name
        if versions is None:
            versions = read_component_versions(os.path.join(TOOL_DIR, OPTIONS_FILE))
        self.versions = dict(versions)
        #The directory that holds the components' directories
        self.tools_dir = os.path.join(TOOL_DIR, "tools")
        self.work_dir = None

    def installation_problems(self, names: list[str]) -> list[str]:
        """One message for each component of `names` (conf names, keys of
        `registry`) that cannot be run as installed, each on one line naming
        the component: its tool-info module's `executable` raises
        ToolNotFoundException for the component's directory under `tools_dir`;
        or `versions` has no ComponentVersion for it; or the DOI recorded in
        its directory (installed_doi) is not that ComponentVersion's, so that
        its options might be those of another version. Empty if every
        component can be run. Starts no component. Any other exception from
        making the tool-info object or finding the executable is not
        reported here: run_step reports it as an error of that step."""
        problems = []
        for name in names:
            spec = self.registry[name]
            tool_location = os.path.join(self.tools_dir, spec.directory)
            try:
                spec.tool().executable(BaseTool2.ToolLocator(tool_directory=tool_location))
            except ToolNotFoundException as error:
                problems.append(f"component {name!r}: {' '.join(str(error).split())}")
                continue
            except Exception:
                #Reported with its traceback when run_step makes the tool-info object
                continue
            version = self.versions.get(spec.directory)
            if version is None:
                problems.append(f"component {name!r}: {OPTIONS_FILE} has no options for {spec.directory}; "
                                "run scripts/download-tools.py")
                continue
            recorded = installed_doi(tool_location)
            if recorded != version.doi:
                problems.append(f"component {name!r}: {tool_location} is installed from "
                                f"{recorded or 'an unrecorded DOI (no ' + DOI_RECORD + ')'}, but {OPTIONS_FILE} "
                                f"holds the options of {version.doi}; run scripts/download-tools.py")
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

    def command(self, actor: BaseTool2, spec: ComponentSpec, witness_dir: str) -> ComponentCommand:
        """The ComponentCommand that starts the component `actor`, the
        tool-info object of the registry entry `spec`, on the task, with the
        witness directory `witness_dir` of its run, as BenchExec would start it
        from the component's directory under `tools_dir`, its tool directory:

        - the command line is benchexec.model.cmdline_for_run's, given the
          executable that `actor.executable` finds in the tool directory,
          the options of the component's ComponentVersion followed by those
          of its WitnessSpec, the task file, the property file, the task
          options data_model and language, and an empty ResourceLimits (the
          tool-info module for CoOpeRace passes CoOpeRace no limits).
          cmdline_for_run makes a relative path relative to the working
          directory, and expands environment variables and ~ in every
          argument; the executable, the task and the property file are
          absolute paths here, so they are passed as they are;
        - the working directory is `actor.working_directory(executable)`,
          relative to the tool directory, as BenchExec's is relative to the
          directory it runs in, which SV-COMP makes the tool's directory. The
          default, os.curdir, which every module of REGISTRY keeps in
          BenchExec 3.31, so gives the tool directory;
        - the environment is run_environment of `actor.environment(executable)`.

        Raises what `actor.executable` raises, ToolNotFoundException if the
        executable is not there, and KeyError if `versions` has no entry for
        the component."""
        version = self.versions[spec.directory]
        tool_location = os.path.join(self.tools_dir, spec.directory)
        executable = actor.executable(BaseTool2.ToolLocator(tool_directory=tool_location))
        options = list(version.options) + witness_options(spec.witness, witness_dir)
        cmdline = bmodel.cmdline_for_run(
            actor, executable, options, [self.file], None, self.property_file,
            {"data_model": self.data_model, "language": "C"}, BaseTool2.ResourceLimits())
        cwd = os.path.normpath(os.path.join(tool_location, actor.working_directory(executable)))
        return ComponentCommand(cmdline, cwd, run_environment(actor.environment(executable)))

    def run_component(self, actor: BaseTool2, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the component `actor` of the config.Step `step` on the task in
        the ComponentGroup `group`, with the step's limits, and prints its
        block. Returns the Outcome of its verdict, with the witness files it
        wrote during this run, if `step.accept` accepts the verdict and `group`
        was not stopped, and NO_OUTCOME otherwise.

        The component's entry in the registry is looked up by the conf's name
        of the component, `step.component`, which config.load has checked is
        in the registry, and its options in `versions`, which
        installation_problems has checked hold it; the case of `actor.name()`
        (BenchExec's sv-sanitizers module names itself "SV-sanitizers") does
        not matter. It is started as `command` says. The block is printed
        under `actor.name()`."""
        spec = self.registry[step.component]
        #A directory of this run, in which the component's witnesses end up
        witness_dir = tempfile.mkdtemp(prefix=actor.name().replace(" ", "_") + "-", dir=self.work_dir)
        command = self.command(actor, spec, witness_dir)

        started = start_time(witness_dir)
        tool_result = run_in_session(
            command=with_resource_limits(step, command.cmdline),
            cwd=command.cwd,
            group=group,
            env=command.env,
            )
        #Also for a stopped component, so that no file it wrote stays in its directory
        witness_files = collect_witness_files(spec.witness, command.cwd, witness_dir, started)

        if group.stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used.
            #This check is also why component_status never gets a returncode of
            #None: run_in_session returns None only when the group was already
            #stopped, and a group never becomes unstopped.
            print_component_run(actor.name(), tool_result, "stopped by CoOpeRace", None)
            return NO_OUTCOME

        status = component_status(actor, command.cmdline, tool_result, component_cpu_time_limit(step))

        if confirm_verdict(step.accept, status, "true", self.property_name):
            verdict = "true"
        elif confirm_verdict(step.accept, status, "false", self.property_name):
            verdict = "false"
        else:
            verdict = "unknown"

        print_component_run(actor.name(), tool_result, status, verdict)

        if verdict == "unknown":
            return NO_OUTCOME
        return Outcome(verdict, actor.name(), witness_files, reported_result(self.property_name, status))


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


def component_status(actor: BaseTool2, cmdline: list[str], tool_result: subprocess.CompletedProcess,
                     cpu_time_limit: int | None = None) -> str:
    """The status BenchExec would give this run of `actor` (a
    processes.FinishedProcess from run_in_session, or a
    subprocess.CompletedProcess, which has no CPU time) under the CPU-time
    limit `cpu_time_limit` in seconds that CoOpeRace set (None: none).

    The status is `actor.determine_result` on a Run as
    benchexec.model.Run.set_result makes it, with the output's lines as
    output_lines splits them, the real exit code and no termination reason
    (None: BenchExec did not end the run). An unspecific result (unknown,
    error or done) is then refined as benchexec.model.Run._analyze_result
    (BenchExec 3.31) refines it, which this copies: "TIMEOUT" if the CPU-time
    limit ended the component (ended_by_cpu_time_limit), else "ABORTED",
    "SEGMENTATION FAULT", "KILLED" or "KILLED BY SIGNAL <n>" for the signal
    that ended it, or "ERROR (<code>)" for an error with a non-zero exit code.
    A component that crashes is so reported as, for example,
    "EXCEPTION (SetDomain.Unsupported)" or "ERROR (1)", which confirm_verdict
    does not accept.

    Where this differs from BenchExec: BenchExec gives TIMEOUT whenever the
    run's CPU time exceeded its limit, and writes a specific result found
    after that as "TIMEOUT (true)"; here a specific result is kept as the
    module returns it, so a verdict a component printed before its limit
    ended it is still accepted, as it was before TIMEOUT was reported."""
    returncode = tool_result.returncode
    if returncode < 0:
        exit_code = butil.ProcessExitCode.create(signal=-returncode)
    else:
        exit_code = butil.ProcessExitCode.create(value=returncode)
    run = BaseTool2.Run(
        cmdline=cmdline,
        exit_code=exit_code,
        output=BaseTool2.RunOutput(output_lines(tool_result.stdout)),
        termination_reason=None
    )
    status = actor.determine_result(run)

    if status in bresult.RESULT_LIST_OTHER:
        if ended_by_cpu_time_limit(exit_code.signal, getattr(tool_result, "cpu_time", None), cpu_time_limit):
            status = bresult.RESULT_TIMEOUT
        elif exit_code.signal == signal.SIGABRT:
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


def ended_by_cpu_time_limit(signal_number: int | None, cpu_time: float | None,
                            cpu_time_limit: int | None) -> bool:
    """Whether the RLIMIT_CPU that with_rlimits sets (soft limit
    `cpu_time_limit` seconds, hard limit one second more) ended the
    component's process, which ended by the signal `signal_number` (None: it
    exited) after `cpu_time` seconds of CPU time (FinishedProcess.cpu_time;
    None: not known): it ended by SIGXCPU, which only that limit sends, or by
    SIGKILL after more CPU time than `cpu_time_limit`, which is how the kernel
    ends a process at the hard limit (BenchExec's _is_timeout also compares the
    CPU time with the limit, and its own limit ends a run by SIGKILL).

    Only the component's own process is judged. A descendant that the limit
    ends, such as a level of Goblint's portfolio runner (goblint exited with
    code -24), makes no TIMEOUT when the process that started it exits by
    itself, as goblint_runner.py does with exit code 0: its status is what
    its tool-info module reads from the output. Adding up the CPU time of the
    descendants would not tell this apart either: RLIMIT_CPU limits each
    process on its own, so levels that each end by themselves can together use
    more than the limit."""
    if signal_number == signal.SIGXCPU:
        return True
    return (signal_number == signal.SIGKILL and cpu_time is not None and cpu_time_limit is not None
            and cpu_time > cpu_time_limit)


def output_lines(output: str) -> list[str]:
    r"""The lines of a component's output `output`, each with its line
    separator, as benchexec.model.Run.set_result reads them from the run's log
    with readlines() for the RunOutput it gives the tool-info module: split
    after each "\n" only, and a last line without one kept as it is. Leading
    and trailing blank lines are kept. RunOutput gives a module each line
    without its separator. run_in_session reads the output in text mode,
    which turns "\r\n" and "\r" into "\n", as BenchExec's reading of the log
    does."""
    lines = output.split("\n")
    return [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


#The statuses of a component, as BenchExec's tool-info modules return them, that
#are a component's verdict "true" or "false" on each property (a key of
#properties.FORMULAS). Any other status, among them a violation of another
#property such as "false(unreach-call)" on a no-data-race task, is no verdict.
#BenchExec's RESULT_FALSE_PROP, plain "false", is what Dartagnan's tool-info
#module (benchexec/tools/dartagnan.py, the same in BenchExec 3.31 to 3.35)
#returns for a FAIL without a line that names the violation. BenchExec's
#get_result_category scores it as the property's "false" on a task whose
#expected verdict has no sub-property, but on a valid-memsafety task, whose
#expected "false" names one (valid-deref, valid-free or valid-memtrack), it
#is "result does not match property" (0 points) where the expected verdict
#is false, and wrong (-16) where it is true; so for valid-memsafety only the
#three statuses with a sub-property are a "false".
ACCEPTED_STATUSES = {
    UNREACH_CALL: {"true": (bresult.RESULT_TRUE_PROP,),
                   "false": (bresult.RESULT_FALSE_REACH, bresult.RESULT_FALSE_PROP)},
    NO_OVERFLOW: {"true": (bresult.RESULT_TRUE_PROP,),
                  "false": (bresult.RESULT_FALSE_OVERFLOW, bresult.RESULT_FALSE_PROP)},
    VALID_MEMSAFETY: {"true": (bresult.RESULT_TRUE_PROP,),
                      "false": (bresult.RESULT_FALSE_DEREF, bresult.RESULT_FALSE_FREE,
                                bresult.RESULT_FALSE_MEMTRACK)},
    NO_DATA_RACE: {"true": (bresult.RESULT_TRUE_PROP,),
                   "false": (bresult.RESULT_FALSE_DATARACE, bresult.RESULT_FALSE_PROP)},
}


def confirm_verdict(tool_acceptance_criteria: str, status: str, expected_verdict: str,
                    property_name: str) -> bool:
    """Whether the status `status` of a component run (from component_status)
    is the verdict `expected_verdict` ("true" or "false") on the property
    `property_name` (a key of ACCEPTED_STATUSES) and the acceptance
    `tool_acceptance_criteria` ("true", "false" or "all") accepts that verdict.
    `status` is the verdict if it is one of
    ACCEPTED_STATUSES[property_name][expected_verdict], compared as it is,
    including the case; any other status, such as "false(unreach-call)" on
    another property, "unknown", "TIMEOUT" or "ERROR (1)", is not."""
    if status not in ACCEPTED_STATUSES[property_name].get(expected_verdict, ()):
        return False
    return tool_acceptance_criteria in ("all", expected_verdict)


def reported_result(property_name: str, status: str) -> str:
    """What CoOpeRace prints after "CoOpeRace verdict: " for a status `status`
    of a component that confirm_verdict accepted on the property
    `property_name`. For no-data-race it is "true" or "false", the line
    BenchExec's tool-info module for CoOpeRace (BenchExec 3.31 to 3.35) maps to
    "true" and "false(no-data-race)", for both accepted "false" statuses.
    For another property it is `status`, the result BenchExec gives the
    component alone, except that a plain "false" (accepted only for a
    property without sub-properties) becomes "false(<property_name>)", which
    BenchExec scores the same on a task of that property and which names the
    property in the result."""
    if property_name == NO_DATA_RACE:
        return "true" if status == bresult.RESULT_TRUE_PROP else "false"
    if status == bresult.RESULT_FALSE_PROP:
        return f"{bresult.RESULT_FALSE_PROP}({property_name})"
    return status


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
