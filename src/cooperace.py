from collections import namedtuple
import os
import queue
import signal
import subprocess
import shutil
import glob
import sys
import importlib
import tempfile
import threading
import time
import traceback

for whl_file in glob.glob("lib/*.whl"):
    sys.path.insert(0, whl_file)

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



# What runSequential, runParallel and runActorThread return: the verdict
# ("true", "false" or "unknown"), the name of the component that gave it (None
# for "unknown") and that component's witness files from this run.
Outcome = namedtuple("Outcome", "verdict component witness_files")
NO_OUTCOME = Outcome("unknown", None, [])

# Seconds a component's process group has to exit after SIGTERM before
# stopProcessGroups sends it SIGKILL.
STOP_GRACE_SECONDS = 1.0


def processExited(pid):
    """Whether process `pid` has exited, that is, is gone or a zombie, read
    from /proc. A zombie counts as exited because the thread waiting for it in
    actorResult may not have reaped it yet."""
    try:
        with open(f"/proc/{pid}/stat") as stat_file:
            stat = stat_file.read()
    except OSError:
        return True
    return stat[stat.rindex(")") + 2] == "Z"


def stopProcessGroups(processes):
    """Ends each of `processes`, which actorResult started as leaders of new
    sessions, together with every other process of its process group: SIGTERM
    to each group, up to STOP_GRACE_SECONDS for the leaders to exit, then
    SIGKILL to each group. The SIGKILL also ends descendants that are still
    running after their leader has exited, such as the JVMs that
    Dartagnan-SVCOMP.sh and Ultimate.py start. A descendant that starts a
    session of its own is not reached. Does not reap the leaders; the threads
    waiting in actorResult do."""
    def signalGroup(process, signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    for process in processes:
        signalGroup(process, signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and not all(processExited(p.pid) for p in processes):
        time.sleep(0.05)
    for process in processes:
        signalGroup(process, signal.SIGKILL)


class ComponentGroup:
    """The component processes started under one runParallel call, and the
    groups of the runParallel calls nested in it. Cooperace.root_group holds
    the components started outside any runParallel. Once stop() is called,
    actorResult starts no further component in the group or its subgroups."""

    def __init__(self, parent=None):
        self.lock = threading.Lock()
        self.stopped = False
        self.processes = set()
        self.subgroups = []
        if parent is not None:
            parent.addSubgroup(self)

    def addSubgroup(self, group):
        with self.lock:
            self.subgroups.append(group)
            stopped = self.stopped
        if stopped:
            group.stop()

    def add(self, process):
        """Records a running component process. Returns False, and records
        nothing, if the group is already stopped."""
        with self.lock:
            if self.stopped:
                return False
            self.processes.add(process)
            return True

    def remove(self, process):
        with self.lock:
            self.processes.discard(process)

    def markStopped(self):
        """Marks this group and its subgroups stopped and returns the processes
        running in them."""
        with self.lock:
            self.stopped = True
            processes = list(self.processes)
            subgroups = list(self.subgroups)
        for group in subgroups:
            processes += group.markStopped()
        return processes

    def stop(self):
        """Stops every component running in this group or its subgroups with
        stopProcessGroups and keeps further ones from starting."""
        stopProcessGroups(self.markStopped())


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


class StopSignal(BaseException):
    """Raised in the main thread by the handler that execute installs for
    SIGTERM, SIGINT and SIGHUP. A BaseException, so that the handlers for
    Exception on the way do not take it for a component's failure."""

    def __init__(self, signum):
        super().__init__(f"signal {signum}")
        self.signum = signum


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

class Cooperace:
    def __init__(self, file, property_file, data_model, conf):
        self.file = file
        self.property_file = os.path.abspath(property_file)
        self.data_model = data_model
        self.conf = conf
        self.acceptable_results = {}

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
        #Per thread: `group`, the ComponentGroup the thread starts components in
        self.local = threading.local()
        self.print_lock = threading.Lock()

    def currentGroup(self):
        return getattr(self.local, "group", self.root_group)

    def actorResult(self, command, cwd):
        """Runs `command` in `cwd` as the leader of a new session, so that the
        component and every process it starts form one process group, which
        ComponentGroup.stop can end, and records it in the current thread's
        group while it runs. Returns a subprocess.CompletedProcess whose
        `stdout` is the component's standard output and standard error in one,
        as BenchExec captures them, and whose `stderr` is empty; `returncode`
        is negative if the component was ended by a signal, and None if the
        group was stopped before it could start."""
        group = self.currentGroup()
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

        
    def parseTools(self, tools):
        executable_tools = []
        for tool in tools:
            if isinstance(tool, list):
                executable_tools.append(self.parseTools(tool))
            else:
                for tool_name, tool_value in tool.items():
                    self.acceptable_results[tool_name] = tool_value
                    executable_tools.append(self.tools[tool_name])

        print(executable_tools)

        return executable_tools
            
        
    def parseConf(self):
        execution_type = self.conf["runType"]
        execution_tools = self.parseTools(self.conf["tools"])
        
        return execution_type, execution_tools
        
    def execute(self):
        """Runs the configuration and returns its verdict. No component is
        running when it returns. If SIGTERM, SIGINT or SIGHUP arrives while it
        runs (in the main thread), it stops every component, prints that it was
        stopped, and ends CoOpeRace with that signal, without a verdict."""
        executon_type, execution_tools = self.parseConf()

        handlers = {}

        def ignoreStopSignals():
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)

        def raiseStopSignal(signum, frame):
            #A second signal must not interrupt the stopping of the components
            ignoreStopSignals()
            raise StopSignal(signum)

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                if signal.getsignal(signum) != signal.SIG_IGN:
                    handlers[signum] = signal.signal(signum, raiseStopSignal)

        verdict = "unknown"
        stopped_by = None
        self.removeOldWitnessFiles()
        #Holds one directory per component run, made in runActor
        self.work_dir = tempfile.mkdtemp(prefix="cooperace-")
        try:
            if executon_type == "sequential":
                outcome = self.runSequential(execution_tools)
            elif executon_type == "parallel":
                outcome = self.runParallel(execution_tools)
            else:
                raise Exception("execution type in conf file is incorrect. Must be 'parallel' or 'sequential'")
            #Only the witness of the component whose verdict is returned
            self.witnessFilesToFileRoot(outcome.witness_files)
            verdict = outcome.verdict
        except StopSignal as stop:
            stopped_by = stop.signum
        except Exception as error:
            print("Error, something went wrong:", error)
            traceback.print_exc()
        finally:
            ignoreStopSignals()
            self.root_group.stop()
            shutil.rmtree(self.work_dir, ignore_errors=True)
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        if stopped_by is not None:
            print(f"CoOpeRace stopped by signal {stopped_by}", flush=True)
            signal.signal(stopped_by, signal.SIG_DFL)
            os.kill(os.getpid(), stopped_by)
        return verdict


    def runSequential(self, actors=None):
        """Runs the elements of `actors` one after another, a list element with
        runParallel, and returns the Outcome of the first accepted verdict, or
        NO_OUTCOME if there is none or the current thread's group is stopped
        first."""
        for actor in actors:
            if self.currentGroup().stopped:
                break
            #If actor is a list, then we want the list of tools to be run in parallel
            if isinstance(actor, list):
                outcome = self.runParallel(actor)
            else:
                outcome = self.runOne(actor)

            if outcome.verdict == "true" or outcome.verdict == "false":
                return outcome

        return NO_OUTCOME

    def runOne(self, actor):
        """Runs `actor` with runActor and returns its Outcome. runActor sets
        `witness_files` of the current thread for an accepted verdict."""
        self.local.witness_files = []
        verdict = self.runActor(actor)
        if verdict == "true" or verdict == "false":
            return Outcome(verdict, actor.name(), self.local.witness_files)
        return NO_OUTCOME

    def runActorThread(self, actor):
        #If actor in parallel running is a list, then that list should be run sequentially
        if isinstance(actor, list):
            return self.runSequential(actor)
        else:
            return self.runOne(actor)

    def runParallel(self, actors=None):
        """Runs the elements of `actors` at the same time, each in a thread of
        its own (a list element runs there with runSequential), in a new
        ComponentGroup nested in the current thread's group. Returns the
        Outcome of the first accepted verdict that a thread reports, or
        NO_OUTCOME once every thread has reported none. Before returning it
        stops the group, which ends the components still running, and joins
        every thread, so that no component of the group runs or prints
        afterwards."""
        group = ComponentGroup(self.currentGroup())
        outcomes = queue.Queue()

        def runBranch(actor):
            self.local.group = group
            try:
                outcome = self.runActorThread(actor)
            except Exception:
                traceback.print_exc()
                outcome = NO_OUTCOME
            outcomes.put(outcome)

        threads = [threading.Thread(target=runBranch, args=(actor,)) for actor in actors]
        for thread in threads:
            thread.start()

        outcome = NO_OUTCOME
        try:
            for _ in threads:
                reported = outcomes.get()
                if reported.verdict == "true" or reported.verdict == "false":
                    outcome = reported
                    break
        finally:
            group.stop()
            #Also when a StopSignal ends the wait, so that the stopped
            #components have printed their lines before CoOpeRace ends
            for thread in threads:
                thread.join()

        return outcome

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
        touched."""
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

    def confirmVerdict(self, tool_name, verdict: str, expected_verdict: str):
        tool_acceptance_criteria = self.acceptable_results.get(tool_name, None)

        if verdict.__contains__(expected_verdict):
            if tool_acceptance_criteria is None or tool_acceptance_criteria == "all" or tool_acceptance_criteria == expected_verdict:
                return True
            else:
                return False
    

    def runActor(self, actor: BaseTool2):
        tool_location = os.path.join(os.getcwd(), "tools")
        tool_location = os.path.join(tool_location, self.tool_locations[actor.name()])

        tool_locator = BaseTool2.ToolLocator(tool_directory=tool_location)
        executable = actor.executable(tool_locator)

        cwd = str.rsplit(executable, "/", 1)[0]
        
        task = BaseTool2.Task.with_files(
            input_files=[self.file],
            property_file=self.property_file,
            options={"data_model":"ILP32",
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
            command=cmdline,
            cwd=cwd
            )
        #Also for a stopped component, so that no file it wrote stays in its directory
        witness_files = self.collectWitnessFiles(actor, cwd, witness_dir, started)

        if self.currentGroup().stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used
            self.printComponentRun(actor.name(), tool_result, "stopped by CoOpeRace", None)
            return "unknown"

        status = self.componentStatus(actor, cmdline, tool_result)
        verdict = status.lower()

        if self.confirmVerdict(actor.name(), verdict, "true"):
            self.local.witness_files = witness_files
            verdict = "true"
        elif self.confirmVerdict(actor.name(), verdict, "false"):
            self.local.witness_files = witness_files
            verdict = "false"
        else:
            verdict = "unknown"

        self.printComponentRun(actor.name(), tool_result, status, verdict)

        return verdict

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
    
    
