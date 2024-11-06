from concurrent.futures import ThreadPoolExecutor, as_completed
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

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.runActor, actors[i]) for i in range(len(actors))]

            for future in as_completed(futures):
                result = future.result()

                if result == "true" or result == "false":
                    for f in futures:
                        f.cancel()

                    return result
                
        return verdict


    def runActor(self, actor):
        verdict = "unknown"

        try:
            tool_locator = tool_finder()
            path = actor.executable(tool_locator)

            cwd, executable = os.path.split(path)

            task_options = {"data_model": self.data_model, "language": "C"} #Language is hardcoded, maybe should get from user input?

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
