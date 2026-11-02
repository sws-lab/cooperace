# Confs: a strategy per property

A conf is a JSON object whose only key is `properties`, which maps a property to its strategy. A strategy says three things for its property: when each tool runs (`phases`), whose verdict wins (`answers`), and whose witness is delivered (`witnesses`). `conf/svcomp27.json` is the default conf, read when CoOpeRace is started without `--conf`. `conf/witnesses.json` is not a conf: it is the witness table, which says which witness each verdict of each property needs (see [The witness table](#the-witness-table-confwitnessesjson)).

## The default conf: `conf/svcomp27.json`

The default conf is the SV-COMP 2027 strategy: in each property, phase 1 runs Goblint, trusted for `true`, beside sv-sanitizers, trusted for `false`, where it can answer, each limited to 30 s of CPU time, and in no-data-race IKOS-ConC, trusted for both verdicts, limited to 60 s; phase 2 runs the best `true` verifier and the best `false` falsifier of the property whose 2027 version writes the witness 2027 requires. The changes over the conf of the first 2027 submission are fits on the per-task results of the SV-COMP 2027 pre-run, simulated with the strategy semantics below (IKOS-ConC in no-data-race; ULTIMATE GemCutter first in `witnesses` of unreach-call and no-overflow and trusted for `false` in no-overflow; Dartagnan as a last bracket in valid-memsafety).

```json
{
    "properties": {
        "no-data-race": {
            "phases":        [{"tools": ["sv-sanitizers", "Goblint", "IKOS-ConC"], "wallTimeLimit": 45},
                              ["Goblint", "IKOS-ConC", "Dartagnan"]],
            "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30, "IKOS-ConC": 60},
            "memoryLimits":  {"Dartagnan": "35%"},
            "runCpuTimeBudget": 870,
            "answers":       [["Goblint:true", "IKOS-ConC:true"], ["IKOS-ConC:false", "sv-sanitizers:false"],
                              ["Dartagnan"]],
            "witnesses":     ["IKOS-ConC", "sv-sanitizers", "Dartagnan"],
            "ignoreIfCalls": {"Dartagnan": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy"],
                              "Dartagnan:true": ["pthread_mutexattr_settype"]}
        },
        "unreach-call": {
            "phases":        [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45},
                              ["ULTIMATE GemCutter", "Dartagnan"]],
            "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
            "memoryLimits":  {"ULTIMATE GemCutter": "60%", "Dartagnan": "35%"},
            "runCpuTimeBudget": 870,
            "answers":       [["Goblint:true"], ["ULTIMATE GemCutter:true", "Dartagnan:false"],
                              ["sv-sanitizers:false"]],
            "witnesses":     ["ULTIMATE GemCutter", "Goblint", "Dartagnan", "sv-sanitizers"],
            "ignoreIfCalls": {"Dartagnan": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy"]}
        },
        "no-overflow": {
            "phases":        [{"tools": ["Goblint"], "wallTimeLimit": 45},
                              ["ULTIMATE GemCutter", "Deagle"]],
            "cpuTimeLimits": {"Goblint": 30},
            "memoryLimits":  {"ULTIMATE GemCutter": "80%", "Deagle": "15%"},
            "runCpuTimeBudget": 870,
            "answers":       [["Goblint:true"], ["ULTIMATE GemCutter", "Deagle:false"]],
            "witnesses":     ["ULTIMATE GemCutter", "Goblint", "Deagle"],
            "ignoreIfCalls": {"Deagle": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy",
                                        "pthread_once", "pthread_mutexattr_settype"]}
        },
        "valid-memsafety": {
            "phases":        [{"tools": ["sv-sanitizers", "Goblint"], "wallTimeLimit": 45},
                              ["Deagle", "Dartagnan"]],
            "cpuTimeLimits": {"sv-sanitizers": 30, "Goblint": 30},
            "memoryLimits":  {"Deagle": "60%", "Dartagnan": "35%"},
            "runCpuTimeBudget": 870,
            "answers":       [["Goblint:true"], ["Deagle:false", "sv-sanitizers:false"], ["Deagle:true"],
                              ["Dartagnan"]],
            "witnesses":     ["Deagle", "sv-sanitizers", "Dartagnan"],
            "ignoreIfCalls": {"Deagle": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy",
                                        "pthread_once", "pthread_mutexattr_settype"],
                              "Dartagnan": ["pthread_barrier_init", "pthread_barrier_wait", "pthread_barrier_destroy"],
                              "Dartagnan:true": ["pthread_mutexattr_settype"]}
        }
    }
}
```

In no-data-race, sv-sanitizers, Goblint and IKOS-ConC start together. Goblint's and IKOS-ConC's `true` are trusted most, then IKOS-ConC's and sv-sanitizers' `false`, then either answer of Dartagnan; IKOS-ConC is first in `witnesses`. Phase 1 ends when sv-sanitizers has ended, or after 45 s of wall time, which stops sv-sanitizers; Goblint, also in phase 2, keeps running until its own limit of 30 s of CPU time, IKOS-ConC keeps running until its own limit of 60 s of CPU time (it is in phase 2 so that the wall-time limit of phase 1 does not stop it), and Dartagnan starts. In the pre-run IKOS-ConC answered every task it answered within 21 s of CPU time, with no wrong answer. In unreach-call, sv-sanitizers runs beside Goblint in phase 1 with 30 s of CPU time and its `false` is trusted least: its two `false`s in the 2027 pre-run that contradict the expected verdict (`weaver/unroll-cond-3` and `-5`) are benchmark defects, where `false` is right, and it needed 318 s and 402 s for them. Its violation witness for one target is of format 2.0, which only the default witness mode accepts. In no-overflow, phase 1 is Goblint alone. ULTIMATE GemCutter's `true` and Dartagnan's (unreach-call) or Deagle's (no-overflow) `false` share the second bracket, so a disagreement between them is a contradiction; in no-overflow GemCutter's `false` is trusted too. ULTIMATE GemCutter is first in `witnesses`: after Goblint's `true` it is started for its witness (rule 3 of "When a tool is stopped"), which is delivered if it proves `true`, else Goblint's. Since GemCutter now runs after every Goblint `true`, it has a memory limit (below): a GemCutter that exceeds it is killed alone, and Goblint's answer and witness are still delivered, where without it the whole run would end `OUT OF MEMORY`. In the pre-run, GemCutter's witness was confirmed on 26 no-overflow and 3 unreach-call tasks where Goblint's was not, and never failed where Goblint's was confirmed. In valid-memsafety, Deagle's `false` is trusted as much as sv-sanitizers' `false`, its `true` less, and Dartagnan, which runs beside Deagle in phase 2, least. `ignoreIfCalls` drops Deagle's answers wherever the program calls a barrier, `pthread_once` or `pthread_mutexattr_settype`, Dartagnan's answers wherever it calls a barrier function, and Dartagnan's `true` wherever it calls `pthread_mutexattr_settype`: CBMC's library model, which Deagle uses, treats `pthread_barrier_wait` as a no-op, has no model of `pthread_once` and ignores recursive mutexes; Dartagnan svcomp27 locks a recursive mutex as a plain one, which gave it four wrong no-data-race `true`s in the 2027 pre-run, and treats the barrier functions as unknown functions, which gave it wrong `false`s on the barrier tasks merged into sv-benchmarks for the final run (`pthread-barrier-phases/phase-sum`, unreach-call and no-data-race). In the pre-run simulation the guards cost nothing.

**The memory limits.** Every tool that runs without a CPU-time limit of its own, until the run's budget, has a memory limit, and the limits of the tools of one phase add up to at most 95% of the run's: Dartagnan 35% in no-data-race, unreach-call and valid-memsafety; ULTIMATE GemCutter 60% and Dartagnan 35% in unreach-call; GemCutter 80% (as before) and Deagle 15% in no-overflow; Deagle 60% and Dartagnan 35% in valid-memsafety. The tools with a CPU-time limit (30 s or 60 s) run beside them only for their first minute. The run's cgroup has `memory.oom.group` set, so a tool that took the run to its limit would end the whole run `OUT OF MEMORY` and lose the answers already decided; with the limits, a tool that exceeds its own is killed alone. Dartagnan needs one most: it is a GraalVM native image whose Java heap may grow to 80% of the memory its cgroup shows, which without a limit of its own is the whole run's, and on `pthread-complex/elimination_backoff_stack-race` it grew to 10 GB without answering. The limits keep every answer the 2027 pre-run (3 GB per run) and the SV-COMP 2026 final run (15 GB, each tool alone) give: there, Dartagnan's correct answers in C.Concurrency used at most 3.9 GB (35% of 15 GB is 5.25 GB), GemCutter's in unreach-call at most 8.1 GB, Deagle's in valid-memsafety at most 8.1 GB, and Deagle's correct `false`s in no-overflow, the only answers of Deagle trusted there, at most 0.2 GB.

**The wall-time limit of phase 1, 45 s,** ends phase 1 when a tool waits without using CPU time, which a CPU-time limit does not end. It must not stop a phase-1 tool that is still within its 30 s of CPU time. In the integration suite on `goblint` (BenchExec 3.35, 2 cores per run, cgroups v2), sv-sanitizers reached its 30 s limit on four tasks after 30.8 to 30.9 s of CPU time and 31.4 to 32.2 s of wall time, since BenchExec ends a tool up to about 1 s after its limit and the tool shares the run's cores with Goblint; Goblint needs about 1.1 s of wall time per second of CPU time. 45 s is 13 s above the slowest of these, and 1.5 times the CPU-time limit. The orders of the brackets and of `witnesses` are the fits of the replay on the SV-COMP 2026 results of the notebook finding 1020-imp-revised-baseline-replay; in unreach-call and no-overflow every order replayed scores the same.

The properties are named as the property files of sv-benchmarks are: `unreach-call`, `no-overflow`, `valid-memsafety` and `no-data-race`. CoOpeRace recognizes the property of a task from the formulas in the property file given with `--prop`, with white space and the order of the formulas ignored, never from the file's name or the task's path (`src/cooperace/properties.py`):

| Property | Formulas of the property file |
| --- | --- |
| `unreach-call` | `CHECK( init(main()), LTL(G ! call(reach_error())) )` |
| `no-overflow` | `CHECK( init(main()), LTL(G ! overflow) )` |
| `valid-memsafety` | `CHECK( init(main()), LTL(G valid-free) )`, `CHECK( init(main()), LTL(G valid-deref) )` and `CHECK( init(main()), LTL(G valid-memtrack) )` |
| `no-data-race` | `CHECK( init(main()), LTL(G ! data-race) )` |

A property the conf gives no strategy for is refused before any tool starts, so a conf can leave out a property it should not answer. `conf/svcomp27.json` has a strategy for each of the four.

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

`witnesses` is a list of tools, each with an entry in `answers`. A witness is delivered only where one can count for the property and verdict, which is where the witness table gives a format: in `conf/witnesses.json`, a `false` in all four properties, and a `true` in unreach-call and no-overflow. Where the table gives `null`, for a `true` in no-data-race and valid-memsafety, nothing is delivered, and `witnesses` does not apply. Where a witness can count, once the verdict is decided, it is taken from the first tool of this list that has an entry for the verdict in some bracket, answered that verdict, and wrote a witness that passes the check of `src/cooperace/witnesses.py` for the property and verdict: it is a YAML list whose entries of the verdict's kind are one `violation_sequence` for `false`, and for `true` an `invariant_set`, a `ghost_instrumentation` or one of each; these entries have one and the same `metadata.format_version`, which is one the witness table accepts, and each has a `metadata.task.specification` with the formulas of the property, or of the named sub-property for valid-memsafety, or with that single formula's LTL formula alone (`G ! data-race`, as IKOS-ConC writes it, which the 2027 pre-run's validators confirmed). Goblint 23770235 (`svcomp27-0-gdc055dbfb`) writes its default correctness witness: an `invariant_set` of format 2.0, or, where an invariant uses a ghost variable, a `ghost_instrumentation` and an `invariant_set` both of format 2.1. (Goblint 23361000 wrote the ghost entry at 2.1 beside an `invariant_set` at 2.0, which this check refuses, and CoOpeRace's 2027 submission restricted it to the ghost entry, which states no invariant and which validators rarely confirmed.) A GraphML witness never passes. If no tool of the list qualifies, it is taken from the first tool by name of the deciding bracket that answered the verdict and wrote one. If there is none, and the witness table requires a witness for the property and verdict, CoOpeRace prints `CoOpeRace verdict: unknown`, with a line before it saying which verdict it had and that no witness was written. `"witnesses": []` is allowed; then only the deciding bracket can supply the witness.

Whether a witness can count, whether one is required, and which format versions pass the check are what the witness table says (see [The witness table](#the-witness-table-confwitnessesjson)); they are not part of the conf.

In valid-memsafety, `false(valid-deref)` and `false(valid-free)` in one bracket are not a contradiction. The sub-property is taken from the tool whose witness is delivered, else from the first agreeing tool of `witnesses`, else from the first agreeing tool of the deciding bracket by name.

## The witness table: `conf/witnesses.json`

`conf/witnesses.json` is the one place that says what the competition requires of a witness. CoOpeRace reads it from its own directory, whatever `--conf` says, to decide whether a witness passes and whether a verdict without one can be delivered (`src/cooperace/witnesses.py`), and the integration suite reads it to check the witness a `false` delivers (`tests/integration/run.py`, check 6). It is plain JSON, so that a script checking a component's witnesses against the competition's formats can read it too:

```json
{
    "highestVersion": "2.3",
    "properties": {
        "no-data-race": {
            "true": null,
            "false": {"minimumVersion": "2.2", "required": true}
        },
        "unreach-call": {
            "true": {"minimumVersion": "2.0", "required": true},
            "false": {"minimumVersion": "2.0", "required": true}
        },
        "no-overflow": {
            "true": {"minimumVersion": "2.0", "required": true},
            "false": {"minimumVersion": "2.2", "required": true}
        },
        "valid-memsafety": {
            "true": null,
            "false": {"minimumVersion": "2.2", "required": false,
                      "subproperties": {"valid-memtrack": {"minimumVersion": "2.3"}}}
        }
    }
}
```

- `highestVersion` is the highest YAML witness format version that passes, for every property and verdict.
- `properties` has an object for each of the four properties, and each of those has the keys `"true"` and `"false"`. The value of a verdict is `null` where the competition supports no witness for it: none is delivered, `witnesses` does not apply, and the verdict scores without one. Otherwise it is an object with:
  - `minimumVersion`, the lowest YAML format version that passes. A witness passes with a version from `minimumVersion` to `highestVersion`, both included. A version is a string `"major.minor"`, never a JSON number, since `2.10` and `2.1` are the same number. The type of the witness's entries follows from the verdict: `violation_sequence` for `false`, `invariant_set` and `ghost_instrumentation` for `true`.
  - `required`, `true` if the competition scores the verdict only with a witness that passes. Then a verdict whose suppliers wrote no witness that passes ends as `CoOpeRace verdict: unknown`. `false` is the competition's demo mode: the verdict scores without a witness, and one that passes is still delivered, and validated without affecting the score. Rule 3 of "When a tool is stopped" applies in both cases.
  - `subproperties`, optional and allowed only for valid-memsafety's `false`: a sub-property (`valid-free`, `valid-deref` or `valid-memtrack`) to an object `{"minimumVersion": "x.y"}`, which replaces `minimumVersion` for a `false` naming that sub-property. `required` cannot differ between sub-properties.

| Property | `true` | `false` |
| --- | --- | --- |
| no-data-race | not supported | required, YAML 2.2 to 2.3 |
| unreach-call | required, YAML 2.0 to 2.3 (2.1 to 2.3 in the strict mode) | required, YAML 2.0 to 2.3 (2.2 to 2.3 in the strict mode) |
| no-overflow | required, YAML 2.0 to 2.3 (2.1 to 2.3 in the strict mode) | required, YAML 2.2 to 2.3 |
| valid-memsafety | not supported | demo mode, YAML 2.2 to 2.3; 2.3 only for `valid-memtrack` |

The content is the `C.*.Concurrency` rows of "Supported Witness Formats" on the SV-COMP 2027 benchmarks page (<https://sv-comp.sosy-lab.org/2027/benchmarks.php>, read 2026-10-20), which the competition's scoring scripts classify the same way (`sosy-lab/benchmarking/competition-scripts` at `cf979db`, `prepare_tables/category_structure.py`, commit `4cc75eb`, whose unit tests pin those cells). The page supports YAML 2.0 to 2.3, and 2.4 in demo mode only; `highestVersion` is 2.3, so a 2.4 witness never passes. GraphML witnesses count in none of these cells, and CoOpeRace never delivers one. In the valid-memsafety row the whole `false` cell is in demo mode. The page's footnote that versions 2.0 to 2.2 can express only valid-deref and valid-free violations, and that valid-memtrack needs 2.3 or higher in demo mode, belongs to the rows outside concurrency; the table keeps its consequence for valid-memtrack as a `subproperties` override, so that a valid-memtrack witness passes only in version 2.3.

The table describes the `C.*.Concurrency` sub-categories only. In `C.Huawei-Concurrency-Challenges` every cell is in demo mode, but CoOpeRace cannot tell at run time whether a task belongs to it, and applies the `C.*.Concurrency` table to every task. On a Huawei task this changes the score in one case: a `false` of no-data-race, unreach-call or no-overflow, or a `true` of unreach-call or no-overflow, for which no supplier wrote a witness that passes, ends as `unknown` and scores 0. Under the Huawei rows, CoOpeRace would have delivered the answer without a witness: if right, it would have scored its points, which are lost; if wrong, it would have cost the penalty, which is avoided. A witness that passes is delivered on either table.

**The witness mode: witnesses of format 2.0 in unreach-call and no-overflow.** The page requires correctness witnesses of format 2.1 or higher in `C.unreach-call.Concurrency` and `C.no-overflow.Concurrency`, and violation witnesses of 2.2 or higher in `C.unreach-call.Concurrency`. The SV-COMP 2027 pre-run did not enforce that: of Goblint's own pre-run answers (`svcomp27-0-gdc055dbfb`, Zenodo 23770235), 34 unreach-call and 59 no-overflow `true`s with an `invariant_set` of format 2.0 were confirmed and scored, and so were sv-sanitizers' unreach-call `false`s, whose `violation_sequence` is of format 2.0. Goblint writes format 2.0 wherever none of its invariants uses a ghost variable, and 2.1 otherwise. `conf/witnesses.json` therefore accepts 2.0 for these three cells (the default mode). `conf/witness-modes/strict.json` is the table as the page states it; it differs from `conf/witnesses.json` in these three cells only. Under the 2026 scoring rules, which 2027 keeps, a correct answer whose witness is not confirmed scores 0 and is not penalized, so the default mode can lose no points against the strict one if the final run enforces the page. The archive carries one table as `conf/witnesses.json`: `WITNESS_MODE=strict scripts/svcomp-dist.sh` (or `make svcomp WITNESS_MODE=strict`) packs the strict table in its place and ends the archive's `VERSION` with `witnesses-strict`. In the strict mode a Goblint `true` with a 2.0 witness keeps the verdict, but the witness does not pass, so ULTIMATE GemCutter, which comes first in `witnesses` anyway, must supply one, or the run prints `unknown`.

When what the competition requires changes, edit `conf/witnesses.json`; the code has no copy of the table. A table that CoOpeRace refuses is listed under "What CoOpeRace refuses".

## When a tool is stopped

A tool is stopped, or not started when its phase comes, only when no answer it could give changes what CoOpeRace prints — the verdict, the error, the witness delivered or the sub-property — whatever the tools still running or still to start answer. So stopping tools early gives the result that letting every tool run to its end would give; the one difference is that a contradiction names only the entries that answered before the run ended.

The exception is the run's CPU-time budget, `runCpuTimeBudget` (seconds, optional). The competition's limit, 900 s of CPU time for all processes of the run, ends a run that is still waiting for a tool, and BenchExec then scores it `TIMEOUT` even if a bracket had decided long before (for example, Deagle's `false` in no-overflow while GemCutter, which could contradict it, runs on). Once the CPU time of the run's cgroup (every tool together, read from `cpu.stat`) reaches the budget, CoOpeRace stops every tool still running, starts none, and prints the result of the answers it has, as if those tools had given up. `svcomp27.json` sets 870 s, 30 s below the limit, for the time that stopping the tools and delivering the witness take.

Before there is a verdict, every tool runs as `phases` says. Once the deciding bracket `b` has a verdict `v`, let `V*` be `v` together with every verdict that a tool still running or still to start has an entry for in a bracket above `b`: each of these could still become the verdict. A tool keeps running, or is started when its phase comes, if one of these rules holds for some `u` in `V*`, with `b` the bracket that `u` would be decided in:

1. **It could beat `u`**: it has an entry in a bracket above `b`.
2. **It could contradict `u`**: it has an entry for the other verdict in `b` and has not answered.
3. **It could supply a witness that comes first**, wherever a witness can count for `u`, whether or not one is required: it has an entry for `u`, and it comes before the current supplier in the order in which suppliers are asked (`witnesses`, then the tools of `b` by name), or anywhere in that order if there is no supplier. For a `u` other than `v`, the current supplier is none.

Every other tool is stopped. The run ends when no tool is running or still to start. A conf that does not want a tool kept running for a witness leaves it out of `witnesses`.

## Optional: a memory limit per tool

`memoryLimits` maps a tool name to a memory limit for that tool alone, either a positive number of bytes (`4000000000`) or a percentage above 0 and at most 100 of the memory limit of the whole run (`"70%"`), which CoOpeRace reads from its cgroup (`memory.max` under cgroups v2, `memory.limit_in_bytes` under cgroups v1). It is the limit of the memory cgroup of the tool's `RunExecutor` run, which counts all the tool's processes together, the memory they hold resident and the files its container writes to its tmpfs. When the tool reaches it, BenchExec kills every process of the tool, and the tool's status is `OUT OF MEMORY`; the other tools keep running with the memory it used. Without it, one tool reaching the run's limit ends the whole run. A percentage is ignored when no cgroup limit is found.

## Optional: a CPU-time limit per tool

`cpuTimeLimits` maps a tool name to a positive CPU-time limit in seconds for that tool alone (`{"Goblint": 30}`). It is the limit of the cpu cgroup of the tool's `RunExecutor` run, which counts all the tool's processes together: for Goblint, all levels of its portfolio. It counts from the tool's start, whichever phases the tool spans. When the tool reaches it, BenchExec kills every process of the tool (about 0.6 s late, when its time-limit check next runs), and the tool's status is `TIMEOUT`. A tool ended by its limit counts as `unknown` in its brackets.

Both limits can be given for any tool of the strategy they are in. In a conf, each strategy has its own `memoryLimits` and `cpuTimeLimits`, which name tools of that strategy's phases and apply only when that strategy runs. Both are limits of a cgroup of the tool's run, which BenchExec makes inside its own container under cgroups v2, where CoOpeRace runs ([`../README.md`](../README.md)). BenchExec has no fallback for either: where it finds no memory or cpu cgroup, it refuses to start a tool with that limit, and the tool's status is `ERROR (RunExecutorError: ...)`.

## Optional: ignoring an answer when the program calls a function

`ignoreIfCalls` maps an entry of `answers` (`"Name:true"`, `"Name:false"`, or `"Name"` for the entries of Name that `answers` has) to a non-empty list of C function names. Before the strategy runs, CoOpeRace reads the task's program file: if it calls one of the names, the entry is dropped from `answers`, with a line `CoOpeRace: the task calls f: Name:verdict is ignored`. A bracket left empty is dropped; a tool left without an entry is dropped from the phases, from `witnesses` and from the limits, and a phase left empty is dropped. If no entry is left, no tool runs and the verdict is `unknown`. A call is the name followed by `(` where an expression or a statement can start (after `;`, `{`, `}`, `=`, `(`, `,`, `!`, `&`, `|`, `?`, `:`, `)` or `return`), so a declaration such as glibc's `extern int pthread_once (...)` in a preprocessed file is no call (`config.called_functions`). This is a guard against a component's known modelling gap, not an analysis: a call through a macro or a function pointer is not seen.

## What CoOpeRace refuses

CoOpeRace checks the property file, the conf and the witness table before it starts any tool (`src/cooperace/properties.py`, `src/cooperace/config.py`, `src/cooperace/witnesses.py`) and stops with status 1, one line `CoOpeRace: error: ...` on stderr and no `CoOpeRace verdict:` line (so BenchExec records ERROR, not UNKNOWN, and the task scores 0), if:

- the property file holds the formulas of none of the four properties, or the conf has no strategy for the property;
- the conf is not a JSON object, has no `properties`, has another key beside it, or maps a name that is not one of the four properties;
- a strategy has a key other than `phases`, `answers`, `witnesses`, `cpuTimeLimits`, `memoryLimits`, `runCpuTimeBudget` and `ignoreIfCalls`, or lacks one of the first three;
- `runCpuTimeBudget` is not a positive number;
- `ignoreIfCalls` is not an object, has a key that is no entry of `answers`, or a value that is not a non-empty list of C identifiers;
- `phases` is empty; a phase is neither a list of names nor an object; a phase object has a key other than `tools` and `wallTimeLimit`, or no `tools`; a phase is empty or names a tool twice;
- a phase names a tool CoOpeRace does not know;
- a tool is in two phases that are not consecutive;
- a bracket is empty, an entry is not `Tool:true`, `Tool:false` or `Tool`, or one verdict of one tool is in two entries, in one bracket or in two;
- an entry names a tool in no phase, or a tool of a phase has no entry;
- `witnesses` names a tool twice, or a tool with no entry;
- `cpuTimeLimits` or `memoryLimits` has a key that is in no phase, or a `wallTimeLimit`, a `cpuTimeLimits` value or a `memoryLimits` value is not a positive number (for `memoryLimits`, nor a percentage above 0 and at most 100);
- the witness table `conf/witnesses.json` of CoOpeRace's directory cannot be read or is not JSON; it is not an object with exactly the keys `highestVersion` and `properties`; `properties` names a property that is not one of the four or lacks one of them; a property's object does not have exactly the keys `"true"` and `"false"`; a verdict's value is neither `null` nor an object with `minimumVersion` and `required` and optionally `subproperties`; `required` is not `true` or `false`; `highestVersion` or a `minimumVersion` is not a string `"major.minor"`; a `minimumVersion` is above `highestVersion`; or `subproperties` is given anywhere but valid-memsafety's `false`, names another sub-property than `valid-free`, `valid-deref` and `valid-memtrack`, or gives one another key than `minimumVersion`.

Every strategy of the conf is checked, not only the one for the task's property. CoOpeRace also stops this way, before any tool starts, if the executable of a tool that any strategy of the conf names is not under the `tools/` directory of CoOpeRace; a conf that names a tool that is not installed (for example ULTIMATE Automizer, which `scripts/download-tools.py` fetches only with `--pool`, since it is not in `tools.txt`) is refused as a whole, for every property. The same holds for a tool of any strategy whose `tools/<name>/.doi` is not the DOI that `tools-options.json` holds its options for.

A conf given with `--conf` is a path relative to the working directory; without it, CoOpeRace reads `conf/svcomp27.json` from its own directory.
