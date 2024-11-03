#!/usr/bin/env bash
#Run in root directory

# Check if tools directory exists
if [ -d "tools" ]; then
    # Prompt for confirmation to delete the contents
    read -p "The tools directory already exists. Do you want to delete its contents? (Y/n): " confirmation

    if [[ "$confirmation" == "y" || "$confirmation" == "Y" || "$confirmation" == "" ]]; then
        echo "Cleaning up existing tools directory..."
        rm -rf tools/*
    else
        echo "Operation canceled. Exiting script."
        exit 0
    fi
else
    echo "Creating tools directory..."
    mkdir tools
fi

mkdir tmp

#Get Dartagnan

curl "https://zenodo.org/records/10161362/files/dartagnan.zip?download=1" -o tmp/dartagnan.zip

unzip -o tmp/dartagnan.zip -d tools/

rm -rf tmp/dartagnan.zip

#Get Goblint


curl "https://zenodo.org/records/10202867/files/goblint.zip?download=1" -o tmp/goblint.zip

unzip -o tmp/goblint.zip -d tools/

rm -rf tmp/goblint.zip


#Get Deagle

curl "https://zenodo.org/records/10207348/files/deagle.zip?download=1" -o tmp/deagle.zip

unzip -o tmp/deagle.zip -d tools/

rm -rf tmp/deagle.zip

#After downloading all required tools

rm -r tmp/
