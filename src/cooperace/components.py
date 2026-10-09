import os
import signal
import subprocess
import shutil
import sys
import importlib
import tempfile
import threading
import traceback

from benchexec import result as bresult
from benchexec import util as butil
from benchexec.tools.template import BaseTool2
from benchexec.tools.goblint import Tool as Goblint
from benchexec.tools.dartagnan import Tool as Dartagnan
from benchexec.tools.deagle import Tool as Deagle
from benchexec.tools.ultimateautomizer import Tool as UltimateAutomizer
from benchexec.tools.ultimategemcutter import Tool as UltimateGemCutter
from benchexec.tools.ultimatetaipan import Tool as UltimateTaipan
from benchexec.tools.nacpa import Tool as nacpa
from benchexec.tools.cpachecker import Tool as CPAchecker
from benchexec.tools.racerf import Tool as RacerF
sv_sanitizers = importlib.import_module("benchexec.tools.sv-sanitizers")

from .processes import ComponentGroup, run_memory_limit, stopProcessGroups
from .strategy import NO_OUTCOME, Outcome, Strategy



# Which files of a component run are its witness, per component name.
# "directory" is "run" if "options" tell the component to write them into the
# directory runActor makes for the run ({dir} in an option stands for it), and
# "component" if the component writes them under its own directory, the
# working directory of its run. "files" are their paths relative to that
# directory, or None for every file there whose name contains "witness" and
# ends in graphml or yml (Cooperace.witnessFiles). collectWitnessFiles takes
# only those modified during the run, and witnessFilesToFileRoot delivers a
# graphml file as witness.graphml and a YAML file as witness.yml. A component
# that writes a new witness file, or a new format, needs only its entry here.
_ULTIMATE_WITNESS_FILES = {
    #Ultimate.py --witness-dir; witness.yml is written beside the graphml for "true"
    "directory": "run", "options": ["--witness-dir", "{dir}"],
    "files": ["witness.graphml", "witness.yml"],
}
WITNESS_FILES = {
    #witness.yaml.path; the svcomp26 configuration writes no graphml witness
    "Goblint": {"directory": "run", "options": ["--set", "witness.yaml.path", "{dir}/witness.yml"],
                "files": ["witness.yml"]},
    #Dartagnan-SVCOMP.sh sets DAT3M_OUTPUT to output/ in its working directory,
    #which must be Dartagnan's directory, and has no option for another one
    "Dartagnan": {"directory": "component", "options": [], "files": ["output/witness.graphml"]},
    "ULTIMATE Automizer": _ULTIMATE_WITNESS_FILES,
    "ULTIMATE GemCutter": _ULTIMATE_WITNESS_FILES,
    "ULTIMATE Taipan": _ULTIMATE_WITNESS_FILES,
}
#For a component without an entry: the name pattern, under its own directory
DEFAULT_WITNESS_FILES = {"directory": "component", "options": [], "files": None}


# This could be done in the download_tools.py part, where it creates a .json for this dictionary 
def tool_locations():
    default_path = os.path.join(os.getcwd(), "tools")
    
    return {
            "Goblint": os.path.join(default_path, "goblint"),
            "Deagle": os.path.join(default_path, "deagle"),
            "Dartagnan": os.path.join(default_path, "dartagnan"),
            "ULTIMATE Automizer": os.path.join(default_path, "uautomizer"),
            "ULTIMATE GemCutter": os.path.join(default_path, "ugemcutter"),
            "ULTIMATE Taipan": os.path.join(default_path, "utaipan"),
            "nacpa": os.path.join(default_path, "nacpa"),
            "CPAchecker": os.path.join(default_path, "CPAchecker-4.0-unix"),
            "sv-sanitizers": os.path.join(default_path, "sv-sanitizers"),
            "RacerF": os.path.join(default_path, "racerf")
    }

# The values of a task's `data_model` option (and of `--arch`) that the
# components' BenchExec tool-info modules understand. SV-COMP task definitions
# without a `data_model` are ILP32 programs, so that is what `--arch` defaults to.
DATA_MODELS = ("ILP32", "LP64")
DEFAULT_DATA_MODEL = "ILP32"

class Cooperace(Strategy):
    # `data_model` is the value of `--arch`: one of DATA_MODELS, or None when
    # the option was not given, which means DEFAULT_DATA_MODEL (reported on
    # stderr). Any other value raises ValueError: given to the components as it
    # is, the Goblint and ULTIMATE tool-info modules raise
    # UnsupportedFeatureException inside runActor and Dartagnan's ignores it.
    def __init__(self, file, property_file, data_model, conf):
        if data_model is None:
            print(f"CoOpeRace: no --arch given, assuming {DEFAULT_DATA_MODEL}", file=sys.stderr)
            data_model = DEFAULT_DATA_MODEL
        elif data_model not in DATA_MODELS:
            raise ValueError(f"unsupported data model {data_model!r}, expected one of {', '.join(DATA_MODELS)}")
        self.file = file
        self.property_file = os.path.abspath(property_file)
        self.data_model = data_model
        self.conf = conf

        #Any new tools need to be added here. Key values taken from corresponding tool name() value
        self.tools = {
            "Goblint": Goblint(),
            "Deagle": Deagle(),
            "Dartagnan": Dartagnan(),
            "ULTIMATE Automizer": UltimateAutomizer(),
            "ULTIMATE GemCutter": UltimateGemCutter(),
            "ULTIMATE Taipan": UltimateTaipan(),
            "nacpa": nacpa(),
            "CPAchecker": CPAchecker(),
            "sv-sanitizers": sv_sanitizers.Tool(),
            "RacerF": RacerF()
        }

        #Tool name and tool directory dictionary
        self.tool_locations = tool_locations()

        #Components started outside any runParallel; execute stops it on return
        self.root_group = ComponentGroup()
        self.print_lock = threading.Lock()

    def componentMemoryLimit(self, step):
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

    def componentCpuTimeLimit(self, step):
        """CPU-time limit in seconds for the config.Step `step`, from its
        `cpu_time_limit` (the conf's `cpuTimeLimits`), or None."""
        value = step.cpu_time_limit
        return None if value is None else int(value)

    def withResourceLimits(self, step, command):
        """`command`, started with the limits of the config.Step `step`.

        RLIMIT_DATA is set to the memory limit (componentMemoryLimit). A
        percentage for which the run has no memory limit applies no limit and
        prints "Memory limit of <component>: none (no cgroup memory limit found for
        "<value>")". RLIMIT_DATA bounds the private writable memory (heap,
        anonymous mmap) of each process of the component; the component's
        processes inherit it. A JVM
        that reaches it fails to commit memory and exits, so the component
        ends without a verdict and its memory is free for the components still
        running. RLIMIT_AS is not used: a JVM reserves its whole `-Xmx` as
        address space at start and fails to start under it.

        RLIMIT_CPU is set to the CPU-time limit (componentCpuTimeLimit), with
        the hard limit one second higher: the kernel sends SIGXCPU to a process
        that has used the limit and SIGKILL one second later. It counts the
        CPU time of each process on its own, not of the component's processes
        together. Goblint's portfolio runner (goblint_runner.py) runs one
        goblint process per level; when the limit ends one, the runner gives
        up the remaining levels and exits without a verdict. Goblint's stage
        so uses the CPU time of the levels that end by themselves plus at most
        the limit.

        The limits are set by a small Python process that then execs the
        component, because `preexec_fn` is unsafe with the threads of
        `runParallel`. The exec keeps the process, so the component is still
        the leader of the session actorResult starts, and stopProcessGroups
        still ends it with every process it starts.
        """
        limits = []
        tool_name = step.component
        memory = self.componentMemoryLimit(step)
        if memory is not None:
            limits.append(f"RLIMIT_DATA={memory}:{memory}")
            message = f"Memory limit of {tool_name}: {memory} bytes (RLIMIT_DATA)"
            with self.print_lock:
                print(message, flush=True)
        else:
            value = step.memory_limit
            if isinstance(value, str) and value.endswith("%"):
                #A percentage that run_memory_limit could not resolve: no
                #limit applies, and the run says so
                with self.print_lock:
                    print(f"Memory limit of {tool_name}: none "
                          f"(no cgroup memory limit found for \"{value}\")", flush=True)
        cpu = self.componentCpuTimeLimit(step)
        if cpu is not None:
            limits.append(f"RLIMIT_CPU={cpu}:{cpu + 1}")
            with self.print_lock:
                print(f"CPU-time limit of {tool_name}: {cpu} s (RLIMIT_CPU)", flush=True)
        if not limits:
            return command
        setter = ("import os, resource, sys\n"
                  "for limit in sys.argv[1].split(','):\n"
                  "    name, value = limit.split('=')\n"
                  "    soft, hard = value.split(':')\n"
                  "    resource.setrlimit(getattr(resource, name), (int(soft), int(hard)))\n"
                  "os.execvp(sys.argv[2], sys.argv[2:])")
        return [sys.executable, "-c", setter, ",".join(limits)] + command

    def actorResult(self, command, cwd, group):
        """Runs `command` in `cwd` as the leader of a new session, so that the
        component and every process it starts form one process group, which
        ComponentGroup.stop can end, and records it in the ComponentGroup
        `group` while it runs. Returns a subprocess.CompletedProcess whose
        `stdout` is the component's standard output and standard error in one,
        as BenchExec captures them, and whose `stderr` is empty; `returncode`
        is negative if the component was ended by a signal, and None if the
        group was stopped before it could start."""
        if group.stopped:
            return subprocess.CompletedProcess(command, None, "", "")
        process = subprocess.Popen(command,
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                        start_new_session=True
                        )
        try:
            if not group.add(process):
                stopProcessGroups([process])
            output, _ = process.communicate()
        except BaseException:
            #StopSignal while this thread (the main thread) waits
            stopProcessGroups([process])
            process.wait()
            raise
        finally:
            group.remove(process)
        return subprocess.CompletedProcess(command, process.returncode, output, "")

        
    def runOne(self, step, group):
        """Runs the config.Step `step` with runActor in the ComponentGroup
        `group` and returns the Outcome runActor returns.

        An Exception from runActor, from setting the run up (for example
        ToolNotFoundException from the tool-info module's `executable`) or
        from the run, makes this step a step without a verdict: the traceback
        goes to stderr, the component's block is printed with status "ERROR
        (<exception class>: <message>)" and result "unknown", and NO_OUTCOME
        is returned, so that the next step of a sequence runs. StopSignal is
        a BaseException and propagates."""
        actor = self.tools[step.component]
        try:
            return self.runActor(actor, step, group)
        except Exception as error:
            traceback.print_exc()
            self.printComponentRun(actor.name(),
                                   subprocess.CompletedProcess(None, None, "", ""),
                                   f"ERROR ({type(error).__name__}: {error})", "unknown")
            return NO_OUTCOME

    def witnessFiles(self, tool_dir):
        witness_files = []
        
        for root, dirs, files in os.walk(tool_dir):
            for file in files:
                if 'witness' in file.lower() and (file.endswith("graphml") or file.endswith("yml")):
                    witness_files.append(os.path.join(root, file))

        return witness_files
        

    def witnessFilesToFileRoot(self, witness_files):
        """Copies the witness files of one component run, from
        collectWitnessFiles, to the working directory under the name of their
        format: a graphml file as witness.graphml, a YAML file as witness.yml."""
        for file in witness_files:
            if file.endswith(".graphml"):
                name = "witness.graphml"
            elif file.endswith(".yml") or file.endswith(".yaml"):
                name = "witness.yml"
            else:
                continue
            shutil.copy2(file, os.path.join(os.getcwd(), name))

    def removeOldWitnessFiles(self):
        """Removes witness.graphml and witness.yml, the names
        witnessFilesToFileRoot delivers witnesses under, from the working
        directory, so that one left there by an earlier run is not delivered
        with this run's verdict."""
        for name in ("witness.graphml", "witness.yml"):
            try:
                os.remove(os.path.join(os.getcwd(), name))
            except FileNotFoundError:
                pass

    def witnessSpec(self, actor):
        return WITNESS_FILES.get(actor.name(), DEFAULT_WITNESS_FILES)

    def witnessOptions(self, actor, witness_dir):
        """The "options" of `actor`'s entry in WITNESS_FILES, with {dir}
        replaced by `witness_dir`."""
        return [option.replace("{dir}", witness_dir) for option in self.witnessSpec(actor)["options"]]

    def startTime(self, witness_dir):
        """A time stamp of now, taken from the file system as the modification
        time of a new file in `witness_dir`, so that it compares exactly with
        the modification times of the files a component writes afterwards."""
        marker = os.path.join(witness_dir, ".started")
        open(marker, "w").close()
        return os.stat(marker).st_mtime_ns

    def collectWitnessFiles(self, actor, cwd, witness_dir, started):
        """Returns the witness files of the run of `actor` that began at
        `started`, all in `witness_dir`: the files that `actor`'s entry in
        WITNESS_FILES names, in `witness_dir` or under the component's own
        directory `cwd`, that were modified at or after `started`. Those under
        `cwd` are moved to the same relative path in `witness_dir`. Any other
        file, one left by an earlier run or shipped with the component
        (Goblint's smoketests/*witness*.yml), is neither returned nor
        touched.

        The comparison of modification times assumes that `witness_dir` (where
        `started` was taken, by startTime) and the directory a component
        writes to take their modification times from the same clock at the
        same granularity, which holds on Linux for ext4, tmpfs and overlayfs;
        on a file system with one-second time stamps, a witness written in the
        second of `started` would be dropped."""
        spec = self.witnessSpec(actor)
        source = witness_dir if spec["directory"] == "run" else cwd
        if spec["files"] is None:
            candidates = self.witnessFiles(source)
        else:
            candidates = [os.path.join(source, file) for file in spec["files"]]

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

    def confirmVerdict(self, tool_acceptance_criteria, verdict: str, expected_verdict: str):
        if verdict.__contains__(expected_verdict):
            if tool_acceptance_criteria == "all" or tool_acceptance_criteria == expected_verdict:
                return True
            else:
                return False
    

    def runActor(self, actor: BaseTool2, step, group):
        """Runs the component `actor` of the config.Step `step` on the task in
        the ComponentGroup `group`, with the step's limits, and prints its
        block. Returns the Outcome of its verdict, with the witness files it
        wrote during this run, if `step.accept` accepts the verdict and `group`
        was not stopped, and NO_OUTCOME otherwise."""
        tool_location = os.path.join(os.getcwd(), "tools")
        tool_location = os.path.join(tool_location, self.tool_locations[actor.name()])

        tool_locator = BaseTool2.ToolLocator(tool_directory=tool_location)
        executable = actor.executable(tool_locator)

        cwd = str.rsplit(executable, "/", 1)[0]
        
        task = BaseTool2.Task.with_files(
            input_files=[self.file],
            property_file=self.property_file,
            options={"data_model": self.data_model,
                     "language": "C"},
        )

        if (actor.name() == "Goblint"):
            # The options of Goblint's own SV-COMP entry (benchexec_toolinfo_options
            # of version svcomp26 in its fm-tools file goblint.yml). The portfolio
            # path is relative to cwd, Goblint's directory. Goblint prints
            # "SV-COMP result: ..." only when run through this portfolio.
            # Keep in step with the Goblint version in tools.txt.
            options = ["--portfolio-conf", "conf/svcomp26/seq.txt"]
        elif (actor.name().__contains__("ULTIMATE")):
            options = ["--full-output"]
        else:
            options = []

        #A directory of this run, in which the component's witnesses end up
        witness_dir = tempfile.mkdtemp(prefix=actor.name().replace(" ", "_") + "-", dir=self.work_dir)
        options = options + self.witnessOptions(actor, witness_dir)

        cmdline = actor.cmdline(
            executable,
            options,
            task,
            BaseTool2.ResourceLimits()
        )

        started = self.startTime(witness_dir)
        tool_result = self.actorResult(
            command=self.withResourceLimits(step, cmdline),
            cwd=cwd,
            group=group
            )
        #Also for a stopped component, so that no file it wrote stays in its directory
        witness_files = self.collectWitnessFiles(actor, cwd, witness_dir, started)

        if group.stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used.
            #This check is also why componentStatus never gets a returncode of
            #None: actorResult returns None only when the group was already
            #stopped, and a group never becomes unstopped.
            self.printComponentRun(actor.name(), tool_result, "stopped by CoOpeRace", None)
            return NO_OUTCOME

        status = self.componentStatus(actor, cmdline, tool_result)
        verdict = status.lower()

        if self.confirmVerdict(step.accept, verdict, "true"):
            verdict = "true"
        elif self.confirmVerdict(step.accept, verdict, "false"):
            verdict = "false"
        else:
            verdict = "unknown"

        self.printComponentRun(actor.name(), tool_result, status, verdict)

        if verdict == "unknown":
            return NO_OUTCOME
        return Outcome(verdict, actor.name(), witness_files)

    def componentStatus(self, actor, cmdline, tool_result):
        """The status BenchExec would give this run of `actor` (a
        subprocess.CompletedProcess from actorResult): `actor.determine_result`
        on the output and the real exit code, and for an unspecific result
        (unknown, error or done) the refinement of benchexec.model, which
        names the signal that ended the component or, for an error, the exit
        code. A component that crashes is so reported as, for example,
        "EXCEPTION (SetDomain.Unsupported)" or "ERROR (1)", which
        confirmVerdict does not accept."""
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

    def printComponentRun(self, name, tool_result, status, verdict):
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
        with self.print_lock:
            print("\n".join(lines), flush=True)
    
    
