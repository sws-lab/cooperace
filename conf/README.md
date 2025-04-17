# How to add new config files

##### Lets use svcomp25.json file as an example:
`
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
`
The tool combinations configs are set up as .json files and required two elements:
- runType --- Either sequential or parallel
- tools --- List of tools used, takes as value a list, which composes of specific tools and/or list of specific tools.

We add the tools as a key-value pair where the key is the tool name and value is either "true", "false", "all". The value defines what type of results we accept. The runType value specifies if the run for this tool config starts as sequential or parellel. In the example above, it starts sequentially, which means that first the script will run Goblint first, if Goblint does not provide an acceptable answer, the rest of the tools will be run in parallel after that. We only have to specify the starting runType, because each nested list will then be the opposite of the previous runType, in the example the first runType is sequential, so the nested list will be run in parallel.
