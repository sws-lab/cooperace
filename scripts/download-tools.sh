#!/usr/bin/env bash
#Run in root directory

mkdir tmp

#Get Dartagnan

curl "https://zenodo.org/records/10161362/files/dartagnan.zip?download=1" -o tmp/dartagnan.zip

unzip -o tmp/dartagnan.zip -d tools/

rm -rf tmp/dartagnan.zip

#Get Goblint


curl "https://zenodo.org/records/10202867/files/goblint.zip?download=1" -o tmp/goblint.zip

unzip -o tmp/goblint.zip -d tools/

rm -rf tmp/goblint.zip


#After downloading all required tools

rm -r tmp/
