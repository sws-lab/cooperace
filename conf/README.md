# How to add new config files

##### Lets use svcomp25.json file as an example:
```
{
    "runType": "sequential",
    "tools": [
        {"Goblint": "true"},
        [
            {"Dartagnan": "all"},
            {"ULTIMATE GemCutter": "all"}, 
            {"ULTIMATE Automizer": "all"},
            {"Deagle": "true"}
        ]
    ]
}
```
##### The tool combinations configs are set up as .json files and required two elements:
- runType --- Either sequential or parallel
- tools --- List of tools used, takes as value a list, which composes of specific tools and/or list of specific tools.

Such a conf is one strategy; it is the strategy for the property no-data-race only (see "A strategy per property").

We add the tools as a key-value pair where the key is the tool name and value is either "true", "false", "all". The value defines what type of results we accept. The runType value specifies if the run for this tool config starts as sequential or parellel. In the example above, it starts sequentially, which means that first the script will run Goblint first, if Goblint does not provide an acceptable answer, the rest of the tools will be run in parallel after that. We only have to specify the starting runType, because each nested list will then be the opposite of the previous runType, in the example the first runType is sequential, so the nested list will be run in parallel.

##### A strategy per property
A conf like the one above is one strategy, and it is the strategy for the property no-data-race and for no other: given another property file, CoOpeRace refuses the task (see below). To check other properties, the conf is an object whose only key is `properties`, which maps a property to its strategy, each in the format above, with its own `memoryLimits` and `cpuTimeLimits` (the valid-memsafety strategy here shows the format; it is not a chosen strategy):
```
{
    "properties": {
        "no-data-race": {
            "runType": "sequential",
            "tools": [
                {"Goblint": "true"},
                [
                    {"Dartagnan": "all"},
                    {"ULTIMATE Automizer": "all"}
                ]
            ],
            "cpuTimeLimits": {"Goblint": 30},
            "memoryLimits": {"ULTIMATE Automizer": "70%"}
        },
        "valid-memsafety": {
            "runType": "parallel",
            "tools": [
                {"Goblint": "true"},
                {"Dartagnan": "all"}
            ],
            "cpuTimeLimits": {"Dartagnan": 60}
        }
    }
}
```
The properties are named as the property files of sv-benchmarks are: `unreach-call`, `no-overflow`, `valid-memsafety` and `no-data-race`. CoOpeRace recognizes the property of a task from the formulas in the property file given with `--prop`, with white space and the order of the formulas ignored, never from the file's name or the task's path (`src/cooperace/properties.py`):

| Property | Formulas of the property file |
| --- | --- |
| `unreach-call` | `CHECK( init(main()), LTL(G ! call(reach_error())) )` |
| `no-overflow` | `CHECK( init(main()), LTL(G ! overflow) )` |
| `valid-memsafety` | `CHECK( init(main()), LTL(G valid-free) )`, `CHECK( init(main()), LTL(G valid-deref) )` and `CHECK( init(main()), LTL(G valid-memtrack) )` |
| `no-data-race` | `CHECK( init(main()), LTL(G ! data-race) )` |

A property the conf gives no strategy for is refused before any tool starts, so a conf can leave out a property it should not answer. No conf in this directory has a strategy for a property other than no-data-race.

##### Optional: a memory limit per component
- memoryLimits --- maps a tool name to a memory limit for that tool alone, either in bytes (`4000000000`) or as a percentage of the memory limit of the whole run (`"70%"`), which CoOpeRace reads from its cgroup (`memory.max` under cgroups v2, `memory.limit_in_bytes` under cgroups v1). The limit is set as `RLIMIT_DATA` on the tool's processes. A tool that reaches it ends without a verdict, and the other tools keep running with the memory it used; without it, one tool reaching the run's limit ends the whole run. A percentage is ignored when no cgroup limit is found.

##### Optional: a CPU-time limit per component
- cpuTimeLimits --- maps a tool name to a CPU-time limit in seconds for that tool alone (`{"Goblint": 30}`). It is set as `RLIMIT_CPU` on the tool's processes, each of which may use that much CPU time on its own (the kernel ends it with SIGXCPU, and SIGKILL one second later). Goblint's portfolio runner starts one process per level and gives up the remaining levels when the limit ends one, so a limit for Goblint bounds its whole stage by the time of the levels that end by themselves plus the limit. Without it, a strategy that runs Goblint before the other tools loses their answers on every task where Goblint's last levels run until the run's own time limit. A tool whose own process the limit ends (by SIGXCPU, or by SIGKILL after it used more CPU time than the limit) gets the status `TIMEOUT`, as BenchExec reports a run that reached its time limit; Goblint's runner instead exits by itself after the limit ended a level, so Goblint's status stays what its tool-info module reads from the output (`unknown`), and its block shows `goblint exited with code -24`.

Both limits can be given for any tool of the strategy they are in, not only Goblint. In a conf of `properties`, each strategy has its own `memoryLimits` and `cpuTimeLimits`, which name tools of that strategy and apply only when that strategy runs. A tool ended by its limit is a step without a verdict, like a tool that answers `unknown`: in a sequential list the next element runs, and in a parallel list the other tools keep running.

##### What CoOpeRace refuses
CoOpeRace checks the property file and the conf before it starts any tool (`src/cooperace/properties.py`, `src/cooperace/config.py`) and stops with status 1, one line `CoOpeRace: error: ...` on stderr and no `CoOpeRace verdict:` line (so BenchExec records ERROR, not UNKNOWN, and the task scores 0), if the property file holds the formulas of none of the four properties, if the conf has no strategy for the property, or if the conf is not a JSON object, has `properties` beside another key, maps a name that is not one of the four properties, or has a strategy that lacks `runType` or `tools`, has a `runType` other than sequential or parallel, has a `tools` that is not a list of objects and lists, names a tool more than once anywhere in `tools`, has a key in `memoryLimits` or `cpuTimeLimits` that is not a tool of `tools`, names a tool CoOpeRace does not know, or gives an accepted value that is not "true", "false" or "all". Every strategy of the conf is checked, not only the one for the task's property. It also stops this way, before any tool starts, if the executable of a tool that any strategy of the conf names is not under the `tools/` directory of CoOpeRace; a conf that names a tool that is not installed (for example Deagle, which `scripts/download-tools.py` fetches only with `--pool`, since it is not in `tools.txt`) is refused as a whole, for every property, not run up to that tool. The same holds for a tool of any strategy whose `tools/<name>/.doi` is not the DOI that `tools-options.json` holds its options for.

A conf given with `--conf` is a path relative to the working directory; without it, CoOpeRace reads `conf/svcomp26.json` from its own directory.
