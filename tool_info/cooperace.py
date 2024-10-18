# This file is part of BenchExec, a framework for reliable benchmarking:
# https://github.com/sosy-lab/benchexec
#
# SPDX-FileCopyrightText: 2007-2020 Dirk Beyer <https://www.sosy-lab.org>
#
# SPDX-License-Identifier: Apache-2.0

import benchexec.tools.template
import benchexec.result as result

import re
import logging


class Tool(benchexec.tools.template.BaseTool2):
    """
    Tool info for CoOpeRace.
    """

    def executable(self, tool_locator):
        return tool_locator.find_executable("cooperace.sh")


    def name(self):
        return "CoOpeRace"


    def cmdline(self, executable, options, task, rlimits):
        if task.property_file:
            options += [task.property_file]
        return [executable, *options, *task.input_files]

    def determine_result(self, run):
        status = result.RESULT_ERROR
        if run.output:
            result_str = run.output[-1].strip()
            if "fail" in result_str:
                return result.RESULT_FALSE_PROP
            if "true" in result_str:
                return result.RESULT_TRUE_PROP
            if "unknown" in result_str:
                return result.RESULT_UNKNOWN

        return status
