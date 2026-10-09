#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# Runs CoOpeRace on its own smoke-test task from the root of the unpacked
# archive, and fails unless the task gets the verdict it is known to have:
# the last line of the output is exactly "CoOpeRace verdict: false" (the line
# BenchExec's tool-info module reads) and a well-formed witness.graphml is
# written to the working directory.

set -euo pipefail

PROPERTY="tests/properties/no-data-race.prp"
TASK="tests/no-data-race/00-sanity_09-include.i"
EXPECTED="CoOpeRace verdict: false"

echo "== CoOpeRace version =="
./cooperace --version

echo
echo "== Running smoke test scenario =="
rm -f witness.graphml
STATUS=0
OUTPUT="$(./cooperace --prop "${PROPERTY}" "${TASK}")" || STATUS=$?
echo "${OUTPUT}"

if [ "${STATUS}" -ne 0 ]; then
  echo "Smoke test failed: cooperace exited with status ${STATUS}." >&2
  exit 1
fi

LAST_LINE="$(tail -n 1 <<< "${OUTPUT}")"
if [ "${LAST_LINE}" != "${EXPECTED}" ]; then
  echo "Smoke test failed: the last line of the output is '${LAST_LINE}', expected '${EXPECTED}'." >&2
  exit 1
fi

if [ ! -s witness.graphml ]; then
  echo "Smoke test failed: witness.graphml was not written." >&2
  exit 1
fi

if ! python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' witness.graphml; then
  echo "Smoke test failed: witness.graphml is not well-formed XML." >&2
  exit 1
fi

echo "== SMOKETEST SUCCESSFUL! =="
