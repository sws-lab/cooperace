#!/bin/bash
#Run in root directory

shopt -s extglob
set -euo pipefail

THIS_DIR=$(pwd)

SIZES=(3)  # sizes of the combinations to test; a list of values is allowed.

for i in "${SIZES[@]}"; do
    RESULTS_DIR=$THIS_DIR/results/results16GB_$i

    PARALLEL=2  # The limiting factor is RAM (32 on the server);

    # read-only and overlay dirs for Value too large for defined data type workaround
    BENCHEXEC=(benchexec --read-only-dir / --overlay-dir . --overlay-dir /home --outputpath "$RESULTS_DIR" --numOfThreads "$PARALLEL")

    rm -rf "$RESULTS_DIR"  #for now, we want to start fresh
    mkdir -p "$RESULTS_DIR"

    ./scripts/combination_n.sh "$i" "${BENCHEXEC[@]}"

    COOPERACE_WITNESS_DIR=$(echo cooperace.*.files)
    echo "Cooperace witness directory:" "$COOPERACE_WITNESS_DIR"

    echo "Generate table with merged results"
    cd "$RESULTS_DIR"
    cp "$THIS_DIR/tests/table-generator_combinations.xml" table-generator.xml
    table-generator -x table-generator.xml

    # Decompress all tool outputs for table HTML links
    unzip -o '*.logfiles.zip' -d "results_$i/"

    cd "$THIS_DIR"
done
