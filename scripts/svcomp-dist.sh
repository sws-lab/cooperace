#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# Builds dist/cooperace.zip, the SV-COMP archive, from the commit HEAD of the
# repository this script is in; it can be run from any directory.
#
#   FMTOOLS_VERSION=svcomp27 scripts/svcomp-dist.sh
#
# The archive holds the tracked files cooperace, src/, conf/, lib/, LICENSE,
# README.md, tools.txt and tools-options.json, the smoke-test script and the
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

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FMTOOLS_VERSION="${1:-${FMTOOLS_VERSION:-fmtools-version-unset}}"
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

git archive "${TREE}" cooperace src conf lib LICENSE README.md tools.txt tools-options.json \
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

echo "${FMTOOLS_VERSION} ${DESCRIBE}" > "${DIST}/VERSION"
find "${DIST}" -name '.DS_Store' -delete

# The same commit gives the same zip: every file gets the commit's time, and
# the entries are sorted.
EPOCH="$(git log -1 --format=%ct "${TREE}")"
find "${DIST}" -exec touch -h -d "@${EPOCH}" {} +
(
  cd "${DIST}/.."
  rm -f cooperace.zip
  find cooperace -print | LC_ALL=C sort | TZ=UTC zip -X -q -@ cooperace.zip
)
echo "Built ${ROOT}/dist/cooperace.zip: $(cat "${DIST}/VERSION")"
