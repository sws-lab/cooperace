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

We add the tools as a key-value pair where the key is the tool name and value is either "true", "false", "all". The value defines what type of results we accept. The runType value specifies if the run for this tool config starts as sequential or parellel. In the example above, it starts sequentially, which means that first the script will run Goblint first, if Goblint does not provide an acceptable answer, the rest of the tools will be run in parallel after that. We only have to specify the starting runType, because each nested list will then be the opposite of the previous runType, in the example the first runType is sequential, so the nested list will be run in parallel.

##### Optional: a memory limit per component
- memoryLimits --- maps a tool name to a memory limit for that tool alone, either in bytes (`4000000000`) or as a percentage of the memory limit of the whole run (`"70%"`), which CoOpeRace reads from its cgroup (`memory.max` under cgroups v2, `memory.limit_in_bytes` under cgroups v1). The limit is set as `RLIMIT_DATA` on the tool's processes. A tool that reaches it ends without a verdict, and the other tools keep running with the memory it used; without it, one tool reaching the run's limit ends the whole run. A percentage is ignored when no cgroup limit is found.

##### Optional: a CPU-time limit per component
- cpuTimeLimits --- maps a tool name to a CPU-time limit in seconds for that tool alone (`{"Goblint": 30}`). It is set as `RLIMIT_CPU` on the tool's processes, each of which may use that much CPU time on its own (the kernel ends it with SIGXCPU, and SIGKILL one second later). Goblint's portfolio runner starts one process per level and gives up the remaining levels when the limit ends one, so a limit for Goblint bounds its whole stage by the time of the levels that end by themselves plus the limit. Without it, a strategy that runs Goblint before the other tools loses their answers on every task where Goblint's last levels run until the run's own time limit.

##### What CoOpeRace refuses
CoOpeRace checks the conf before it starts any tool (`src/cooperace/config.py`) and stops with status 1, one line `CoOpeRace: error: ...` on stderr and no `CoOpeRace verdict:` line (so BenchExec records ERROR, not UNKNOWN), if the conf is not a JSON object, lacks `runType` or `tools`, has a `runType` other than sequential or parallel, has a `tools` that is not a list of objects and lists, names a tool more than once anywhere in `tools`, has a key in `memoryLimits` or `cpuTimeLimits` that is not a tool of `tools`, names a tool CoOpeRace does not know, or gives an accepted value that is not "true", "false" or "all". It also stops this way, before any tool starts, if the executable of a tool the conf names is not under the `tools/` directory of CoOpeRace; a conf that names a tool that is not installed (for example Deagle, which `scripts/download-tools.py` does not fetch) is refused as a whole, not run up to that tool.

A conf given with `--conf` is a path relative to the working directory; without it, CoOpeRace reads `conf/svcomp26.json` from its own directory.
