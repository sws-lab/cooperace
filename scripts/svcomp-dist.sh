#!/bin/sh
#run from root directory

#WORK IN PROGRESS

ROOT=`pwd`
DIST="dist/cooperace"
mkdir -p $DIST

cp cooperace $DIST
git ls-files src | xargs -I {} cp --parents {} $DIST
cp -r tools $DIST/tools
cp LICENSE README.md $DIST

cd $DIST/..
zip -r cooperace.zip cooperace
