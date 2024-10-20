#!/bin/bash
#Run in root directory

shopt -s extglob
set -e

THIS_DIR=`pwd`

RESULTS_DIR=$THIS_DIR/results

DARTAGNAN_DIR=$THIS_DIR/tools/dartagnan
GOBLINT_DIR=$THIS_DIR/tools/goblint

PARALLEL=6  # The limiting factor is RAM (32 on the server); 6 * 4Gb is okay for experiments

# read-only and overlay dirs for Value too large for defined data type workaround
BENCHEXEC="benchexec --read-only-dir / --overlay-dir . --overlay-dir /home --outputpath $RESULTS_DIR --numOfThreads $PARALLEL"
PYTHONPATH=$THIS_DIR:$PYTHONPATH

export PYTHONPATH=$PYTHONPATH:$THIS_DIR/tool_info

rm -rf $RESULTS_DIR  #for now, we want to start fresh
mkdir $RESULTS_DIR || true

echo "Running Dartagnan"
cd $DARTAGNAN_DIR
$BENCHEXEC $THIS_DIR/tests/bench-defs/dartagnan.xml

echo "Running Goblint"
cd $GOBLINT_DIR
$BENCHEXEC $THIS_DIR/tests/bench-defs/goblint.xml

echo "Running Cooperace (uses local tool-info module)"
cd $THIS_DIR
$BENCHEXEC $THIS_DIR/tests/bench-defs/cooperace.xml


# Generate table with merged results
cd $RESULTS_DIR
cp $THIS_DIR/tests/table-generator.xml table-generator.xml
table-generator -x table-generator.xml

# Decompress all tool outputs for table HTML links
unzip -o '*.logfiles.zip'
