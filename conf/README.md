# Confs: a strategy per property

A conf is a JSON object whose only key is `properties`, which maps a property to its strategy. A strategy says three things for its property: when each tool runs (`phases`), whose verdict wins (`answers`), and whose witness is delivered (`witnesses`). `conf/svcomp27.json` is the default conf, read when CoOpeRace is started without `--conf`.

The executor that runs a strategy is not written at this commit: `run_strategy` in `src/cooperace/strategy.py` raises `NotImplementedError`. CoOpeRace reads and checks a conf as this file describes, and then ends with status 1 and no verdict line.

## Two examples

```json
{
    "properties": {
        "no-data-race": {
            "phases":        [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 60},
                              ["Goblint", "Dartagnan"]],
            "cpuTimeLimits": {"sv-sanitizers": 30},
            "answers":       [["Goblint:true"], ["sv-sanitizers:false"], ["Dartagnan"]],
            "witnesses":     ["sv-sanitizers", "Dartagnan"]
        },
        "valid-memsafety": {
            "phases":        [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 60},
                              ["Deagle"]],
            "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
            "answers":       [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"]],
            "witnesses":     ["Deagle", "sv-sanitizers"]
        }
    }
}
```

In no-data-race, sv-sanitizers and Goblint start together. Goblint's `true` is trusted most, then sv-sanitizers' `false`, then either answer of Dartagnan. Phase 1 ends when sv-sanitizers has ended, or after 60 s of wall time, which stops sv-sanitizers; Goblint, also in phase 2, keeps running, and Dartagnan starts. In valid-memsafety, Deagle's `false` is trusted as much as sv-sanitizers' `false`, and its `true` less. The 60 s are a placeholder, not a measured value.

The properties are named as the property files of sv-benchmarks are: `unreach-call`, `no-overflow`, `valid-memsafety` and `no-data-race`. CoOpeRace recognizes the property of a task from the formulas in the property file given with `--prop`, with white space and the order of the formulas ignored, never from the file's name or the task's path (`src/cooperace/properties.py`):

| Property | Formulas of the property file |
| --- | --- |
| `unreach-call` | `CHECK( init(main()), LTL(G ! call(reach_error())) )` |
| `no-overflow` | `CHECK( init(main()), LTL(G ! overflow) )` |
| `valid-memsafety` | `CHECK( init(main()), LTL(G valid-free) )`, `CHECK( init(main()), LTL(G valid-deref) )` and `CHECK( init(main()), LTL(G valid-memtrack) )` |
| `no-data-race` | `CHECK( init(main()), LTL(G ! data-race) )` |

A property the conf gives no strategy for is refused before any tool starts, so a conf can leave out a property it should not answer. `conf/svcomp27.json` has no strategy for no-overflow.

## `phases`: when each tool runs

`phases` is a list of phases, run in order. A phase is a list of tool names, or an object `{"tools": [...], "wallTimeLimit": s}`.

- **A tool listed in consecutive phases keeps running across their boundary** and is not restarted. A tool may not be listed in two phases that are not consecutive.
- **The next phase starts** when every tool of the current phase that is not in the next one has ended, or when the phase's wall-time limit passes. A tool has ended when it answered, gave up, reached its limit, or was stopped.
- **A phase's wall-time limit** is a deadline in seconds counted from when the phase starts. When it passes, CoOpeRace stops every tool of the phase that is not also in the next phase, and the next phase starts; tools continuing into the next phase are not affected. In the last phase, it stops every tool still running. A phase without it runs until the run's own limits. It is needed because a CPU-time limit does not end a tool that waits without using CPU time.

## `answers`: whose verdict wins

`answers` is a list of **brackets**, ranked by trust, highest first. A bracket is a list of **entries**, whose order means nothing. An entry is `Tool:true`, `Tool:false`, or `Tool` for both verdicts. A tool may have entries in more than one bracket, for different verdicts, but each verdict of a tool is in one bracket only. A tool's answer that matches none of its entries is recorded in the log and otherwise ignored.

- **Inside a bracket, the verdict is the meet**: `unknown` is the top, `true` and `false` are below it and not comparable, and the contradiction is the bottom. A tool that has not answered, gave up or reached a limit counts as `unknown`, so one answer is enough, and `true` beside `false` is the contradiction.
- **Between brackets, the higher one overrides**: the verdict is the meet of the highest bracket whose meet is not `unknown`. Answers of a lower bracket that disagree with it are recorded in the log only.
- **A contradiction ends the run with an error**, `CoOpeRace verdict: ERROR (contradiction: Goblint true, Dartagnan false)` naming every disagreeing entry, once no tool with an entry in a higher bracket is still running or still to start. A higher bracket can still override it.
- **An answer without the witness its verdict needs still counts** in the meet; it only cannot supply the witness.

## `witnesses`: whose witness is delivered

`witnesses` is a list of tools, each with an entry in `answers`. Once the verdict is decided, the witness is taken from the first tool of this list that has an entry for the verdict in some bracket, answered that verdict, and wrote a witness in the format the property and verdict need. If no tool of the list qualifies, the witness is that of an answer in the deciding bracket that wrote one. If there is none, and the competition's rules require a witness for the property and verdict, CoOpeRace prints `CoOpeRace verdict: unknown`. `"witnesses": []` is allowed; then only the deciding bracket can supply the witness.

A witness is required for a `false` in no-data-race, unreach-call and no-overflow, and for a `true` in unreach-call and no-overflow; valid-memsafety requires none. This table is fixed by the rules of SV-COMP 2027 and is not part of the conf.

In valid-memsafety, `false(valid-deref)` and `false(valid-free)` in one bracket are not a contradiction. The sub-property is taken from the tool whose witness is delivered, else from the first agreeing tool in `witnesses`, else from the first agreeing tool by name.

## When a tool is stopped

A tool is stopped, or not started when its phase comes, only when no answer it could give changes what CoOpeRace prints — the verdict, the error, or the witness delivered — whatever the tools still running or still to start answer. So stopping tools early gives the result that letting every tool run to its end would give. Once a verdict is decided, a tool keeps running only if it has an entry in a higher bracket, if it has an entry for the other verdict in the deciding bracket and has not answered, or if it could supply a witness the verdict needs from a better place in `witnesses`.

## Optional: a memory limit per tool

`memoryLimits` maps a tool name to a memory limit for that tool alone, either a positive number of bytes (`4000000000`) or a percentage above 0 and at most 100 of the memory limit of the whole run (`"70%"`), which CoOpeRace reads from its cgroup (`memory.max` under cgroups v2, `memory.limit_in_bytes` under cgroups v1). It is the limit of the memory cgroup of the tool's `RunExecutor` run, which counts all the tool's processes together, the memory they hold resident and the files its container writes to its tmpfs. When the tool reaches it, BenchExec kills every process of the tool, and the tool's status is `OUT OF MEMORY`; the other tools keep running with the memory it used. Without it, one tool reaching the run's limit ends the whole run. A percentage is ignored when no cgroup limit is found.

## Optional: a CPU-time limit per tool

`cpuTimeLimits` maps a tool name to a positive CPU-time limit in seconds for that tool alone (`{"Goblint": 30}`). It is the limit of the cpu cgroup of the tool's `RunExecutor` run, which counts all the tool's processes together: for Goblint, all levels of its portfolio. It counts from the tool's start, whichever phases the tool spans. When the tool reaches it, BenchExec kills every process of the tool (about 0.6 s late, when its time-limit check next runs), and the tool's status is `TIMEOUT`. A tool ended by its limit counts as `unknown` in its brackets.

Both limits can be given for any tool of the strategy they are in. In a conf, each strategy has its own `memoryLimits` and `cpuTimeLimits`, which name tools of that strategy's phases and apply only when that strategy runs. Both are limits of a cgroup of the tool's run, which BenchExec makes inside its own container under cgroups v2, where CoOpeRace runs ([`../README.md`](../README.md)). BenchExec has no fallback for either: where it finds no memory or cpu cgroup, it refuses to start a tool with that limit, and the tool's status is `ERROR (RunExecutorError: ...)`.

## What CoOpeRace refuses

CoOpeRace checks the property file and the conf before it starts any tool (`src/cooperace/properties.py`, `src/cooperace/config.py`) and stops with status 1, one line `CoOpeRace: error: ...` on stderr and no `CoOpeRace verdict:` line (so BenchExec records ERROR, not UNKNOWN, and the task scores 0), if:

- the property file holds the formulas of none of the four properties, or the conf has no strategy for the property;
- the conf is not a JSON object, has no `properties`, has another key beside it, or maps a name that is not one of the four properties;
- a strategy has a key other than `phases`, `answers`, `witnesses`, `cpuTimeLimits` and `memoryLimits`, or lacks one of the first three;
- `phases` is empty; a phase is neither a list of names nor an object; a phase object has a key other than `tools` and `wallTimeLimit`, or no `tools`; a phase is empty or names a tool twice;
- a phase names a tool CoOpeRace does not know;
- a tool is in two phases that are not consecutive;
- a bracket is empty, an entry is not `Tool:true`, `Tool:false` or `Tool`, or one verdict of one tool is in two entries, in one bracket or in two;
- an entry names a tool in no phase, or a tool of a phase has no entry;
- `witnesses` names a tool twice, or a tool with no entry;
- `cpuTimeLimits` or `memoryLimits` has a key that is in no phase, or a `wallTimeLimit`, a `cpuTimeLimits` value or a `memoryLimits` value is not a positive number (for `memoryLimits`, nor a percentage above 0 and at most 100).

Every strategy of the conf is checked, not only the one for the task's property. CoOpeRace also stops this way, before any tool starts, if the executable of a tool that any strategy of the conf names is not under the `tools/` directory of CoOpeRace; a conf that names a tool that is not installed (for example Deagle, which `scripts/download-tools.py` fetches only with `--pool`, since it is not in `tools.txt`) is refused as a whole, for every property. The same holds for a tool of any strategy whose `tools/<name>/.doi` is not the DOI that `tools-options.json` holds its options for.

A conf given with `--conf` is a path relative to the working directory; without it, CoOpeRace reads `conf/svcomp27.json` from its own directory.
