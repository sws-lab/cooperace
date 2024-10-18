#!/bin/bash

properties=$1

file=$2

THIS_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

export COOPERACE_DARTAGNAN=$THIS_DIR/tools/dartagnan
export COOPERACE_GOBLINT=$THIS_DIR/tools/goblint

cmd="python3 src/main.py --properties_file $properties --filepath $file"


$cmd
