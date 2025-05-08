#!/bin/bash
#Run in root directory

shopt -s extglob
set -e

THIS_DIR=$(pwd)


for i in 3; do #number represents the size of combinations to test, can also be a list of values.
    RESULTS_DIR=$THIS_DIR/results/results16GB_$i

    PARALLEL=2  # The limiting factor is RAM (32 on the server);

    # read-only and overlay dirs for Value too large for defined data type workaround
    BENCHEXEC="benchexec --read-only-dir / --overlay-dir . --overlay-dir /home --outputpath $RESULTS_DIR --numOfThreads $PARALLEL"
    PYTHONPATH=$THIS_DIR:$PYTHONPATH

    export PYTHONPATH=$PYTHONPATH:$THIS_DIR/tool_info

    rm -rf $RESULTS_DIR  #for now, we want to start fresh
    mkdir $RESULTS_DIR || true

    ./scripts/combination_n.sh "$i" "$BENCHEXEC"

    COOPERACE_WITNESS_DIR=$(echo cooperace.*.files)
    echo "Cooperace witness directory:" $COOPERACE_WITNESS_DIR

    echo "Generate table with merged results"
    cd "$RESULTS_DIR"
    cp "$THIS_DIR/tests/table-generator_combinations.xml" table-generator.xml
    table-generator -x table-generator.xml

    # Decompress all tool outputs for table HTML links
    unzip -o '*.logfiles.zip' -d "results_$i/"

    cd $THIS_DIR
done
