class Task:
    def __init__(self, input_files, property_file, options):
        self.input_files = input_files
        self.property_file = property_file
        self.options = options
        self.input_files_or_identifier = input_files
        self.single_input_file = input_files[0]