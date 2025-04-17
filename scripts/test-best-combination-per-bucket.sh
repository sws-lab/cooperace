#!/bin/bash
#Run in root directory

shopt -s extglob
set -e

THIS_DIR=$(pwd)

RESULTS_DIR=$THIS_DIR/results/results_best

PARALLEL=2

# read-only and overlay dirs for Value too large for defined data type workaround
BENCHEXEC="benchexec --read-only-dir / --overlay-dir . --overlay-dir /home --outputpath $RESULTS_DIR --numOfThreads $PARALLEL"
PYTHONPATH=$THIS_DIR:$PYTHONPATH

export PYTHONPATH=$PYTHONPATH:$THIS_DIR/tool_info

rm -rf $RESULTS_DIR  #for now, we want to start fresh
mkdir $RESULTS_DIR || true

#Run best from bucket 2 (dartagnan_goblint)
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations_best/combination_2_5.xml
cd $THIS_DIR

#Run best from bucket 3 (deagle_dartagnan_goblint)
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations_best/combination_3_5.xml
cd $THIS_DIR

#Run best from bucket 4 (nacpa_deagle_dartagnan_goblint)
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations_best/combination_4_3.xml
cd $THIS_DIR

#Run best from bucket 5 (uautomizer_sv-sanitizers_deagle_dartagnan_goblint)
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations_best/combination_5_2.xml
cd $THIS_DIR

#Run best from bucket 6 (nacpa_sv-sanitizers_deagle_dartagnan_ugemcutter_goblint)
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations_best/combination_6_3.xml
cd $THIS_DIR

COOPERACE_WITNESS_DIR=$(echo cooperace.*.files)
echo "Cooperace witness directory:" $COOPERACE_WITNESS_DIR

echo "Generate table with merged results"
cd "$RESULTS_DIR"
cp "$THIS_DIR/tests/table-generator_combinations.xml" table-generator.xml
table-generator -x table-generator.xml

# Decompress all tool outputs for table HTML links
unzip -o '*.logfiles.zip' -d "results_$i/"

