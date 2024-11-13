import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from multiprocessing.pool import ThreadPool
import subprocess
import re
import os
import multiprocessing



from .util.run import Run

from .actors.task import Task

from .util.tool_finder import tool_finder
from .actors.goblint import Goblint

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
            while value == "unknown":
                value = next(it)
            verdict = value

        return verdict


    def runActor(self, actor):
        verdict = "unknown"

        try:
            tool_locator = tool_finder()
            path = actor.executable(tool_locator)

            cwd, executable = os.path.split(path)

            task_options = {"data_model": self.data_model, "language": "C"} #Language is hardcoded, maybe should get from user input?

            task = Task.with_files(
                input_files=[self.file],
                options=task_options,
                property_file=self.property_file
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

            print(command)

            tool_result = self.actorResult(actor, command, cwd)
            
            run = Run(tool_result.stdout.strip().split("\n"))
            verdict = actor.determine_result(run).lower()
            print(actor.name(), "verdict:",verdict)
            if verdict == "unknown" or verdict == "error":
                print(actor.name(), "result inconclusive")
                print("STDOUT:\n")
                print(tool_result.stdout)
                print("\nSTDERR:\n")
                print(tool_result.stderr)
            else:
                return verdict
        except:
            print(actor.name(), "result inconclusive")
            print("STDOUT:\n")
            print(tool_result.stdout)
            print("\nSTDERR:\n")
            print(tool_result.stderr)
            pass
        
        return verdict
    