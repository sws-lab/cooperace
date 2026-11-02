#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# Builds dist/cooperace.zip, the SV-COMP archive, from the commit HEAD of the
# repository this script is in; it can be run from any directory.
#
#   FMTOOLS_VERSION=svcomp27 scripts/svcomp-dist.sh
#
# The archive holds the tracked files cooperace, src/, conf/, lib/, LICENSE, the
# GPL texts of LICENSES/, README.md, THIRD-PARTY-SOURCES.md, tools.txt and tools-options.json, the smoke-test script and the
# two files it runs on, exactly as committed, and the components that
# tools.txt names, copied from tools/<name> with their record .doi. The
# components of tools-pool.txt are not packed. The file VERSION in it holds
# the fm-tools version name (FMTOOLS_VERSION, or the first argument) and
# `git describe --always --dirty`, which `cooperace --version` prints.
#
# The script refuses to run while tracked files have uncommitted changes.
# ALLOW_DIRTY=1 overrides that for development: the archive is then made from
# the tracked files as they are in the working tree, and its VERSION ends in
# "-dirty". Never submit such an archive.
#
# WITNESS_MODE selects the witness table the archive carries as
# conf/witnesses.json (conf/README.md, "The witness table"): "default" (or
# unset) keeps conf/witnesses.json, which accepts a true witness of format 2.0
# in unreach-call and no-overflow; "strict" packs conf/witness-modes/strict.json
# in its place, which accepts 2.1 or higher there, as the SV-COMP 2027
# benchmarks page states, and ends VERSION with "witnesses-strict".

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FMTOOLS_VERSION="${1:-${FMTOOLS_VERSION:-fmtools-version-unset}}"
WITNESS_MODE="${WITNESS_MODE:-default}"
case "${WITNESS_MODE}" in
  default|strict) ;;
  *) echo "WITNESS_MODE must be default or strict, not ${WITNESS_MODE}" >&2; exit 1 ;;
esac
DIST="${ROOT}/dist/cooperace"

TREE=HEAD
if ! git diff --quiet HEAD --; then
  if [ "${ALLOW_DIRTY:-}" != 1 ]; then
    echo "Refusing to build: tracked files have uncommitted changes:" >&2
    git status --short --untracked-files=no >&2
    echo "Commit them, or set ALLOW_DIRTY=1 for a development archive." >&2
    exit 1
  fi
  echo "!!!!!!!! ALLOW_DIRTY=1: this archive holds uncommitted changes. DO NOT SUBMIT IT. !!!!!!!!" >&2
  TREE="$(git stash create)"
fi
DESCRIBE="$(git describe --always --dirty)"

rm -rf "${DIST}"
mkdir -p "${DIST}/tools"

git archive "${TREE}" cooperace src conf lib LICENSE LICENSES/GPL-2.0-only.txt LICENSES/GPL-3.0-or-later.txt \
  README.md THIRD-PARTY-SOURCES.md tools.txt tools-options.json \
  tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i \
  | tar -x -C "${DIST}"
git show "${TREE}:scripts/sv-comp/smoketest.sh" > "${DIST}/smoketest.sh"
chmod +x "${DIST}/smoketest.sh"

# tools.txt, the lock file of the components, has one "<name>: <doi>" line per
# component. tools/ must hold exactly those DOIs, as download-tools.py records,
# and tools-options.json the options of those DOIs.
python3 scripts/download-tools.py --check
while IFS=: read -r name _; do
  name="${name//[[:space:]]/}"
  case "${name}" in ''|'#'*) continue ;; esac
  if [ ! -d "tools/${name}" ]; then
    echo "tools.txt names ${name}, but tools/${name} does not exist. Run scripts/download-tools.py." >&2
    exit 1
  fi
  cp -a "tools/${name}" "${DIST}/tools/${name}"
done < "${DIST}/tools.txt"

if [ "${WITNESS_MODE}" = strict ]; then
  cp "${DIST}/conf/witness-modes/strict.json" "${DIST}/conf/witnesses.json"
  echo "${FMTOOLS_VERSION} ${DESCRIBE} witnesses-strict" > "${DIST}/VERSION"
else
  echo "${FMTOOLS_VERSION} ${DESCRIBE}" > "${DIST}/VERSION"
fi
find "${DIST}" -name '.DS_Store' -delete

# The same commit gives the same zip: every file gets the commit's time, and
# the entries are sorted.
EPOCH="$(git log -1 --format=%ct "${TREE}")"
# `touch -d @EPOCH` is GNU-only, so the time is passed as `touch -t` local time,
# formatted with GNU `date -d @` or, on macOS, BSD `date -r`.
STAMP="$(date -d "@${EPOCH}" +%Y%m%d%H%M.%S 2>/dev/null || date -r "${EPOCH}" +%Y%m%d%H%M.%S)"
find "${DIST}" -exec touch -h -t "${STAMP}" {} +
(
  cd "${DIST}/.."
  rm -f cooperace.zip
  find cooperace -print | LC_ALL=C sort | TZ=UTC zip -X -q -@ cooperace.zip
)
echo "Built ${ROOT}/dist/cooperace.zip: $(cat "${DIST}/VERSION")"
