from collections import namedtuple
import os
import queue
import signal
import subprocess
import shutil
import glob
import sys
import importlib
import threading
import time
import traceback

for whl_file in glob.glob("lib/*.whl"):
    sys.path.insert(0, whl_file)

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


class StopSignal(BaseException):
    """Raised in the main thread by the handler that execute installs for
    SIGTERM, SIGINT and SIGHUP. A BaseException, so that the handlers for
    Exception on the way do not take it for a component's failure."""

    def __init__(self, signum):
        super().__init__(f"signal {signum}")
        self.signum = signum


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

    def currentGroup(self):
        return getattr(self.local, "group", self.root_group)

    def actorResult(self, command, cwd):
        """Runs `command` in `cwd` as the leader of a new session, so that the
        component and every process it starts form one process group, which
        ComponentGroup.stop can end, and records it in the current thread's
        group while it runs. Returns a subprocess.CompletedProcess with the
        captured output; `returncode` is negative if the component was ended by
        a signal, and None if the group was stopped before it could start."""
        group = self.currentGroup()
        if group.stopped:
            return subprocess.CompletedProcess(command, None, "", "")
        process = subprocess.Popen(command,
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        errors="replace",
                        start_new_session=True
                        )
        try:
            if not group.add(process):
                stopProcessGroups([process])
            stdout, stderr = process.communicate()
        except BaseException:
            #StopSignal while this thread (the main thread) waits
            stopProcessGroups([process])
            process.wait()
            raise
        finally:
            group.remove(process)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

        
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
        try:
            if executon_type == "sequential":
                verdict = self.runSequential(execution_tools).verdict
            elif executon_type == "parallel":
                verdict = self.runParallel(execution_tools).verdict
            else:
                raise Exception("execution type in conf file is incorrect. Must be 'parallel' or 'sequential'")
            self.deleteAllWitnessFiles(self.witnessFiles(os.path.join(os.getcwd(), "tools")))
        except StopSignal as stop:
            stopped_by = stop.signum
        except Exception as error:
            print("Error, something went wrong:", error)
            traceback.print_exc()
        finally:
            ignoreStopSignals()
            self.root_group.stop()
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
        verdict = self.runActor(actor)
        if verdict == "true" or verdict == "false":
            return Outcome(verdict, actor.name(), [])
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
        for file in witness_files:
            if file.endswith("graphml"):
                destination = os.path.join(os.getcwd(), "witness.graphml")
            else:
                destination = os.path.join(os.getcwd(), os.path.basename(file))
            shutil.copy2(file, destination)

    def deleteAllWitnessFiles(self, witness_files):
        for file in witness_files:
            os.remove(file)

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
            options = ["--conf", os.path.join(cwd, "conf", "svcomp26", "verify.json")]
        elif (actor.name().__contains__("ULTIMATE")):
            options = ["--full-output"]
        else:
            options = []


        cmdline = actor.cmdline(
            executable,
            options,
            task,
            BaseTool2.ResourceLimits()
        )

        tool_result = self.actorResult(
            command=cmdline,
            cwd=cwd
            )

        if self.currentGroup().stopped:
            #Another component's verdict was returned, or CoOpeRace is stopping:
            #the component was ended or never started, and its result is not used
            print("Tool name:", actor.name(), "Status: stopped by CoOpeRace")
            return "unknown"

        run = BaseTool2.Run(
            cmdline=cmdline,
            exit_code=butil.ProcessExitCode.create(value=0),
            output=BaseTool2.RunOutput(tool_result.stdout.strip().split("\n")),
            termination_reason=""
        )
        
        verdict = actor.determine_result(run).lower()

        if self.confirmVerdict(actor.name(), verdict, "true"):
            self.witnessFilesToFileRoot(self.witnessFiles(cwd))
            verdict = "true"
        elif self.confirmVerdict(actor.name(), verdict, "false"):
            self.witnessFilesToFileRoot(self.witnessFiles(cwd))
            verdict = "false"
        else:
            verdict = "unknown"
            print(f"---{actor.name()} logs---\n")
            print(tool_result.stdout)
            print(tool_result.stderr)

        print("Tool name:", actor.name(), "Result:", verdict)
        
        return verdict
    
    
