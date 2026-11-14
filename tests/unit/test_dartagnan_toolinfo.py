# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""How CoOpeRace starts Dartagnan and reads its result
(src/cooperace/toolinfo/dartagnan.py): as BenchExec e7b7d4a4 does, which ran
Dartagnan 10.5281/zenodo.24134559 in the SV-COMP 2027 pre-run, except that
a plain "false" is "ERROR". The outputs are the closing lines of Dartagnan
svcomp27 (10.5281/zenodo.23897433, Dat3M 05d5bdf; 23263361, 6b3b464, prints
the same) on sv-benchmarks tasks, run on goblint on 8 November 2026; the
result rules are the same in e7b7d4a4 as in fbd04593."""
import subprocess

import pytest
from support import STUB_DOI

from src.cooperace import components
from src.cooperace.components import REGISTRY

# The warning Dartagnan prints early in every run whose program calls a
# function it does not model; it contains "unknown functions".
UNKNOWN_FUNCTIONS_WARNING = ("[08.11.2026] 11:16:44 [WARN] LibraryLinker$MissingLibrary.link - Unknown functions "
                             "strcpy. Detecting calls to unknown functions requires --property=program_spec.")

# pthread/bigshot_p.i, no-data-race (expected false)
RACE = [
    UNKNOWN_FUNCTIONS_WARNING,
    "[08.11.2026] 11:16:44 [INFO] AssumeSolver.verify - Verification finished with result FAIL",
    "Test: /home/cwd/sv-benchmarks/c/pthread/bigshot_p.i",
    "Result: FAIL",
    "Reason: CAT specification violation found",
    "Details:",
    "\tFlag data-race",
    "\t@bigshot_p.i#1111 / @bigshot_p.i#1116\t(E58 / E67)",
    "\t@bigshot_p.i#1116 / @bigshot_p.i#1111\t(E67 / E58)",
    "Witness: /opt/cooperace/tools/dartagnan/output/witness.yml",
    "Time: 0.015 secs",
]

# goblint-regression/04-mutex_35-trylock_rc.i, no-data-race: Dartagnan stops at the call of
# time(), which it does not model, and reports that as a violation, with one thread
UNKNOWN_FUNCTION = [
    "[08.11.2026] 12:14:05 [INFO] AssumeSolver.verify - Verification finished with result FAIL",
    "Test: /home/cwd/sv-benchmarks/c/goblint-regression/04-mutex_35-trylock_rc.i",
    "Result: FAIL",
    "Reason: Program specification violation found",
    "Details:",
    "\tT0:main -> @04-mutex_35-trylock_rc.i#1839\t(E2): Calling unknown function time",
    "Witness: /opt/cooperace/tools/dartagnan/output/witness.yml",
    "Time: 0.007 secs",
]

# A FAIL with a reason that no rule of the module recognizes
UNRECOGNIZED_FAIL = [
    "Result: FAIL",
    "Reason: Some other violation found",
    "Witness: /opt/cooperace/tools/dartagnan/output/witness.yml",
]

PASS = [UNKNOWN_FUNCTIONS_WARNING, "Result: PASS", "Time: 1.2 secs"]

USER_ASSERTION = ["Result: FAIL", "Reason: user assertion violation found", "Details:"]


def status(lines, returncode=0):
    """The status component_status gives Dartagnan's run with output `lines`."""
    tool_result = subprocess.CompletedProcess(["Dartagnan-SVCOMP.sh"], returncode, "\n".join(lines) + "\n", "")
    return components.component_status(REGISTRY["Dartagnan"].tool(), ["Dartagnan-SVCOMP.sh"], tool_result)


def test_dartagnan_s_tool_info_module_is_cooperace_s_copy():
    assert REGISTRY["Dartagnan"].module == "src.cooperace.toolinfo.dartagnan"
    assert REGISTRY["Dartagnan"].tool().name() == "Dartagnan"


@pytest.mark.parametrize("data_model", ["ILP32", "LP64"])
def test_dartagnan_is_given_the_data_model_of_the_task(tmp_path, make_runner, data_model):
    """Dartagnan 24134559 compiles with -m32 for --data-model=ILP32 and with
    -m64 for LP64, which is also what it does without the option; BenchExec
    e7b7d4a4 passes the task's data model so, after the input file."""
    executable = tmp_path / "dartagnan" / "Dartagnan-SVCOMP.sh"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    runner = make_runner(data_model)
    spec = components.ComponentSpec("Dartagnan", REGISTRY["Dartagnan"].module, str(tmp_path / "dartagnan"),
                                    witness=REGISTRY["Dartagnan"].witness)
    runner.versions[spec.directory] = components.ComponentVersion(STUB_DOI, "svcomp27", ())

    cmdline = runner.command(spec.tool(), spec, "/w").cmdline

    assert cmdline[0] == str(tmp_path / "dartagnan" / "Dartagnan-SVCOMP.sh")
    assert cmdline[1:] == [runner.property_file, "/dev/null", f"--data-model={data_model}"]


def test_a_race_reported_as_flag_data_race_is_false_no_data_race():
    assert status(RACE) == "false(no-data-race)"
    assert components.confirm_verdict("all", status(RACE), "false", "no-data-race")


def test_a_call_of_an_unknown_function_is_an_error():
    assert status(UNKNOWN_FUNCTION) == "ERROR"


def test_a_fail_that_no_rule_recognizes_is_an_error_not_a_plain_false():
    assert status(UNRECOGNIZED_FAIL) == "ERROR"
    assert status(UNRECOGNIZED_FAIL, returncode=1) == "ERROR (1)"


def test_the_unknown_functions_warning_alone_does_not_decide_the_result():
    assert status(PASS) == "true"


def test_a_user_assertion_is_false_unreach_call():
    assert status(USER_ASSERTION) == "false(unreach-call)"


@pytest.mark.parametrize("lines", [UNKNOWN_FUNCTION, UNRECOGNIZED_FAIL], ids=["unknown-function", "unrecognized"])
@pytest.mark.parametrize("property_name", ["no-data-race", "unreach-call", "no-overflow", "valid-memsafety"])
def test_no_dartagnan_output_that_bench_exec_reads_as_a_plain_false_reaches_a_verdict(lines, property_name):
    """BenchExec's module reads both outputs as a plain "false", which CoOpeRace
    accepts as the verdict false of every property but valid-memsafety
    (components.ACCEPTED_STATUSES); CoOpeRace's copy reads them as ERROR, which
    no property accepts."""
    for verdict in ("true", "false"):
        for acceptance in ("all", "true", "false"):
            assert not components.confirm_verdict(acceptance, status(lines), verdict, property_name)
