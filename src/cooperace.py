from functools import partial
from multiprocessing.pool import ThreadPool
import os
import subprocess
import shutil


from benchexec.tools.template import BaseTool2
from benchexec.tools.goblint import Tool as Goblint
from benchexec.tools.dartagnan import Tool as Dartagnan
from benchexec.tools.deagle import Tool as Deagle
from benchexec.tools.ultimateautomizer import Tool as UltimateAutomizer
from benchexec.tools.ultimategemcutter import Tool as UltimateGemCutter

from .util.run import Run

from .util.tool_finder import ToolFinder

class Cooperace:
    def __init__(self, file, property_file, data_model, conf):
        self.file = file
        self.property_file = os.path.abspath(property_file)
        self.data_model = data_model
        self.conf = conf

        #Any new tools need to be added here
        self.tools = {
            "goblint": Goblint(),
            "deagle": Deagle(),
            "dartagnan": Dartagnan(),
            "uAutomizer": UltimateAutomizer(),
            "uGemCutter": UltimateGemCutter()
        }
        
    def actorResult(self, command, cwd):
        return subprocess.run(command,
                        cwd=cwd,
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
        
        return execution_type, execution_tools
        
    def execute(self):
        executon_type, execution_tools = self.parseConf()

        verdict = "unknown"

        if executon_type == "sequential":
            verdict = self.runSequential(execution_tools)
        elif executon_type == "parallel":
            verdict = self.runParallel(execution_tools)
        else:
            raise Exception("execution type in conf file is incorrect. Must be 'parallel' or 'sequential'")
        self.deleteAllWitnessFiles(self.witnessFiles(os.path.join(os.getcwd(), "tools")))
        return verdict
        

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

    def witnessFiles(self, tool_dir):
        witness_files = []
        
        for root, dirs, files in os.walk(tool_dir):
            for file in files:
                if 'witness' in file.lower():
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

    def runActor(self, actor: BaseTool2):
        tool_locator = ToolFinder()
        executable = actor.executable(tool_locator)

        cwd = str.rsplit(executable, "/", 1)[0]
        
        task = BaseTool2.Task.with_files(
            input_files=[self.file],
            property_file=self.property_file,
            options={"data_model":"ILP32",
                     "language": "C"},
        )

        if (actor.name() == "Goblint"):
            options = ["--conf", os.path.join(cwd, "conf", "svcomp24.json")]
        elif (actor.name().__contains__("ULTIMATE")):
            options = ["--full-output"]
        else:
            options = []


        cmdline = actor.cmdline(
            executable,
            options,
            task,
            None
        )

        tool_result = self.actorResult(
            command=cmdline,
            cwd=cwd
            )
        
        run = Run(
            output=tool_result.stdout,
            cmdline=cmdline
            )
        
        verdict = actor.determine_result(run).lower()

        if verdict.__contains__("true"):
            self.witnessFilesToFileRoot(self.witnessFiles(cwd))
            verdict = "true"
        elif verdict.__contains__("false"):
            self.witnessFilesToFileRoot(self.witnessFiles(cwd))
            verdict = "false"
        else:
            print("---STDOUT---\n")
            print(tool_result.stdout)
            print("---STDERR---\n")
            print(tool_result.stderr)
        
        return verdict
    


    