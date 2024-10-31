import os


class tool_finder:
    def find_executable(self, name):
        tools = os.path.join(os.getcwd(), "tools")
        for directory in os.listdir(tools):
            path = os.path.join(tools, directory)
            for file in os.listdir(path):
                if file == name:
                    return os.path.join(path, file)
        
        return None