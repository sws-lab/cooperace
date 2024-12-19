#!/bin/bash

# Check for required arguments
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <source_directory> <destination_directory> <property_file_path>"
    exit 1
fi

# Arguments
SOURCE_DIR=$1
DEST_DIR=$2
PROPERTY_FILE_PATH=$3

# Extract only the file name from the property file path
PROPERTY_FILE=$(basename "$PROPERTY_FILE_PATH")

# Ensure the destination directory exists
mkdir -p "$DEST_DIR"

# Find all .yml files and process them
find "$SOURCE_DIR" -type f -name '*.yml' | while read -r yml_file; do
    # Check if the .yml file contains the specific property line (not commented out)
    if grep -q "^[^#]*- property_file: .*${PROPERTY_FILE}" "$yml_file"; then
        # Copy the .yml file to the destination directory
        cp "$yml_file" "$DEST_DIR"

        # Extract the corresponding .i or .c file from the yml_file
        source_file=$(grep -oP "(?<=input_files: ').*\.(i|c)(?=')" "$yml_file")

        # If the source file exists, copy it too
        if [ -n "$source_file" ] && [ -f "$(dirname "$yml_file")/$source_file" ]; then
            cp "$(dirname "$yml_file")/$source_file" "$DEST_DIR"
        else
            echo "Warning: Corresponding .i or .c file not found for $yml_file"
        fi
    fi
done
