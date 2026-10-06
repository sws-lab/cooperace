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

