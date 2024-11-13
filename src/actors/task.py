from collections import namedtuple
import copy

from .template import UnsupportedFeatureException


class Task(
        namedtuple(
            "Task", ["input_files_or_empty", "identifier", "property_file", "options"]
        )
    ):
        """
        Represent the task for which the tool should be executed in a run.
        While this class is technically a tuple,
        this should be seen as an implementation detail and the order of elements in the
        tuple should not be considered. New fields may be added in the future.

        Explanation of fields:
        input_files_or_empty: ordered sequence of paths to input files (or directories),
            each relative to the tool's working directory;
            guaranteed to be of type collections.abc.Sequence;
            it is recommended to access input_files or input_files_or_identifier instead
        identifier: name of task when <withoutfile> is used to define the task,
            i.e., when the list of input files is empty, None otherwise
        property_file: path to property file if one is used (relative to the tool's
            working directory) or None otherwise
        options: content of the "options" key in the task-definition file (if present)
        """

        def __new__(cls, input_files, identifier, property_file, options):
            input_files = tuple(input_files)  # make input_files immutable
            assert bool(input_files) != bool(identifier), (
                f"exactly one is required: " f"{input_files=!r} {identifier=!r}"
            )
            options = copy.deepcopy(options)  # defensive copy because not immutable
            return super().__new__(cls, input_files, identifier, property_file, options)

        @classmethod
        def with_files(cls, input_files, *, property_file=None, options=None):
            return cls(
                input_files=input_files,
                identifier=None,
                property_file=property_file,
                options=options,
            )

        @classmethod
        def without_files(cls, identifier, *, property_file=None, options=None):
            return cls(
                input_files=[],
                identifier=identifier,
                property_file=property_file,
                options=options,
            )

        @property
        def input_files(self):
            """
            Return sequence of input files or raise appropriate exception if the task
            has no input files.
            """
            self.require_input_files()
            return self.input_files_or_empty

        @property
        def single_input_file(self):
            """
            Return string with the single given input file, or raise appropriate
            exception if there is not exactly one input file.
            """
            self.require_input_files()
            self.require_single_input_file()
            return self.input_files_or_empty[0]

        @property
        def input_files_or_identifier(self):
            """
            Return either the sequence of input files or a one-element sequence with the
            identifier. Useful for adding either to the command line arguments.
            """
            return self.input_files_or_empty or (self.identifier,)

        def require_input_files(self):
            """
            Check that there is at least one path in input_files and raise appropriate
            exception otherwise
            """
            if not self.input_files_or_empty:
                raise UnsupportedFeatureException(
                    "Tool does not support tasks without input files"
                )

        def require_single_input_file(self):
            """
            Check that there is not more than one path in input_files and raise
            appropriate exception otherwise.
            """
            if len(self.input_files_or_empty) > 1:
                raise UnsupportedFeatureException(
                    "Tool does not support tasks with more than one input file"
                )
