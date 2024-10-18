#!/bin/sh
#run from root directory

#WORK IN PROGRESS

ROOT=`pwd`
DIST="dist/cooperace"
mkdir -p $DIST

cp cooperace.sh $DIST
cp -r src $DIST/src
cp -r tools $DIST/tools

cd $DIST/..
zip -r cooperace.zip cooperace
