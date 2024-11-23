#!/bin/sh
#run from root directory

#WORK IN PROGRESS

ROOT=`pwd`
DIST="dist/cooperace"
mkdir -p $DIST/tests

cp cooperace $DIST
git ls-files src | xargs -I {} cp --parents {} $DIST
cp -r tools $DIST/tools
cp -r conf $DIST
cp LICENSE README.md $DIST

cp tests/properties/no-data-race.prp $DIST/tests
cp tests/no-data-race/00-sanity_09-include.i $DIST/tests/test.i

cd $DIST/..
zip -r cooperace.zip cooperace
