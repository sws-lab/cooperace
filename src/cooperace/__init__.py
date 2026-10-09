import glob
import sys

for whl_file in glob.glob("lib/*.whl"):
    sys.path.insert(0, whl_file)
