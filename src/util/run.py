class Run:
    def __init__(self, output):
        self.output = output

    def any_line_contains(self, substr):
        return any(substr in line for line in self.output)