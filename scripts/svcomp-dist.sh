#!/usr/bin/env bash
# run from repository root

set -euo pipefail

ROOT="$(pwd)"
DIST="${ROOT}/dist/cooperace"

rm -rf "${DIST}"
mkdir -p "${DIST}/tests"

cp "${ROOT}/cooperace" "${DIST}"
rsync -a --files-from=<(git ls-files src) "${ROOT}/" "${DIST}"
cp -r tools "${DIST}/tools"
cp -r conf lib "${DIST}"
cp LICENSE README.md tools.txt "${DIST}"
cp scripts/sv-comp/smoketest.sh "${DIST}"
chmod +x "${DIST}/smoketest.sh"

cp tests/properties/no-data-race.prp "${DIST}/tests"
cp tests/no-data-race/00-sanity_09-include.i "${DIST}/tests/test.i"

(
  cd "${DIST}/.."
  rm -f cooperace.zip
  zip -r cooperace.zip cooperace
)
