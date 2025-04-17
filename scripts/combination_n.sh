#!/bin/bash

THIS_DIR=$(pwd)

BENCHEXEC=$2

echo "Running Cooperace combination $1-1"
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations$1/combination_1.xml

echo "Running Cooperace combination $1-2"
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations$1/combination_2.xml

echo "Running Cooperace combination $1-3"
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations$1/combination_3.xml

echo "Running Cooperace combination $1-4"
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations$1/combination_4.xml

echo "Running Cooperace combination $1-5"
$BENCHEXEC $THIS_DIR/tests/bench-defs/combinations$1/combination_5.xml

