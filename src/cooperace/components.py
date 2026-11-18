# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The components CoOpeRace runs (REGISTRY) and how one is run: its command
line, working directory and environment from its BenchExec tool-info module
(ComponentRunner.command), its limits, its status and verdict, its witness
files, and the block of protocol lines it prints (print_component_run).
ComponentRunner is the strategy.StepRunner the strategy runs steps with; it
runs each component under BenchExec's RunExecutor (execution.py). With
execution.py (and the tool-info modules of toolinfo/), the only module of the
package that imports BenchExec.

The standard output is read by programs, so its lines are a protocol: for
each component run, "---<name> logs---", its output, "---end of <name>
logs---", "Tool name: <name> Status: <status> Exit code: <code>", unless CoOpeRace
stopped it "Tool name: <name> Result: <verdict>", and for a component that
started "Tool name: <name> Measured: ..."; "Memory limit of <name>: ..." and
"CPU-time limit of <name>: ..." before a component with a limit starts, and
"Task limit of <name>: ..." before every component starts.
strategy.run_strategy adds a line "CoOpeRace answer: ..." for each verdict
and lines "CoOpeRace: ..." about wall-time limits, overridden answers and a
missing witness, strategy.execute "CoOpeRace result from: <name>" and
"CoOpeRace stopped by signal <n>", cli "CoOpeRace verdict: <result>", where
<result> is "unknown", reported_result of the status of the component the
verdict is taken from, or "ERROR (contradiction: ...)".
A component that cannot be run or crashes is reported in its block and is a
step without a verdict (run_step). A component that is not installed, or
whose installed version is not the one whose options tools-options.json
holds, is found out before any component starts
(ComponentRunner.installation_problems), and cli ends CoOpeRace for it with
an error on stderr and no verdict line.

What a component is run with comes from two places. REGISTRY, here, holds
what is CoOpeRace's own: the component's name in the conf, its tool-info
module, its fm-tools name (its directory under tools/), its WitnessSpec and
its task limit.
tools-options.json in TOOL_DIR holds what comes from the component's fm-tools
entry: the DOI it is installed from and the `benchexec_toolinfo_options` of
that version, which scripts/download-tools.py writes when it installs the
components of tools.txt and tools-pool.txt (read_component_versions). The
options are read from that file, never from fm-tools while CoOpeRace runs."""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from benchexec import model as bmodel
from benchexec import result as bresult
from benchexec import util as butil
from benchexec.tools.template import BaseTool2, ToolNotFoundException

from . import TOOL_DIR
from .config import Step
from .processes import PRINT_LOCK, ComponentGroup, run_cpu_seconds, run_memory_limit
from .properties import FORMULAS, NO_DATA_RACE, NO_OVERFLOW, UNREACH_CALL, VALID_MEMSAFETY
from .strategy import NO_OUTCOME, Outcome
from .witnesses import DELIVERED_NAME, needs_witness, passing_witness, subproperty_of


@dataclass(frozen=True)
class WitnessSpec:
    """Which files of a component run are its witness. `directory` is "run" if
    `options` tell the component to write them into the directory run_component
    makes for the run ({dir} in an option stands for it), and "component" if
    the component writes them under its own directory, the working directory
    of its run. `files` are their paths relative to that directory, empty
    for a component that writes no witness that can count, or None for
    every file there whose name contains "witness" and ends in yml
    (witness_files_under). ComponentRunner.run_component takes, of those the
    component wrote during the run, the one that witnesses.passing_witness
    accepts for its verdict, and witness_files_to_file_root delivers it as
    witness.yml. A component that writes a new witness file needs only a new
    WitnessSpec in its registry entry. GraphML files are not named: no GraphML
    witness counts (witnesses.py)."""

    directory: str
    options: tuple[str, ...] = ()
    files: tuple[str, ...] | None = None


#For a component without a WitnessSpec of its own: the name pattern, under its own directory
DEFAULT_WITNESS = WitnessSpec("component")

#Ultimate.py --witness-dir, into which it writes witness.yml (and witness.graphml, never delivered)
_ULTIMATE_WITNESS = WitnessSpec("run", ("--witness-dir", "{dir}"), ("witness.yml",))


# The task limit of a component (ComponentSpec.task_limit): the most tasks,
# processes and threads together, that its run may have at once, the pids.max
# of its cgroup. Measured on goblint at 1 and 2 cores (pids.peak of each
# component's cgroup, E8 reproduction): GemCutter at most 48 tasks, Dartagnan
# 12, Deagle 8, Goblint 7, IKOS-ConC 5; a JVM's own threads grow with the
# cores it sees, to a few hundred on a large machine. sv-sanitizers runs the
# task's program, whose threads count as its tasks: some programs create
# threads without bound (pthread-lp64-twins/sssc12-2-lp64 reached 13,540
# tasks in one run of E7), pthread-lit/sssc12 reached 402, and
# goblint-regression/28-race_reach_84, which creates 30,000 short-lived
# threads, 165. The limits of the components that run at the same time add up
# to at most 4096 tasks in every phase of conf/svcomp27.json, so that within a
# run limited to 5000 tasks (README.md, "Running CoOpeRace by hand")
# CoOpeRace itself keeps 900 even when every component reaches its limit.
TASK_LIMIT = 1024
PROGRAM_TASK_LIMIT = 2048


@dataclass(frozen=True)
class ComponentSpec:
    """A component CoOpeRace can run. `name` is its name in the conf, `module`
    the name of its BenchExec tool-info module, imported when the component is
    first run, `directory` its name in fm-tools, which is its directory under
    tools/ (where download-tools.py unpacks it) and its key in
    tools-options.json, `witness` the WitnessSpec of its witness files, and
    `task_limit` the most tasks its run may have at once (TASK_LIMIT), or
    None for no limit. The options it is run with are those of its
    ComponentVersion, before those of `witness`.

    `gets_memory_limit` says whether the tool-info module's cmdline is given
    the component's memory limit in its ResourceLimits, as BenchExec gives a
    tool the run's (ComponentRunner.command): for a component whose tool-info
    module passes it on and that sizes itself from it. Every other module is
    given empty ResourceLimits, so that its command line stays the one the
    competition's options give.

    `budget_variable` names the environment variable from which the
    component itself reads the seconds it may plan with, None for one that
    reads none. A component with a budget_variable is run with it set to
    the run's CPU-time budget if it is the run's first component, and else
    to what is left of that budget when it starts (component_time_budget,
    in whole seconds rounded up: budget_seconds), never to its own CPU-time
    limit: its own default is the competition's budget (iekke: 870 under
    900 s), so started first under the run's budget of 870 s it is told what
    the competition told it, and started late it is told the time the run
    really has. Its CPU-time limit then ends it, as it
    ends any component, and a verdict of a run that a limit ended is not
    accepted in the first-verdict mode. No margin is subtracted: the run's
    budget (870 of 900 s) already keeps CoOpeRace's own end out of the
    competition's limit. Its command line is unchanged."""

    name: str
    module: str
    directory: str
    witness: WitnessSpec = DEFAULT_WITNESS
    task_limit: int | None = TASK_LIMIT
    gets_memory_limit: bool = False
    budget_variable: str | None = None

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
    # its fm-tools options name (--portfolio-conf conf/svcomp27/seq.txt, a path
    # relative to Goblint's directory, the working directory of its run).
    #Witness: witness.yaml.path; the svcomp26 configuration writes no graphml witness.
    #Goblint 24080342 (svcomp27-0-g9febbae9c), like 24040934 and 23770235 before it, writes its default
    #correctness witness:
    #an invariant_set of format 2.0, or, where an invariant uses a ghost variable,
    #ghost_instrumentation and invariant_set both of format 2.1. Restricting it to
    #ghost_instrumentation (witness.yaml.entry-types) writes [] where it has no ghost,
    #and the ghost entry alone states no invariant, which validators rarely confirm.
    #exp.region-offsets: the svcomp27 configuration turns it on, and it gives a wrong
    #no-data-race true on races between fields of one struct; it is turned off here.
    ComponentSpec("Goblint", "benchexec.tools.goblint", "goblint",
                  witness=WitnessSpec("run", ("--set", "witness.yaml.path", "{dir}/witness.yml",
                                              "--set", "exp.region-offsets", "false"),
                                      ("witness.yml",))),
    #The wrapper deagle (4.2.0, 10.5281/zenodo.24278298, as 24150501 before it) runs deagle_exe
    #with --yaml-witness witness.yml in its working directory, Deagle's directory, for every
    #property, and deletes the file before each run of deagle_exe and unless it answers true or
    #false (4.1.0, 23488039, wrote error-witness.yml). The archive of 24278298 holds a
    #witness.yml of its smoke test, of 16 November; only what a run writes into the overlay of
    #Deagle's directory is collected, so it never counts
    ComponentSpec("Deagle", "benchexec.tools.deagle", "deagle",
                  witness=WitnessSpec("component", (), ("witness.yml",))),
    #Dartagnan-SVCOMP.sh sets DAT3M_OUTPUT to output/ in its working directory,
    #which must be Dartagnan's directory, and has no option for another one.
    #Version svcomp27 writes output/witness.yml; svcomp26 writes only output/witness.graphml
    #Its tool-info module is BenchExec e7b7d4a4's, which reads Dartagnan svcomp27's race report and
    #passes the task's data model (--data-model=ILP32 or LP64), and gives ERROR, never a plain
    #"false", for a failure it does not recognize (toolinfo/dartagnan.py)
    ComponentSpec("Dartagnan", "src.cooperace.toolinfo.dartagnan", "dartagnan",
                  witness=WitnessSpec("component", (), ("output/witness.yml",))),
    ComponentSpec("ULTIMATE Automizer", "benchexec.tools.ultimateautomizer", "uautomizer",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE GemCutter", "benchexec.tools.ultimategemcutter", "ugemcutter",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("ULTIMATE Taipan", "benchexec.tools.ultimatetaipan", "utaipan",
                  witness=_ULTIMATE_WITNESS),
    ComponentSpec("nacpa", "benchexec.tools.nacpa", "nacpa"),
    ComponentSpec("CPAchecker", "benchexec.tools.cpachecker", "cpachecker"),
    #sv-sanitizers.py writes witness.yml into its working directory, its own directory.
    #It runs the task's program, whose threads count as its tasks (TASK_LIMIT).
    ComponentSpec("sv-sanitizers", "benchexec.tools.sv-sanitizers", "sv-sanitizers",
                  witness=WitnessSpec("component", (), ("witness.yml",)), task_limit=PROGRAM_TASK_LIMIT),
    #iekke 2.0 (10.5281/zenodo.24279954): its wrapper iekke writes witness.yml into its working
    #directory, iekke's directory: a violation_sequence 2.2 for a false of no-data-race,
    #unreach-call and no-overflow, an invariant_set 2.1 with content [] for a true of
    #unreach-call and no-overflow, none for valid-memsafety, for a no-data-race true or for a
    #false of its concrete (gcc sanitizer) run. witness-content.yml (an intermediate) and
    #error-witness.graphml are never delivered. The wrapper reads its time budget, in wall-clock
    #seconds from its start, from DEAGLE_BUDGET (default 870 under the 900 s limit; read as
    #float(os.environ.get("DEAGLE_BUDGET", "870")), so "870" is its default exactly), and its
    #memory from the tightest cgroup memory.max above it, which is the component's share. Its
    #concrete guard compiles the task with a sanitizer and runs the program, whose threads count as
    #its tasks (PROGRAM_TASK_LIMIT)
    ComponentSpec("iekke", "benchexec.tools.iekke", "iekke", witness=WitnessSpec("component", (), ("witness.yml",)),
                  task_limit=PROGRAM_TASK_LIMIT, budget_variable="DEAGLE_BUDGET"),
    ComponentSpec("RacerF", "benchexec.tools.racerf", "racerf"),
    #IKOS-ConC svcomp27: bin/svcomp_witness.py writes a violation witness of format 2.2 to
    #--out (default witness.yml in its working directory) only for a confirmed race. The
    #tool-info module is not in the bundled BenchExec 3.35; toolinfo/ holds the bench-defs' copy.
    ComponentSpec("IKOS-ConC", "src.cooperace.toolinfo.ikos_conc", "ikos-conc",
                  witness=WitnessSpec("run", ("--out", "{dir}/witness.yml"), ("witness.yml",))),
    #Theta svcomp27 (8.10.1, 10.5281/zenodo.24128717): theta-start.sh runs theta.jar in its
    #working directory, Theta's directory, which writes witness.yml (and witness.graphml, never
    #delivered) there for a false and for a true of unreach-call and no-overflow (none for a
    #true of no-data-race); the archive ships neither. BenchExec's theta module passes the
    #run's memory limit as --memlimit, so CoOpeRace passes the component's (gets_memory_limit)
    #and the command line has the competition's form. Observed on goblint (16 November 2026)
    #with and without CoOpeRace: theta-start.sh starts the portfolio's JVM with -Xmx14210m and
    #the portfolio runs each configuration in a child JVM with -Xmx1420m, whatever --memlimit
    #says. The portfolio's time slices are fixed (they assume the 900 s of the competition); a
    #CPU-time limit of CoOpeRace's ends it early.
    ComponentSpec("Theta", "benchexec.tools.theta", "theta",
                  witness=WitnessSpec("component", (), ("witness.yml",)), gets_memory_limit=True),
    #SAF svcomp27 (0.1.0, 10.5281/zenodo.24267653): bin/saf verify writes its witness to --witness,
    #default witness.yml in its working directory, SAF's directory, and nothing for unknown; fm-tools
    #gives no options, and the tool-info module (toolinfo/saf.py, not in the bundled BenchExec 3.35)
    #passes the property file and --data-model. SAF reads no memory limit; its own time budget is
    #850 s (--timeout, not passed), so a CPU-time limit of CoOpeRace's below that ends it by a kill.
    #To confirm a false it compiles the task with clang-21 and runs the program natively under a
    #sanitizer, so the program's threads count as its tasks (PROGRAM_TASK_LIMIT).
    ComponentSpec("SAF", "src.cooperace.toolinfo.saf", "saf",
                  witness=WitnessSpec("component", (), ("witness.yml",)), task_limit=PROGRAM_TASK_LIMIT),
)}

# The file in TOOL_DIR that holds the ComponentVersion of every component of
# tools.txt and tools-pool.txt, written by scripts/download-tools.py.
OPTIONS_FILE = "tools-options.json"

# The file in a component's directory that records the DOI it was installed
# from, written by scripts/download-tools.py after the installation.
DOI_RECORD = ".doi"

# Seconds probe_versions waits for the version of an ULTIMATE component, and
# the version its tool-info object reports if the probe did not give one.
VERSION_PROBE_SECONDS = 60
UNKNOWN_VERSION = "unknown"


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
    working directory `cwd`, and `environments`, its tool-info module's
    environment(), from which RunExecutor makes the run's environment."""

    cmdline: list[str]
    cwd: str
    environments: dict


# The values of a task's `data_model` option (and of `--arch`) that the
# components' BenchExec tool-info modules understand. SV-COMP task definitions
# without a `data_model` are ILP32 programs, so that is what `--arch` defaults to.
DATA_MODELS = ("ILP32", "LP64")
DEFAULT_DATA_MODEL = "ILP32"

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
                 property_name: str = NO_DATA_RACE, task_path: str | None = None):
        if property_name not in FORMULAS:
            raise ValueError(f"unknown property {property_name!r}, expected one of {', '.join(FORMULAS)}")
        if data_model is None:
            print(f"CoOpeRace: no --arch given, assuming {DEFAULT_DATA_MODEL}", file=sys.stderr)
            data_model = DEFAULT_DATA_MODEL
        elif data_model not in DATA_MODELS:
            raise ValueError(f"unsupported data model {data_model!r}, expected one of {', '.join(DATA_MODELS)}")
        self.file = file
        #The task's path as CoOpeRace was given it (relative, as BenchExec gives it), which the
        #delivered witness names instead of `file` (deliver)
        self.task_path = task_path if task_path is not None else file
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
        #The tool-info object of each component, by name (tool_of)
        self.tools = {}
        self.tools_lock = threading.Lock()
        #How many components run_component has started in this run (under tools_lock): the
        #first one is told the run's whole time budget (component_time_budget)
        self.started = 0

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

    def container_problems(self) -> list[str]:
        """One message if BenchExec cannot run a component in its container
        here (execution.container_works on `tools_dir`), which is the case
        outside runexec and benchexec on Ubuntu 24.04; else none. Starts no
        component."""
        from . import execution  # imports benchexec.runexecutor, which needs Linux

        if execution.container_works(self.tools_dir):
            return []
        return ["BenchExec cannot run a component in its container here (its messages above say why); "
                "CoOpeRace runs components only in that container, which it can make under runexec or "
                "benchexec: from a shell, run it under runexec as "
                f"{os.path.join(TOOL_DIR, 'README.md')}, \"Running CoOpeRace by hand\", says"]

    def prepare(self) -> None:
        """Removes the witness files an earlier run delivered to the working
        directory and makes the work directory, which holds one directory per
        component run, made in run_component."""
        remove_old_witness_files()
        self.work_dir = tempfile.mkdtemp(prefix="cooperace-")

    def deliver(self, witness_files: list[str]) -> None:
        """Delivers `witness_files` with witness_files_to_file_root, with the
        task's absolute path, which the components were given, replaced by
        `task_path`: a validator runs on the task under the path BenchExec gave
        CoOpeRace, and Goblint's validator locates no invariant whose
        file_name is another path of the same file."""
        witness_files_to_file_root(witness_files, {self.file: self.task_path})

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
        "unknown", and NO_OUTCOME is returned, which the strategy counts as
        no verdict. The block names the component by the tool-info
        object's name(), or by the conf's name if there is no such object.
        StopSignal is a BaseException and propagates."""
        name = step.component
        try:
            actor = self.tool_of(step.component)
            name = actor.name()
            return self.run_component(actor, step, group)
        except Exception as error:
            #A run that execute_run gave up (execution.ComponentHang) has a status of its own
            status = getattr(error, "status", None)
            if status is None:
                traceback.print_exc()
                status = f"ERROR ({type(error).__name__}: {error})"
            print_component_run(name, subprocess.CompletedProcess(None, None, "", ""), status, "unknown")
            return NO_OUTCOME

    def tool_of(self, name: str) -> BaseTool2:
        """The tool-info object of the component `name`, made once
        (ComponentSpec.tool) and kept, so that what it caches, such as the
        version probe_versions found, is used by every later call."""
        with self.tools_lock:
            if name not in self.tools:
                self.tools[name] = self.registry[name].tool()
            return self.tools[name]

    def probe_versions(self, names: Iterable[str]) -> None:
        """Determines, before any component starts, the version of each
        component of `names` whose tool-info module is one of ULTIMATE's
        (benchexec.tools.ultimate.UltimateTool). Its cmdline asks for the
        version, which starts the tool's wrapper and several Java processes
        in CoOpeRace's own process, outside any container and limit; done
        when the component starts, that ran beside the stopping of the
        previous phase's components. The probe runs once here, in a daemon
        thread, for at most VERSION_PROBE_SECONDS, and the object of tool_of
        keeps the result (the module caches version() per object). If the
        probe fails or takes longer, the object's version() returns
        UNKNOWN_VERSION instead, with a line "CoOpeRace: ..." on stderr;
        cmdline then takes the default (wrapper) command line, which is the
        one CoOpeRace's options give in any case, since only the
        SV-COMP 2017 version is treated otherwise. Any exception from making
        the object or finding the executable is left to run_step."""
        from benchexec.tools.ultimate import UltimateTool

        for name in names:
            try:
                actor = self.tool_of(name)
                if not isinstance(actor, UltimateTool):
                    continue
                spec = self.registry[name]
                executable = actor.executable(
                    BaseTool2.ToolLocator(tool_directory=os.path.join(self.tools_dir, spec.directory)))
            except Exception:
                continue
            outcome = {}

            def probe(actor=actor, executable=executable, outcome=outcome):
                try:
                    outcome["version"] = actor.version(executable)
                except Exception as error:
                    outcome["error"] = error

            thread = threading.Thread(target=probe, name=f"version probe of {name}", daemon=True)
            try:
                thread.start()
            except RuntimeError as error:
                #The machine is out of tasks: go on without the version, as after a failed probe
                outcome["error"] = error
            else:
                thread.join(VERSION_PROBE_SECONDS)
            if outcome.get("version"):
                continue
            reason = (f"{type(outcome['error']).__name__}: {outcome['error']}" if "error" in outcome
                      else "no version" if not thread.is_alive() else f"no answer in {VERSION_PROBE_SECONDS:g} s")
            print(f"CoOpeRace: the version of {name} could not be determined ({reason}): going on without it",
                  file=sys.stderr, flush=True)
            actor.version = lambda executable: UNKNOWN_VERSION

    def command(self, actor: BaseTool2, spec: ComponentSpec, witness_dir: str,
                memory: int | None = None, budget: float | None = None) -> ComponentCommand:
        """The ComponentCommand that starts the component `actor`, the
        tool-info object of the registry entry `spec`, on the task, with the
        witness directory `witness_dir` of its run, as BenchExec would start it
        from the component's directory under `tools_dir`, its tool directory:

        - the command line is benchexec.model.cmdline_for_run's, given the
          executable that `actor.executable` finds in the tool directory,
          the options of the component's ComponentVersion followed by those
          of its WitnessSpec, the task file, the property file, the task
          options data_model and language, and ResourceLimits that are empty
          (the tool-info module for CoOpeRace passes CoOpeRace no limits),
          except for a component whose `spec.gets_memory_limit`: its
          ResourceLimits hold `memory`, the component's memory limit in
          bytes, or, if it has none (None), the run's (run_memory_limit), which
          then bounds it, or none if the run has none either.
          cmdline_for_run makes a relative path relative to the working
          directory, and expands environment variables and ~ in every
          argument; the executable, the task and the property file are
          absolute paths here, so they are passed as they are;
        - the working directory is `actor.working_directory(executable)`,
          relative to the tool directory, as BenchExec's is relative to the
          directory it runs in, which SV-COMP makes the tool's directory. The
          default, os.curdir, which every module of REGISTRY keeps in
          BenchExec 3.31, so gives the tool directory;
        - the environments are `actor.environment(executable)`, and, for a
          component with a `spec.budget_variable` and a `budget` in seconds
          (component_time_budget; None: none), that variable set to
          budget_seconds(budget).

        Raises what `actor.executable` raises, ToolNotFoundException if the
        executable is not there, and KeyError if `versions` has no entry for
        the component."""
        version = self.versions[spec.directory]
        tool_location = os.path.join(self.tools_dir, spec.directory)
        executable = actor.executable(BaseTool2.ToolLocator(tool_directory=tool_location))
        options = list(version.options) + witness_options(spec.witness, witness_dir)
        limits = BaseTool2.ResourceLimits()
        if spec.gets_memory_limit:
            limits = BaseTool2.ResourceLimits(memory=memory if memory is not None else run_memory_limit())
        cmdline = bmodel.cmdline_for_run(
            actor, executable, options, [self.file], None, self.property_file,
            {"data_model": self.data_model, "language": "C"}, limits)
        cwd = os.path.normpath(os.path.join(tool_location, actor.working_directory(executable)))
        environments = actor.environment(executable)
        if spec.budget_variable and budget is not None:
            environments = {**environments, "newEnv": {**environments.get("newEnv", {}),
                                                       spec.budget_variable: str(budget_seconds(budget))}}
        return ComponentCommand(cmdline, cwd, environments)

    def run_component(self, actor: BaseTool2, step: Step, group: ComponentGroup) -> Outcome:
        """Runs the component `actor` of the config.Step `step` on the task in
        the ComponentGroup `group`, with the step's limits, and prints its
        block. Returns the Outcome of its verdict if `step.accept` accepts the
        verdict and `group` was not stopped, and NO_OUTCOME otherwise. The
        Outcome's witness_files hold the one file the component wrote during
        this run that witnesses.passing_witness accepts for the verdict on
        `property_name`, with the sub-property its result names
        (witnesses.subproperty_of), or none if no file passes; the verdict is
        returned either way.

        In the first-verdict mode (`step.first_verdict`), a verdict of a run
        that RunExecutor ended for a limit or a stop (a termination reason,
        such as "cputime" or "memory") is no verdict: NO_OUTCOME, after the
        block (with result "unknown") and a line that says so. The
        Outcome's witness_files are as outside the mode (the component's own
        file that passes the check, or none), and the Outcome has `measured`
        and `witness_passes`.

        The component's entry in the registry is looked up by the conf's name
        of the component, `step.component`, which config.load has checked is
        in the registry, and its options in `versions`, which
        installation_problems has checked hold it; the case of `actor.name()`
        (BenchExec's sv-sanitizers module names itself "SV-sanitizers") does
        not matter. The block is printed under `actor.name()`.

        It runs as `command` says, with execution.execute_run and the limits
        of announce_limits and announce_task_limit, with its output in `witness_dir`.log beside
        `witness_dir`, the directory made for this run. Its witness files end
        up in `witness_dir`: a component whose WitnessSpec writes there gets
        it with full access; one that writes under its own directory writes
        into the overlay, from which RunExecutor moves them
        (result_files_patterns)."""
        from . import execution  # imports benchexec.runexecutor, which needs Linux

        spec = self.registry[step.component]
        #A directory of this run, in which the component's witnesses end up
        witness_dir = tempfile.mkdtemp(prefix=actor.name().replace(" ", "_") + "-", dir=self.work_dir)
        memory, cpu = announce_limits(step)
        with self.tools_lock:
            first, self.started = self.started == 0, self.started + 1
        budget = announce_time_budget(spec, step, first)
        command = self.command(actor, spec, witness_dir, memory, budget)
        tasks = announce_task_limit(step.component, spec.task_limit, execution.task_limit_supported)
        tool_result = execution.execute_run(
            command.cmdline, command.cwd, command.environments, group,
            log_file=witness_dir + ".log",
            dir_modes=execution.container_dir_modes(os.path.join(self.tools_dir, spec.directory),
                                                    [] if spec.witness.directory == "component" else [witness_dir],
                                                    [self.file, self.property_file]),
            memory=memory, cpu_time=cpu, output_dir=witness_dir,
            result_files_patterns=result_files_patterns(spec.witness), tasks=tasks)
        witness_files = witness_files_in(spec.witness, witness_dir)

        if group.stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used.
            #This check is also why component_status never gets a returncode of
            #None: execute_run returns None only when the group was already
            #stopped, and a group never becomes unstopped.
            print_component_run(actor.name(), tool_result, "stopped by CoOpeRace", None)
            return NO_OUTCOME

        status = component_status(actor, command.cmdline, tool_result, tool_result.termination_reason)

        if confirm_verdict(step.accept, status, "true", self.property_name):
            verdict = "true"
        elif confirm_verdict(step.accept, status, "false", self.property_name):
            verdict = "false"
        else:
            verdict = "unknown"

        if step.first_verdict and verdict != "unknown" and tool_result.termination_reason is not None:
            print_component_run(actor.name(), tool_result, status, "unknown")
            with PRINT_LOCK:
                print(f"CoOpeRace: {step.component} {verdict} from a run that ended by "
                      f"terminationreason={tool_result.termination_reason}: no verdict in the first-verdict mode",
                      flush=True)
            return NO_OUTCOME

        print_component_run(actor.name(), tool_result, status, verdict)

        if verdict == "unknown":
            return NO_OUTCOME
        result = reported_result(self.property_name, status)
        witness = passing_witness(witness_files, self.property_name, verdict, subproperty_of(result),
                                  cpu_time_budget=step.run_cpu_time_budget)
        if step.first_verdict and step.witness_lint and needs_witness(self.property_name, verdict):
            witness = self.linted_witness(step, witness_files, verdict, subproperty_of(result))
        if step.first_verdict:
            return Outcome(verdict, actor.name(), [] if witness is None else [witness], result,
                           measured_fields(tool_result), (witness is not None) if witness_files else None)
        return Outcome(verdict, actor.name(), [] if witness is None else [witness], result)


    def linted_witness(self, step: Step, witness_files: list[str], verdict: str,
                       subproperty: str | None) -> str | None:
        """The first of `witness_files` (GraphML files left out) that counts by
        WitnessLint (witnesslint.lint_delivered: a copy as deliver would
        deliver it, under the name DELIVERED_NAME, on the task's program), or
        None. A file counts if the
        linter says it is valid; not if it says it is invalid; and, where the
        linter fails technically or its self-test failed
        (witnesslint.self_test_problems), if witnesses.passing_witness
        accepts it, with a line "CoOpeRace: WARNING: ..." that says so. Each
        outcome is printed on a line "CoOpeRace: WitnessLint ..."."""
        from . import witnesslint

        problems = witnesslint.self_test_problems(step.run_cpu_time_budget)
        found = None
        for path in [path for path in witness_files if not path.lower().endswith(".graphml")]:
            internal = passing_witness([path], self.property_name, verdict, subproperty,
                                       cpu_time_budget=step.run_cpu_time_budget) is not None
            internal_text = "passes" if internal else "does not pass"
            if problems:
                say_line(f"CoOpeRace: WARNING: WitnessLint is not used ({'; '.join(problems)}): the internal "
                         f"check decides on {step.component}'s witness {path}, which it {internal_text}")
                counts = internal
            else:
                got = witnesslint.lint_delivered(path, self.file, verdict, step.run_cpu_time_budget,
                                                 {self.file: self.task_path}, self.work_dir)
                if got.outcome == witnesslint.FAILED:
                    say_line(f"CoOpeRace: WARNING: WitnessLint failed technically on {step.component}'s witness "
                             f"{path} ({got.status}): the internal check decides, and it {internal_text}")
                    counts = internal
                else:
                    say_line(f"CoOpeRace: WitnessLint on {step.component}'s witness {path}: {got.outcome} "
                             f"({got.status}); the internal check {internal_text}")
                    counts = got.outcome == witnesslint.VALID
            if counts and found is None:
                found = path
        return found


def say_line(line: str) -> None:
    """Prints `line` holding PRINT_LOCK."""
    with PRINT_LOCK:
        print(line, flush=True)


# The pattern, as a glob, of the files a component without a list of witness
# files writes under its own directory (WitnessSpec.files None): the names
# witness_files_under matches, "witness" in any case and ending in yml.
_WITNESS_NAME_PATTERNS = ("**/*[Ww][Ii][Tt][Nn][Ee][Ss][Ss]*yml",)


def result_files_patterns(witness: WitnessSpec) -> list[str]:
    """The `result_files_patterns` of a run in the container of a component
    with the WitnessSpec `witness`: none if it writes into the run's
    directory, else the files `witness` names or the witness name pattern."""
    if witness.directory == "run":
        return []
    if witness.files is None:
        return list(_WITNESS_NAME_PATTERNS)
    return list(witness.files)


def witness_candidates(witness: WitnessSpec, directory: str) -> list[str]:
    """The paths under `directory` that `witness` names, or, if it names
    none, the files there that witness_files_under matches."""
    if witness.files is None:
        return witness_files_under(directory)
    return [os.path.join(directory, file) for file in witness.files]


def witness_files_in(witness: WitnessSpec, witness_dir: str) -> list[str]:
    """The witness_candidates of `witness` in the run's directory
    `witness_dir` that are files; the directory is new, so each was
    written during the run."""
    return [path for path in witness_candidates(witness, witness_dir) if os.path.isfile(path)]


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


def announce_limits(step: Step) -> tuple[int | None, int | None]:
    """The memory limit in bytes (component_memory_limit) and the CPU-time
    limit in seconds (component_cpu_time_limit) of `step`, each None if it
    has none. Prints each limit as "Memory limit of <component>: <n> bytes"
    and "CPU-time limit of <component>: <n> s", and a percentage the run has
    no memory limit for as "Memory limit of <component>: none (no cgroup
    memory limit found for "<value>")"."""
    memory, cpu = component_memory_limit(step), component_cpu_time_limit(step)
    lines = []
    if memory is None and isinstance(step.memory_limit, str):
        #A percentage that run_memory_limit could not resolve
        lines.append(f"Memory limit of {step.component}: none "
                     f"(no cgroup memory limit found for \"{step.memory_limit}\")")
    if memory is not None:
        lines.append(f"Memory limit of {step.component}: {memory} bytes")
    if cpu is not None:
        lines.append(f"CPU-time limit of {step.component}: {cpu} s")
    if lines:
        with PRINT_LOCK:
            print("\n".join(lines), flush=True)
    return memory, cpu


def component_time_budget(step: Step, used: float | None, first: bool = False) -> float | None:
    """The seconds a component of the config.Step `step` that reads its own
    time budget (ComponentSpec.budget_variable) may plan with: None if the
    run has no runCpuTimeBudget; the whole budget if the component is the
    run's first (`first`); else what is left of it given the CPU time `used`
    that the run has used when the component starts (run_cpu_seconds; None:
    unknown, taken as 0), at least 0.

    The first component is told the whole budget although CoOpeRace's own
    start (about 5 s of CPU time in the competition image: installation and
    version checks) has used some of it, so that it plans as in the
    competition, where its run starts at 0 s; the run's budget, 870 of the
    900 s, still ends it before the competition's limit. The component's own
    CPU-time limit does not enter: it ends the component, but does not shape
    its plan (see ComponentSpec)."""
    if step.run_cpu_time_budget is None:
        return None
    if first:
        return float(step.run_cpu_time_budget)
    return max(0.0, step.run_cpu_time_budget - (used or 0.0))


def budget_seconds(budget: float) -> int:
    """The time budget `budget` (component_time_budget) as the whole seconds
    passed to the component: rounded up, so that a start with any time left
    is never told 0."""
    return max(0, math.ceil(budget))


def announce_time_budget(spec: ComponentSpec, step: Step, first: bool = False) -> float | None:
    """For a component that reads its time budget (`spec.budget_variable`):
    component_time_budget of `step` with the CPU time the run has used now
    and `first` (whether it is the run's first component), printed as "Time
    budget of <component>: <x> s left of the run's <budget> s
    (<variable>=<n>)", with "(first component)" after the budget for the
    first. None, and nothing printed, for any other component or without a
    run budget."""
    if not spec.budget_variable:
        return None
    budget = component_time_budget(step, run_cpu_seconds(), first)
    if budget is not None:
        say_line(f"Time budget of {step.component}: {budget:.1f} s left of the run's "
                 f"{step.run_cpu_time_budget:g} s{' (first component)' if first else ''} "
                 f"({spec.budget_variable}={budget_seconds(budget)})")
    return budget


def announce_task_limit(component: str, limit: int | None, supported) -> int | None:
    """The task limit `limit` of `component` (ComponentSpec.task_limit) if
    the function `supported` (execution.task_limit_supported) says that
    runs' tasks can be limited, else None. Prints the limit as "Task limit
    of <component>: <n> tasks", and a limit that cannot be applied as "Task
    limit of <component>: none (no pids cgroup controller found for <n>
    tasks)"; prints nothing for a component without a limit."""
    if limit is None:
        return None
    applied = limit if supported() else None
    with PRINT_LOCK:
        if applied is None:
            print(f"Task limit of {component}: none (no pids cgroup controller found for {limit} tasks)",
                  flush=True)
        else:
            print(f"Task limit of {component}: {limit} tasks", flush=True)
    return applied


def component_status(actor: BaseTool2, cmdline: list[str], tool_result: subprocess.CompletedProcess,
                     termination_reason: str | None = None) -> str:
    """The status BenchExec would give the run `tool_result` (an
    execution.FinishedRun) of `actor` with the command line `cmdline`, which
    RunExecutor ended with `termination_reason` (None: it ended by itself):
    `actor.determine_result` on a Run as benchexec.model.Run.set_result
    makes it, with the lines of output_lines; an unspecific result
    (unknown, error or done) refined as benchexec.model.Run._analyze_result
    (BenchExec 3.31 to 3.35) refines it: TERMINATION_STATUSES of the
    termination reason ("TIMEOUT", "OUT OF MEMORY"), else "ABORTED",
    "SEGMENTATION FAULT", "KILLED" or "KILLED BY SIGNAL <n>" for the signal
    that ended it, or "ERROR (<code>)" for an error with a non-zero exit code.
    Unlike BenchExec, which writes a specific result found after a limit as
    "TIMEOUT (true)", a specific result is kept, so a verdict a component
    printed before its limit ended it is still accepted."""
    returncode = tool_result.returncode
    if returncode < 0:
        exit_code = butil.ProcessExitCode.create(signal=-returncode)
    else:
        exit_code = butil.ProcessExitCode.create(value=returncode)
    run = BaseTool2.Run(
        cmdline=cmdline,
        exit_code=exit_code,
        output=BaseTool2.RunOutput(output_lines(tool_result.stdout)),
        termination_reason=termination_reason
    )
    status = actor.determine_result(run)

    if status in bresult.RESULT_LIST_OTHER:
        if termination_reason is not None:
            status = TERMINATION_STATUSES.get(termination_reason, termination_reason)
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


# The status of a run that RunExecutor ended, by its termination reason,
# as benchexec.model gives it (_ERROR_RESULTS_FOR_TERMINATION_REASON, the same
# in BenchExec 3.31 to 3.35). "killed" is a run that CoOpeRace stopped, which
# run_component reports as "stopped by CoOpeRace" before any status is read.
TERMINATION_STATUSES = {
    "cputime": bresult.RESULT_TIMEOUT,
    "cputime-soft": bresult.RESULT_TIMEOUT,
    "walltime": bresult.RESULT_TIMEOUT,
    "memory": "OUT OF MEMORY",
    "killed": "KILLED",
    "failed": "FAILED",
    "files-count": "FILES-COUNT LIMIT",
    "files-size": "FILES-SIZE LIMIT",
}


def output_lines(output: str) -> list[str]:
    r"""The lines of a component's output `output`, each with its line
    separator, as benchexec.model.Run.set_result reads them from the run's log
    with readlines() for the RunOutput it gives the tool-info module: split
    after each "\n" only, and a last line without one kept as it is. Leading
    and trailing blank lines are kept. RunOutput gives a module each line
    without its separator. execution.execute_run reads the run's log in text
    mode, which turns "\r\n" and "\r" into "\n", as BenchExec's reading of
    the log does."""
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
    `property_name`. For no-data-race it is "true" or "false(no-data-race)",
    for both accepted "false" statuses: BenchExec's tool-info module for
    CoOpeRace (since sosy-lab/benchexec#1337, the module SV-COMP 2027 uses)
    passes the line through as the result, and the organizers' scoring counts
    a plain "false" of an active participant as correct-unconfirmed (0
    points) whatever the validators say. For another property it is `status`, the result BenchExec gives the
    component alone, except that a plain "false" (accepted only for a
    property without sub-properties) becomes "false(<property_name>)", which
    BenchExec scores the same on a task of that property and which names the
    property in the result."""
    if property_name == NO_DATA_RACE:
        return "true" if status == bresult.RESULT_TRUE_PROP else f"{bresult.RESULT_FALSE_PROP}({NO_DATA_RACE})"
    if status == bresult.RESULT_FALSE_PROP:
        return f"{bresult.RESULT_FALSE_PROP}({property_name})"
    return status


def print_component_run(name: str, tool_result: subprocess.CompletedProcess, status: str,
                        verdict: str | None) -> None:
    """Prints the output of a component run between "---<name> logs---"
    and "---end of <name> logs---", then the line "Tool name: <name>
    Status: <status> Exit code: <exit code>" and, unless `verdict` is None,
    "Tool name: <name> Result: <verdict>", and, for a run that was started,
    the lines of run_lines, all at once, so that the output of components
    running in parallel is not interleaved."""
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
    lines += run_lines(name, tool_result)
    with PRINT_LOCK:
        print("\n".join(lines), flush=True)


def measured_fields(tool_result: subprocess.CompletedProcess) -> str:
    """The fields "cputime=<s>s walltime=<s>s memory=<bytes>B
    terminationreason=<reason>" of the line "Tool name: <name> Measured:
    ..." of run_lines for the run `tool_result` that was started."""
    return run_lines("", tool_result)[0].split(" Measured: ", 1)[1]


def run_lines(name: str, tool_result: subprocess.CompletedProcess) -> list[str]:
    """For a run that was started (an execution.FinishedRun with a
    returncode), the line "Tool name: <name> Measured: cputime=<s>s
    walltime=<s>s memory=<bytes>B terminationreason=<reason>" with what
    BenchExec measured, a value it did not measure as "-" and a run that
    ended by itself as "terminationreason=none"; else none."""
    if tool_result.returncode is None:
        return []

    def seconds(value):
        return "-" if value is None else f"{value:.2f}s"

    memory = "-" if tool_result.memory is None else f"{tool_result.memory}B"
    return [f"Tool name: {name} Measured: cputime={seconds(tool_result.cpu_time)} "
            f"walltime={seconds(tool_result.wall_time)} memory={memory} "
            f"terminationreason={tool_result.termination_reason or 'none'}"]


def witness_options(witness: WitnessSpec, witness_dir: str) -> list[str]:
    """The `options` of the WitnessSpec `witness`, with {dir} replaced by
    `witness_dir`."""
    return [option.replace("{dir}", witness_dir) for option in witness.options]


def witness_files_under(tool_dir: str) -> list[str]:
    """The files under `tool_dir` whose name contains "witness" in any case
    and ends in yml, sorted by path, so that which of them
    witnesses.passing_witness takes first does not depend on the order in
    which the file system lists them."""
    witness_files = []

    for root, _dirs, files in os.walk(tool_dir):
        for file in files:
            if 'witness' in file.lower() and file.endswith("yml"):
                witness_files.append(os.path.join(root, file))

    return sorted(witness_files)


def witness_files_to_file_root(witness_files: list[str], paths: Mapping[str, str] | None = None) -> None:
    """Copies the witness of one component run, the at most one file of the
    Outcome of ComponentRunner.run_component, which witnesses.passing_witness
    accepted, to the working directory as witness.yml, with every occurrence
    of a key of `paths` replaced by its value (the task's path). Raises
    ValueError for more than one file, and for a file whose name ends in
    .graphml, which never passes."""
    paths = {old: new for old, new in (paths or {}).items() if old and old != new}
    if len(witness_files) > 1:
        raise ValueError(f"more than one witness file to deliver: {', '.join(witness_files)}")
    for file in witness_files:
        if file.lower().endswith(".graphml"):
            raise ValueError(f"a GraphML file is never delivered: {file}")
        target = os.path.join(os.getcwd(), DELIVERED_NAME)
        if not paths:
            shutil.copy2(file, target)
            continue
        with open(file, encoding="utf-8", errors="surrogateescape") as source:
            text = source.read()
        for old, new in paths.items():
            text = text.replace(old, new)
        with open(target, "w", encoding="utf-8", errors="surrogateescape") as out:
            out.write(text)


def remove_old_witness_files() -> None:
    """Removes witness.yml, the name witness_files_to_file_root delivers a
    witness under, and witness.graphml, which earlier versions of CoOpeRace
    delivered, from the working directory, so that one left there by an
    earlier run is not delivered with this run's verdict."""
    for name in ("witness.graphml", "witness.yml"):
        try:
            os.remove(os.path.join(os.getcwd(), name))
        except FileNotFoundError:
            pass
