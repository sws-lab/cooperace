# This file is part of BenchExec, a framework for reliable benchmarking:
# https://github.com/sosy-lab/benchexec
#
# SPDX-FileCopyrightText: 2007-2020 Dirk Beyer <https://www.sosy-lab.org>
#
# SPDX-License-Identifier: Apache-2.0

# Copied unchanged (below this comment, down to the CoOpeRace section at the end) by CoOpeRace
# from benchexec/tools/dartagnan.py of BenchExec fbd04593, the revision that SV-COMP 2027's
# bench-defs (c0b5532) pin. The bundled wheel (BenchExec 3.35) predates two of its result
# rules: "Flag data-race", the line with which Dartagnan svcomp27 reports a race, gives
# false(no-data-race), and "unknown function" gives ERROR. With 3.35's module both ended as a
# plain "false".
import benchexec.tools.template
from benchexec import result
from benchexec.tools.sv_benchmarks_util import (
    TaskFilesConsidered,
    handle_witness_of_task,
)


class Tool(benchexec.tools.template.BaseTool2):
    """
    Tool info for Dartagnan.
    """

    def executable(self, tool_locator):
        return tool_locator.find_executable("Dartagnan-SVCOMP.sh")

    def name(self):
        return "Dartagnan"

    def project_url(self):
        return "https://github.com/hernanponcedeleon/Dat3M"

    def cmdline(self, executable, options, task, rlimits):
        if task.property_file:
            options += [task.property_file]
        input_files, witness_options = handle_witness_of_task(
            task, options, "-witness", TaskFilesConsidered.INPUT_FILES_OR_IDENTIFIER
        )
        return [executable] + options + witness_options + input_files

    def version(self, executable):
        return self._version_from_tool(executable)

    def determine_result(self, run):
        status = result.RESULT_ERROR
        for line in run.output:
            if "unsupported property" in line or "unknown function" in line:
                status = result.RESULT_ERROR
            elif "FAIL" in line:
                status = result.RESULT_FALSE_PROP
            elif "PASS" in line:
                status = result.RESULT_TRUE_PROP
            elif "integer overflow" in line:
                status = result.RESULT_FALSE_OVERFLOW
            elif "invalid dereference" in line:
                status = result.RESULT_FALSE_DEREF
            elif "user assertion" in line:
                status = result.RESULT_FALSE_REACH
            elif "data race found" in line or "Flag data-race" in line:
                status = result.RESULT_FALSE_DATARACE
            elif "Untrackable object found" in line:
                status = result.RESULT_FALSE_MEMTRACK
            elif "Termination violation found" in line:
                status = result.RESULT_FALSE_TERMINATION
        return status

    def program_files(self, executable):
        return [executable] + self.REQUIRED_PATHS


# --- CoOpeRace: no plain "false" ----------------------------------------------
# BenchExec's module gives a plain "false" (RESULT_FALSE_PROP) for a "FAIL" whose reason it
# does not recognize. CoOpeRace would accept that as a verdict of every property without
# sub-properties (components.ACCEPTED_STATUSES). It is never the competition's correct
# answer for a property Dartagnan runs on in CoOpeRace: each of these has a reason line of
# its own above. Here it is ERROR instead.
_BenchExecTool = Tool


class Tool(_BenchExecTool):
    """BenchExec fbd04593's tool-info module for Dartagnan, except that a plain
    "false" is "ERROR"."""

    def determine_result(self, run):
        status = super().determine_result(run)
        if status == result.RESULT_FALSE_PROP:
            return result.RESULT_ERROR
        return status
