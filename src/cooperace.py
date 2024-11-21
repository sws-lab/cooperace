from functools import partial
from multiprocessing.pool import ThreadPool
import os
import subprocess


from benchexec.tools.template import BaseTool2
from benchexec.tools.goblint import Tool as Goblint
from benchexec.tools.dartagnan import Tool as Dartagnan
from benchexec.tools.deagle import Tool as Deagle

from .util.run import Run

from .util.tool_finder import ToolFinder

class Cooperace:
    def __init__(self, file, property_file, data_model, conf):
        self.file = file
        self.property_file = property_file
        self.data_model = data_model
        self.conf = conf

        #Any new tools need to be added here
        self.tools = {
            "goblint": Goblint(),
            "deagle": Deagle(),
            "dartagnan": Dartagnan()
        }
        
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
        
    def parseTools(self, tools):
        executable_tools = []
        for tool in tools:
            if isinstance(tool, list):
                executable_tools.append(self.parseTools(tool))
            else:
                executable_tools.append(self.tools[tool])

        return executable_tools
            
        
    def parseConf(self):
        execution_type = self.conf["runType"]
        execution_tools = self.parseTools(self.conf["tools"])

        print(execution_tools)
        
        return execution_type, execution_tools
        
    def execute(self):
        executon_type, execution_tools = self.parseConf()

        if (executon_type == "sequential"):
            return self.runSequential(execution_tools)
            
        

    def runSequential(self, actors=None):
        verdict = "unknown"
        
        for actor in actors:
            #If actor is a list, then we want the list of tools to be run in parallel
            if isinstance(actor, list):
                actor_result = self.runParallel(actor)
            else:
                actor_result = self.runActor(actor)


            if actor_result == "true" or actor_result == "false":
                return actor_result
            else:
                verdict = actor_result

        return verdict
    
    def runActorThread(self, actor):
        #If actor in parallel running is a list, then that list should be run sequentially
        if isinstance(actor, list):
            return self.runSequential(actor)
        else:
            return self.runActor(actor)
    
    def runParallel(self, actors=None):
        verdict = "unknown"

        with ThreadPool() as pool:    
            it = pool.imap_unordered(partial(self.runActorThread), actors)
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
    