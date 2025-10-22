#!/usr/bin/env bash

set -euo pipefail

echo "== CoOpeRace version =="
./cooperace --version

echo
echo "== Running smoke test scenario =="
OUTPUT="$(./cooperace --conf conf/svcomp26.json --prop tests/no-data-race.prp tests/test.i)"
echo "${OUTPUT}"

if ! grep -q "CoOpeRace verdict:" <<< "${OUTPUT}"; then
  echo "Smoke test failed: verdict not found in output." >&2
  exit 1
fi

if grep -q "Error, something went wrong" <<< "${OUTPUT}"; then
  echo "Smoke test failed: tool execution reported an error." >&2
  exit 1
fi
