import subprocess
import re
import os

class Cooperace:
    def __init__(self, file, properties_file, data_model):
        self.file = file
        self.properties_file = properties_file
        self.data_model = data_model

    def parse_svcomp_result_goblint(self, output):
        match = re.search(r"SV-COMP result:\s*(\w+)", output)

        if match:
            return match.group(1)
        else:
            return "error"

    def useGoblint(self):
        print("Starting Goblint...")
        options = ["--conf", "conf/svcomp24.json",
                   "--set", "ana.specification", self.properties_file]
        data_model_option = {"ILP32": "32bit", "LP64": "64bit"}.get(self.data_model)
        if data_model_option:
            options += ["--set", "exp.architecture", data_model_option]
        print("Options: ", options)
        result = subprocess.run(["tools/goblint/goblint",
                        *options,
                        self.file],
                        capture_output=True, text=True)

        svcomp_result = self.parse_svcomp_result_goblint(result.stdout)
        print("Goblint result: ", svcomp_result)
        if (svcomp_result != "true" and svcomp_result != "false"):
            print("STDOUT:\n")
            print(result.stdout)
            print("\nSTDERR:\n")
            print(result.stderr)
            return "error"
        else:
            return svcomp_result

    def useDartagnan(self):
        print("Starting Dartagnan...")
        result = subprocess.run(["bash", "Dartagnan-SVCOMP.sh",
                                self.properties_file,
                                self.file],
                                cwd="tools/dartagnan",
                                capture_output=True, text=True 
                                )
        try:
            svcomp_result = result.stdout.strip().splitlines()[-1]
            return svcomp_result
        except:
            print("STDOUT:\n")
            print(result.stdout)
            print("\nSTDERR:\n")
            print(result.stderr)
            return "error"

    def start(self):
        goblint_result = self.useGoblint().lower()
        verdict = "unknown"

        if goblint_result == "unknown" or goblint_result == "error":
            print("Goblint result inconclusive")
            dartagnan_result = self.useDartagnan().lower()
            verdict = dartagnan_result
        else:
            verdict = goblint_result

        return verdict
