# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""BenchExec tool-info modules that the bundled BenchExec wheel
(lib/benchexec-*.whl) does not have, or has in an older version, each copied
unchanged from the BenchExec revision that the SV-COMP bench-defs pin, so that
CoOpeRace reads the component's output as the competition does. Each keeps
BenchExec's license header. dartagnan.py adds one rule of CoOpeRace's at its
end, in a section of its own. Remove a module here once the bundled wheel has
it (and, for dartagnan.py, keep its CoOpeRace rule)."""
