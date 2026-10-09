"""CoOpeRace: runs verifiers ("components") on a task, as the strategy of a
configuration (conf/*.json) for the task's property says, and returns the
first verdict it accepts.

The modules, each importing only modules listed after it: cli (the command
line), components (the registry of components, running one and reading its
result and witness; the only module that imports BenchExec), strategy
(running the tree), config (the conf as a tree of steps per property),
properties (the properties and their recognition in a property file),
processes (starting and stopping the components' processes).

CoOpeRace runs from any working directory. TOOL_DIR, the directory that holds
the launcher `cooperace`, is where it finds everything of its own: the
bundled BenchExec in lib/, the default conf in conf/ and the components in
tools/. It is taken from the location of this package, never from the working
directory. What the user gives (the task, the property file and `--conf`) is
a path relative to the working directory, and witnesses are delivered into it.

Importing the package puts the one wheel lib/benchexec-*.whl of TOOL_DIR first
on sys.path (find_wheel), so that components.py imports that BenchExec and no
other. If lib/ holds no such wheel, or more than one, the import prints the
problem on stderr and ends the process with status 1 (a tool-info module
would otherwise come from whatever BenchExec is installed, or from whichever
wheel comes first).
"""
import glob
import os
import sys

#The directory of the launcher: src/cooperace/__init__.py is two levels below it
TOOL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_wheel(tool_dir: str) -> str:
    """The path of the one BenchExec wheel, `tool_dir`/lib/benchexec-*.whl.

    Raises FileNotFoundError, with a message naming lib/, if there is no such
    wheel, and ValueError, naming the wheels, if there are several."""
    wheels = sorted(glob.glob(os.path.join(glob.escape(tool_dir), "lib", "benchexec-*.whl")))
    lib_dir = os.path.join(tool_dir, "lib")
    if not wheels:
        raise FileNotFoundError(f"no BenchExec wheel {os.path.join(lib_dir, 'benchexec-*.whl')}")
    if len(wheels) > 1:
        raise ValueError(f"more than one BenchExec wheel in {lib_dir}: "
                         + ", ".join(os.path.basename(wheel) for wheel in wheels))
    return wheels[0]


try:
    sys.path.insert(0, find_wheel(TOOL_DIR))
except (FileNotFoundError, ValueError) as error:
    print(f"CoOpeRace: error: {error}", file=sys.stderr)
    sys.exit(1)
