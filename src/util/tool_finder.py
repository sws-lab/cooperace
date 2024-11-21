import os


class ToolFinder:
    def find_executable(self, name):
        #Ultimate framework tools all have the same executable file name, so they need a more specific search
        if name.__contains__("ULTIMATE"):
            ultimate_tools_dict = {"Automizer": "UAutomizer-linux",
                                   "GemCutter": "UGemCutter-linux"}
            tool_dir = os.path.join(os.getcwd(), "tools", ultimate_tools_dict[name.split(" ")][1])
        else:
            tool_dir = os.path.join(os.getcwd(), "tools")

        for directory in os.listdir(tool_dir):
            path = os.path.join(tool_dir, directory)
            for file in os.listdir(path):
                if file == name:
                    return os.path.join(path, file)
        
        return None
    