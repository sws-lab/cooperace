import subprocess
import re
import os

from .util.run import Run

from .actors.task import Task

from .util.tool_finder import tool_finder
from .actors.goblint import Goblint

class Cooperace:
    def __init__(self, file, properties_file, data_model):
        self.file = file
        self.properties_file = properties_file
        self.data_model = data_model
        
    def runActor(self, actor, command, cwd):
        if actor.name() == "Dartagnan":
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
            tool_locator = tool_finder()
            path = actor.executable(tool_locator)

            cwd, executable = os.path.split(path)

            task_options = {"data_model": self.data_model}

            task = Task(
                input_files=[self.file],
                property_file=self.properties_file,
                options=task_options
            )

            if actor.name() == "Goblint":
                options = ["--conf", os.path.join(cwd, "conf", "svcomp24.json")]
            else:
                options = []

            command = actor.cmdline(
                executable=path,
                options=options,
                task=task,
                rlimits=None
            )

            tool_result = self.runActor(actor, command, cwd)
            
            run = Run(tool_result.stdout.split("\n"))
            verdict = actor.determine_result(run).lower()

            if verdict == "unknown" or verdict == "error":
                print(actor.name(), "result inconclusive")
                print("STDOUT:\n")
                print(tool_result.stdout)
                print("\nSTDERR:\n")
                print(tool_result.stderr)
            else:
                return verdict

        return verdict
