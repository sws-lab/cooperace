#!/bin/bash

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# Usage: combination_n.sh N BENCHEXEC_COMMAND [ARGUMENT...]
# Runs the five combinations of size N with the given BenchExec command line.
# Run in the root directory.

set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 N BENCHEXEC_COMMAND [ARGUMENT...]" >&2
    exit 2
fi

THIS_DIR=$(pwd)

N=$1
shift
BENCHEXEC=("$@")

for k in 1 2 3 4 5; do
    echo "Running Cooperace combination $N-$k"
    "${BENCHEXEC[@]}" "$THIS_DIR/tests/bench-defs/combinations${N}_16GB/combination_$k.xml"
done
