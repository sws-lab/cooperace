from collections import namedtuple
import os
import queue
import shutil
import signal
import tempfile
import threading
import traceback

from . import config
from .config import Sequence, Step
from .processes import ComponentGroup


# What runNode, runSequential, runParallel, runOne and runActor return:
# the verdict ("true", "false" or "unknown"), the name of the component that
# gave it (None for "unknown") and that component's witness files from this
# run.
Outcome = namedtuple("Outcome", "verdict component witness_files")
NO_OUTCOME = Outcome("unknown", None, [])


class StopSignal(BaseException):
    """Raised in the main thread by the handler that execute installs for
    SIGTERM, SIGINT and SIGHUP. A BaseException, so that the handlers for
    Exception on the way do not take it for a component's failure."""

    def __init__(self, signum):
        super().__init__(f"signal {signum}")
        self.signum = signum


class Strategy:
    def execute(self):
        """Runs the configuration and returns its verdict. No component is
        running when it returns. For a verdict of "true" or "false" it prints
        "CoOpeRace result from: <name>" last, naming the component whose
        verdict it returns, so that the launcher's verdict line follows it
        directly. If SIGTERM, SIGINT or SIGHUP arrives while it
        runs (in the main thread), it stops every component, prints that it was
        stopped, and ends CoOpeRace with that signal, without a verdict.

        The conf is loaded with config.load first, and an error it raises
        propagates, except config.RunTypeError: for that it removes the old
        witness files, prints "Error, something went wrong: <error>" and the
        traceback, and returns "unknown", as for any other error of the run."""
        try:
            root = config.load(self.conf, self.tools)
        except config.RunTypeError as error:
            self.removeOldWitnessFiles()
            print("Error, something went wrong:", error)
            traceback.print_exc()
            return "unknown"

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
        component = None
        stopped_by = None
        self.removeOldWitnessFiles()
        #Holds one directory per component run, made in runActor
        self.work_dir = tempfile.mkdtemp(prefix="cooperace-")
        try:
            outcome = self.runNode(root, self.root_group)
            #Only the witness of the component whose verdict is returned
            self.witnessFilesToFileRoot(outcome.witness_files)
            verdict = outcome.verdict
            component = outcome.component
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
        if verdict == "true" or verdict == "false":
            print(f"CoOpeRace result from: {component}", flush=True)
        return verdict


    def runNode(self, node, group):
        """Runs the config.Node `node` in the ComponentGroup `group`: a Step
        with runOne, a Sequence with runSequential, a Parallel with
        runParallel. Returns its Outcome."""
        if isinstance(node, Step):
            return self.runOne(node, group)
        if isinstance(node, Sequence):
            return self.runSequential(node, group)
        return self.runParallel(node, group)

    def runSequential(self, sequence, group):
        """Runs the steps of the config.Sequence `sequence` one after another
        in the ComponentGroup `group`, and returns the Outcome of the first
        accepted verdict, or NO_OUTCOME if there is none or `group` is stopped
        first."""
        for node in sequence.steps:
            if group.stopped:
                break
            outcome = self.runNode(node, group)

            if outcome.verdict == "true" or outcome.verdict == "false":
                return outcome

        return NO_OUTCOME

    def runParallel(self, parallel, parent):
        """Runs the steps of the config.Parallel `parallel` at the same time,
        each in a thread of its own (a Sequence runs there with
        runSequential), in a new ComponentGroup nested in the group `parent`.
        Returns the Outcome of the first accepted verdict that a thread
        reports, or NO_OUTCOME once every thread has reported none. Before
        returning it stops the group, which ends the components still running,
        and joins every thread, so that no component of the group runs or
        prints afterwards."""
        group = ComponentGroup(parent)
        outcomes = queue.Queue()

        def runBranch(node):
            #Every branch puts exactly one outcome, also when it ends with a
            #BaseException (such as SystemExit from a module), which is
            #printed and counts as no verdict; otherwise the wait below would
            #never end. StopSignal is raised in the main thread only.
            outcome = NO_OUTCOME
            try:
                outcome = self.runNode(node, group)
            except BaseException:
                traceback.print_exc()
            finally:
                outcomes.put(outcome)

        threads = [threading.Thread(target=runBranch, args=(node,)) for node in parallel.steps]
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
