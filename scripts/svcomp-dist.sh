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
# GPL texts of LICENSES/, README.md, THIRD-PARTY-SOURCES.md, tools.txt,
# tools-options.json and tools-checkers.txt, the smoke-test script and the two
# files it runs on, and the witnesses of the linter's self-test
# (tests/witnesslint), as committed except where CONF_VARIANT and WITNESS_MODE
# replace conf/svcomp27.json, tools.txt and conf/witnesses.json (below); the
# components that tools.txt names, copied from tools/<name> with their record
# .doi; and the checkers of tools-checkers.txt (WitnessLint). The components
# of tools-pool.txt are packed only where CONF_VARIANT runs them. The file VERSION in it holds
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
#
# CONF_VARIANT selects the conf the archive carries as conf/svcomp27.json, the
# one ./cooperace runs by default: a name NAME packs conf/svcomp27-NAME.json in
# its place, adds to the archive's tools.txt, and packs, each component of
# tools-pool.txt that this conf runs and tools.txt lacks (Theta, for "theta"),
# and ends VERSION with "conf-NAME". tools/ must hold those components at the
# DOI of tools-pool.txt (scripts/download-tools.py --pool). "default" keeps
# conf/svcomp27.json, the conf of the bracket mode.
#
# The packed conf must be a submission conf (scripts/sv-comp/submission-conf.py:
# every property in the first-verdict mode, no vetoWindows), so CONF_VARIANT
# unset or "default" is refused, as is a variant in the bracket mode.
# ALLOW_BRACKET_CONF=1 builds such an archive, for comparison with the bracket
# mode; its VERSION ends in "bracket-conf". Never submit such an archive.

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
CONF_VARIANT="${CONF_VARIANT:-default}"
case "${CONF_VARIANT}" in
  default) ;;
  *[!a-z0-9-]*|'') echo "CONF_VARIANT must be default or the NAME of a conf/svcomp27-NAME.json, not ${CONF_VARIANT}" >&2; exit 1 ;;
esac
if [ "${CONF_VARIANT}" = default ] && [ "${ALLOW_BRACKET_CONF:-}" != 1 ]; then
  echo "Refusing to build: CONF_VARIANT is unset or default, which packs conf/svcomp27.json, a conf of the" >&2
  echo "bracket mode. Set CONF_VARIANT to the NAME of the submission conf conf/svcomp27-NAME.json, or" >&2
  echo "ALLOW_BRACKET_CONF=1 for an archive with the bracket conf, which is not submitted." >&2
  exit 1
fi

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
  README.md THIRD-PARTY-SOURCES.md tools.txt tools-options.json tools-checkers.txt \
  tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i \
  tests/witnesslint/program.c tests/witnesslint/valid.witness.yml tests/witnesslint/invalid.witness.yml \
  | tar -x -C "${DIST}"
git show "${TREE}:scripts/sv-comp/smoketest.sh" > "${DIST}/smoketest.sh"
chmod +x "${DIST}/smoketest.sh"

if [ "${CONF_VARIANT}" != default ]; then
  VARIANT_CONF="${DIST}/conf/svcomp27-${CONF_VARIANT}.json"
  if [ ! -f "${VARIANT_CONF}" ]; then
    echo "CONF_VARIANT=${CONF_VARIANT}: conf/svcomp27-${CONF_VARIANT}.json is not in the commit." >&2
    exit 1
  fi
  cp "${VARIANT_CONF}" "${DIST}/conf/svcomp27.json"
  # The components of tools-pool.txt that the variant's conf runs and tools.txt
  # lacks, as "<name>: <doi>" lines, by the registry of the archive's own code.
  git show "${TREE}:tools-pool.txt" > "${DIST}/.tools-pool.txt"
  python3 - "${DIST}" "${DIST}/conf/svcomp27.json" "${DIST}/.tools-pool.txt" >> "${DIST}/tools.txt" <<'PY'
import json
import sys

sys.path.insert(0, sys.argv[1])
from src.cooperace import config
from src.cooperace.components import REGISTRY

def lock(path):
    entries = {}
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, doi = (part.strip() for part in line.partition(":"))
            entries[name] = doi
    return entries

archive, pool = lock(sys.argv[1] + "/tools.txt"), lock(sys.argv[3])
strategies = config.load_strategies(json.load(open(sys.argv[2])), REGISTRY)
needed = sorted({REGISTRY[name].directory for strategy in strategies.values() for name in strategy.tools})
for directory in needed:
    if directory in archive:
        continue
    if directory not in pool:
        sys.exit(f"the conf runs {directory}, which neither tools.txt nor tools-pool.txt names")
    print(f"{directory}: {pool[directory]}")
PY
  rm -f "${DIST}/.tools-pool.txt"
  echo "CONF_VARIANT=${CONF_VARIANT}: conf/svcomp27-${CONF_VARIANT}.json packed as conf/svcomp27.json; tools.txt:" >&2
  cat "${DIST}/tools.txt" >&2
fi

# The packed conf must be a submission conf, unless ALLOW_BRACKET_CONF=1.
BRACKET_CONF=0
CONF_CHECK=0
python3 -B scripts/sv-comp/submission-conf.py "${DIST}" || CONF_CHECK=$?
if [ "${CONF_CHECK}" != 0 ]; then
  if [ "${CONF_CHECK}" != 1 ]; then
    echo "Refusing to build: the packed conf/svcomp27.json cannot be checked (above)." >&2
    exit 1
  fi
  if [ "${ALLOW_BRACKET_CONF:-}" != 1 ]; then
    echo "Refusing to build: the packed conf/svcomp27.json is no submission conf (above)." >&2
    echo "ALLOW_BRACKET_CONF=1 builds it anyway, for an archive that is not submitted." >&2
    exit 1
  fi
  BRACKET_CONF=1
  echo "!!!!!!!! ALLOW_BRACKET_CONF=1: this archive runs a conf of the bracket mode. DO NOT SUBMIT IT. !!!!!!!!" >&2
fi

# tools.txt, the lock file of the components, has one "<name>: <doi>" line per
# component. tools/ must hold exactly those DOIs, as download-tools.py records,
# and tools-options.json the options of those DOIs.
python3 scripts/download-tools.py --check
while IFS=: read -r name doi; do
  name="${name//[[:space:]]/}"
  doi="${doi//[[:space:]]/}"
  case "${name}" in ''|'#'*) continue ;; esac
  if [ ! -d "tools/${name}" ]; then
    echo "tools.txt names ${name}, but tools/${name} does not exist. Run scripts/download-tools.py." >&2
    exit 1
  fi
  # download-tools.py --check covers tools.txt; a component a variant adds is checked here
  if [ "$(cat "tools/${name}/.doi" 2>/dev/null)" != "${doi}" ]; then
    echo "tools/${name} is not installed from ${doi}. Run scripts/download-tools.py --pool." >&2
    exit 1
  fi
  cp -a "tools/${name}" "${DIST}/tools/${name}"
done < "${DIST}/tools.txt"
# The checkers of tools-checkers.txt (WitnessLint), which download-tools.py --check has checked too.
while IFS=: read -r name _; do
  name="${name//[[:space:]]/}"
  case "${name}" in ''|'#'*) continue ;; esac
  if [ ! -d "tools/${name}" ]; then
    echo "tools-checkers.txt names ${name}, but tools/${name} does not exist. Run scripts/download-tools.py." >&2
    exit 1
  fi
  cp -a "tools/${name}" "${DIST}/tools/${name}"
done < "${DIST}/tools-checkers.txt"

VERSION_TEXT="${FMTOOLS_VERSION} ${DESCRIBE}"
if [ "${CONF_VARIANT}" != default ]; then
  VERSION_TEXT="${VERSION_TEXT} conf-${CONF_VARIANT}"
fi
if [ "${BRACKET_CONF}" = 1 ]; then
  VERSION_TEXT="${VERSION_TEXT} bracket-conf"
fi
if [ "${WITNESS_MODE}" = strict ]; then
  cp "${DIST}/conf/witness-modes/strict.json" "${DIST}/conf/witnesses.json"
  VERSION_TEXT="${VERSION_TEXT} witnesses-strict"
fi
echo "${VERSION_TEXT}" > "${DIST}/VERSION"
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
