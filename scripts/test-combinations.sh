#!/bin/bash
#Run in root directory

shopt -s extglob
set -e

THIS_DIR=$(pwd)

RESULTS_DIR=$THIS_DIR/results

PARALLEL=6  # The limiting factor is RAM (32 on the server); 6 * 4Gb is okay for experiments

# read-only and overlay dirs for Value too large for defined data type workaround
BENCHEXEC="benchexec --read-only-dir / --overlay-dir . --overlay-dir /home --outputpath $RESULTS_DIR --numOfThreads $PARALLEL"
PYTHONPATH=$THIS_DIR:$PYTHONPATH

export PYTHONPATH=$PYTHONPATH:$THIS_DIR/tool_info

rm -rf $RESULTS_DIR  #for now, we want to start fresh
mkdir $RESULTS_DIR || true

cd $THIS_DIR

for i in {2..6}; do
    ./scripts/combination_n.sh "$i" "$BENCHEXEC"
done

cd $RESULTS_DIR
COOPERACE_WITNESS_DIR=$(echo cooperace.*.files)
echo "Cooperace witness directory:" $COOPERACE_WITNESS_DIR


# Generate table with merged results
cd $RESULTS_DIR
cp $THIS_DIR/tests/table-generator_combinations6.xml table-generator.xml
table-generator -x table-generator.xml

# Decompress all tool outputs for table HTML links
unzip -o '*.logfiles.zip'
