from functools import partial
from multiprocessing.pool import ThreadPool
import os
import subprocess


from benchexec.tools.template import BaseTool2

from .util.run import Run

from .util.tool_finder import ToolFinder

class Cooperace:
    def __init__(self, file, property_file, data_model):
        self.file = file
        self.property_file = property_file
        self.data_model = data_model
        
    def actorResult(self, actor, command, cwd):
        if actor.name() != "Goblint":
            return subprocess.run(command,
                            cwd=cwd,
                            capture_output=True,
                            text=True  
                            )
        else:
            return subprocess.run(command,
                            capture_output=True,
                            text=True  
                            )
            
        

    def runSequential(self, actors=None):
        verdict = "unknown"
        
        for actor in actors:
            actor_result = self.runActor(actor)
            if actor_result == "true" or actor_result == "false":
                return actor_result
            else:
                verdict = actor_result

        return verdict
    
    def runParallel(self, actors=None):
        verdict = "unknown"

        with ThreadPool() as pool:
            it = pool.imap_unordered(partial(self.runActor), actors)

            value = next(it)
            print(value)
            try:
                while value != "true" and value != "false":
                    value = next(it)
                verdict = value
            except StopIteration:
                return "unknown"

        return verdict


    def runActor(self, actor: BaseTool2):
        tool_locator = ToolFinder()
        executable = actor.executable(tool_locator)

        cwd = str.rsplit(executable, "/", 1)[0]
        
        task = BaseTool2.Task.with_files(
            input_files=[self.file],
            property_file=self.property_file,
            options={"data_model":"ILP32"},
        )

        if (actor.name() == "Goblint"):
            options = ["--conf", os.path.join(cwd, "conf", "svcomp24.json")]
        else:
            options = []

        cmdline = actor.cmdline(
            executable=executable,
            options=options,
            task=task,
            rlimits=None
        )

        tool_result = self.actorResult(
            actor=actor,
            command=cmdline,
            cwd=cwd
            )
        
        run = Run(
            output=tool_result.stdout
            )
        
        verdict = actor.determine_result(run).lower()

        if verdict.__contains__("true"):
            return "true"
        elif verdict.__contains__("false"):
            return "false"
        else:
            print("---STDOUT---\n")
            print(tool_result.stdout)
            print("---STDERR---\n")
            print(tool_result.stderr)
        
        return verdict
    