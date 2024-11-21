class Run:
    def __init__(self, output, cmdline):
        self.output = Output(output.strip().split("\n"))
        self.exit_code = ExitCode(0)
        self.cmdline = cmdline

class Output:
    def __init__(self, output):
        self.output = output
    def any_line_contains(self, substr):
        return any(substr in line for line in self.output)
    def __iter__(self):
        return iter(self.output)
    def __getitem__(self, index):
        return self.output[index]

class ExitCode:
    def __init__(self, value):
        self.value = value