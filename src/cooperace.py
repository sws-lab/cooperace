from functools import partial
from multiprocessing.pool import ThreadPool
import os
import subprocess
import shutil
import glob
import sys
import importlib
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
        executon_type, execution_tools = self.parseConf()

        verdict = "unknown"
        try:
            if executon_type == "sequential":
                verdict = self.runSequential(execution_tools)
            elif executon_type == "parallel":
                verdict = self.runParallel(execution_tools)
            else:
                raise Exception("execution type in conf file is incorrect. Must be 'parallel' or 'sequential'")
            self.deleteAllWitnessFiles(self.witnessFiles(os.path.join(os.getcwd(), "tools")))
        except Exception as error:
            print("Error, something went wrong:", error)
            traceback.print_exc()
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
    
    
