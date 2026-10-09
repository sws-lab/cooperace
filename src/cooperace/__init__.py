"""CoOpeRace: runs data-race verifiers ("components") on a task, as a
configuration (conf/*.json) says, and returns the first verdict it accepts.

The modules, each importing only modules listed after it: cli (the command
line), components (the registry of components, running one and reading its
result and witness; the only module that imports BenchExec), strategy
(running the tree), config (the conf as a tree of steps), processes
(starting and stopping the components' processes).

Importing the package puts the bundled BenchExec wheels, lib/*.whl, on
sys.path, by paths relative to the working directory, which must therefore
be the directory of the launcher `cooperace`; components.py imports
BenchExec after this has run.
"""
import glob
import sys

for whl_file in glob.glob("lib/*.whl"):
    sys.path.insert(0, whl_file)
