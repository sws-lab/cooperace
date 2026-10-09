# Integration suite for the components of CoOpeRace

A regression check that takes about 6 minutes on 2 cores (on `goblint`, 16 cores, BenchExec 3.35), not the hours of a run over the whole no-data-race category. For each component CoOpeRace integrates (Goblint, Dartagnan, ULTIMATE Automizer) it asks whether the component, run inside CoOpeRace, gives the verdict it gives alone and delivers a witness that an SV-COMP 2026 validator confirms.

```sh
python3 scripts/download-tools.py            # tools/, once
python3 scripts/download-validators.py       # validators/, once
python3 tests/integration/run.py             # or: make integration
```

The exit status is 0 when every check passes, 1 when one fails, 2 when the suite could not run (a missing task, tool or validator, an unreadable configuration, a failed `benchexec` call), with the reason on standard error. The table and the failed checks go to standard output and to `tests/integration/out/table.txt`; `out/results.json` has every run (status, CPU time, memory, witness, validation) and the failed and passed checks.

Requirements: Linux with BenchExec (`benchexec` on the path, cgroups as BenchExec needs them; tested with BenchExec 3.35), Python 3 with `yaml` (a BenchExec dependency), and a `tools/` directory made by `scripts/download-tools.py`. Every verifier, validator and `./cooperace` run goes through `benchexec`; nothing is started outside it. Set `TMPDIR=/tmp` if `TMPDIR` points to a directory BenchExec overlays (BenchExec's container then refuses to start).

## What is run

Four kinds of verification run, named in the table by their BenchExec run definition:

| Run definition | What it runs |
| --- | --- |
| `alone-<component>` | the component's own tool-info module (`benchexec.tools.goblint`, `.dartagnan`, `.ultimateautomizer`) with the `benchexec_toolinfo_options` of the component's fm-tools entry for SV-COMP 2026 (Goblint: `--portfolio-conf conf/svcomp26/seq.txt`, ULTIMATE Automizer: `--full-output`, Dartagnan: none), started in the component's directory under `tools/`. This is the reference. |
| `only-<component>` | `./cooperace --conf conf/only-<component>.json`: CoOpeRace configured with that component alone, accepting both verdicts. |
| `svcomp26`, `svcomp25`, ... | `./cooperace --conf conf/<name>.json`: a production configuration of the checkout under test, and any further configuration given with `--config`. A configuration that names a component missing under `tools/` (`svcomp25` needs ULTIMATE GemCutter and Deagle, which `download-tools.py` does not fetch) is listed as not run, not as failed. |
| `svcomp26-goblint-1s` | `./cooperace --conf tests/integration/conf/svcomp26-goblint-1s.json`: a configuration of the suite (`configurations` in `manifest.json`), `conf/svcomp26.json` with `"cpuTimeLimits": {"Goblint": 1}`. No task of the other runs reaches the 30 s limit of `conf/svcomp26.json` within the suite's 100 s, so this configuration lowers it to 1 s and runs a task of its own, `goblint-regression/04-mutex_19-call_by_ptr_rc`, on which level 3 of Goblint's portfolio reaches it and Dartagnan then answers `false`. It exercises the limit, the portfolio runner giving up the remaining levels, and the hand-over to the next stage, in about 8 s of wall time and 4 s of validation. |

`alone-` and `only-` runs use each component's own tasks only (`owners` in `manifest.json`); `--cross` runs every component on every task. The production configurations run every task, except a task that names `configurations` in `manifest.json`, which only those configurations of the suite run. `./cooperace` is started with the checkout as working directory, since it looks for `tools/` there; the witness it writes is collected from the BenchExec run's result files.

Every `false` verdict of a task expected `false` is then validated, as a BenchExec run of the validator named for the task in `manifest.json`, with the options of the validator's bench-defs definition `*-validate-violation-witnesses-v1.xml` of SV-COMP 2026 (the witness is read from the verification run's result files, as the competition reads `results-verified`). The validators are CPAchecker 4.2.2, Dartagnan and ULTIMATE Automizer of the SV-COMP 2026 track "Validation of Violation Witnesses v1". Each task names one validator that confirmed a witness of the task's component in SV-COMP 2026; CPAchecker is not used, since its validator ended in `ERROR` on every witness of these tasks in the competition. SV-COMP 2026 validates only violation witnesses (graphml 1.0) for no-data-race, so no `true` verdict has a witness check.

The witness format is data in `run.py` (`WITNESS_FORMATS`: the file a verifier writes, its validators with BenchExec tool-info module and options, and how to read the producer from it); `--witness-format` selects the entry, and only `graphml-1.0` exists.

## The checks

| # | Check | A failure means |
| --- | --- | --- |
| 1 | No run, in any configuration, gives a verdict different from the task's expected one. | A wrong verdict, in CoOpeRace or in a component alone. |
| 2 | Each component alone gives the expected verdict on its own tasks, and where that is `false`, the named validator confirms its witness. | The task is a bad pick (a component or validator behaves differently here than in SV-COMP 2026): replace the task. Not a defect of CoOpeRace. |
| 3 | CoOpeRace with `only-<component>` gives the verdict the component gives alone, wherever the component alone answered. | The integration changes what the component computes: options, data model, preprocessing, parsing of its output. |
| 4 | Every `false` of CoOpeRace on a task expected `false` delivers a `witness.graphml` that the named validator confirms, and whose `producer`, where it names one, is the component that answered (the `CoOpeRace result from: X` line of CoOpeRace's output). | The witness is missing, belongs to another component or run, or the validator rejects it. |
| 5 | Each production configuration, and each configuration of the suite, gives the expected verdict on every task it runs. | The configuration gives no verdict (`unknown`, timeout, out of memory) or a wrong one on a task that some component answers within the limits. |
| 6 | Every output of CoOpeRace follows its protocol: the last line is `CoOpeRace verdict: true`, `false` or `unknown` (unless BenchExec ended the run); right before it, for `true` and `false` only, one `CoOpeRace result from: X`; every `---X logs---` is closed by its `---end of X logs---` and followed by `Tool name: X Status: ...`; no line starts with `CoOpeRace: error:`; and a `false` run's result files hold exactly one witness file, `witness.graphml` or `witness.yml`, in that format. | CoOpeRace's output or witness delivery changed in a way that `benchexec.tools.cooperace` (which reads only the last line), this suite or a validator depends on, or CoOpeRace ended with an error of its own, which it reports as a line `CoOpeRace: error: ...` and no verdict line. |
| 7 | Wherever a configuration gives a component a limit (`memoryLimits`, `cpuTimeLimits`), CoOpeRace's output has `Memory limit of X: N bytes` or `CPU-time limit of X: N s` whenever X started, with N the configured value; for a percentage, within 1 % of that percentage of the memory limit BenchExec records for the run (`memlimit` in the result XML). | CoOpeRace did not set the limit it was configured with, or set another one: for a percentage, `run_memory_limit` did not find the run's cgroup limit inside BenchExec's container on this machine, and the component ran without a limit of its own. |
| 8 | In `svcomp26-goblint-1s`, Goblint's block shows `goblint exited with code -24` (the portfolio runner's message when SIGXCPU ended a level), Goblint's result is `unknown`, and the verdict comes from a later stage (`CoOpeRace result from: X` with X not Goblint) and is the expected one; check 4 validates its witness. | The CPU-time limit did not end the level (`RLIMIT_CPU` not set, or the level ended before 1 s on this machine: then the task is a bad pick), the runner went on to the next level or reported a verdict, or CoOpeRace did not start the next stage. |
| 9 | In every output of CoOpeRace, no component's block appears twice; and when the component named by `CoOpeRace result from: X` belongs to a parallel stage of the configuration (in `svcomp26`, Dartagnan beside ULTIMATE Automizer), every other component of that stage has `Status: stopped by CoOpeRace` or `Result: unknown`. | `runParallel` did not stop the components that lost, or let two of them give an accepted verdict, or CoOpeRace ran a component twice. |

Check 1 also covers what 5 says about wrong verdicts, so a wrong verdict of a production configuration fails both. Checks 6 to 9 read CoOpeRace's output in the BenchExec log of each run; `src/cooperace/` prints it (`print_component_run` and `with_resource_limits` in `components.py`, `execute` in `strategy.py`) and the launcher `cooperace` prints the verdict line. A task that only one component answers makes a production configuration depend on that component's integration: that is the point of choosing such tasks, since another component's answer cannot hide a broken integration.

## Limits

The suite's own limits, not the competition's (4 cores, 15 GB, 900 s CPU time for a verification run, 2 cores, 7 GB, 90 s for a validation run):

- verification run: 2 cores, 4 GB memory, 100 s CPU time (hard limit 125 s), one run at a time (`-N 1`);
- validation run: 2 cores, 4 GB memory, 90 s CPU time (hard limit 120 s).

`--cores`, `--memlimit`, `--timelimit`, `-N` and `--allowed-cores` change them. The heaviest task, `ldv-races/race-4_1-thread_local_vars`, needs 24 s CPU time of ULTIMATE Automizer alone and 27 s with CoOpeRace.

## Tasks

`manifest.json` lists each task with its owners (the components that are expected to answer it alone), the configurations of the suite that alone run it (`configurations`, where it has one), the validator, its origin, its licence and what SV-COMP 2026 recorded for each component (`sv_comp_2026`: verdict and CPU time of the component's own run, or the status). Tasks are copies from `sv-benchmarks` at tag `svcomp26` (commit 7efe28dd29576b46927b7a34e8f742bd90966a75) with the `.yml` reduced to the no-data-race property; a task of the same name there carries the original `.yml`. All are ILP32, except the two LP64 programs written for the suite. Licences are those of the task's directory in `sv-benchmarks`, texts in `c/LICENSES/`.

Goblint never answers `false` for no-data-race in SV-COMP 2026: its own run reported `false` on none of the 1030 tasks of the category (it ends in `unknown`, a time-out or an error on the 236 tasks expected `false`), so Goblint has `true` tasks only. For every other task, the SV-COMP 2026 record shows the owner answering within 10 s CPU time (Dartagnan), 25 s (ULTIMATE Automizer, whose version query adds about 4 s inside CoOpeRace) or 0.3 s (Goblint), apart from the one task of 42 s.

| Task | Expected | Owner | Why |
| --- | --- | --- | --- |
| `goblint-regression/28-race_reach_41-trylock_racefree` | true (ILP32) | goblint | Goblint answers true in 0.1 s; neither Dartagnan nor ULTIMATE Automizer answers, so only a working Goblint can give the verdict. |
| `pthread-theta/unwind3-100` | true (ILP32) | goblint | Goblint answers true; the others do not. |
| `goblint-regression/28-race_reach_01-simple_racing` | true (ILP32) | goblint | Goblint answers true; the others do not. |
| `pthread-divine/tls_basic` | true (ILP32) | dartagnan | Dartagnan answers true; Goblint gives unknown and ULTIMATE Automizer no answer. |
| `pthread-divine/tls_destructor_worker` | true (ILP32) | dartagnan | Dartagnan answers true; the others do not. |
| `pthread-divine/ring_1w1r-1` | false (ILP32) | dartagnan | Dartagnan answers false and the Dartagnan validator confirms its witness; ULTIMATE Automizer's validator rejects it, the others give no answer. Goblint ends in unknown after 2.2 s CPU time. |
| `goblint-regression/09-regions_01-list_rc` | false (ILP32) | dartagnan | Dartagnan answers false on a .c task (preprocessed by the component) and the Dartagnan validator confirms its witness; Goblint ends in unknown after 0.8 s and ULTIMATE Automizer times out. Replaces pthread-divine/ring_1w1r-2, where Goblint's own run needs 169 s CPU time to end in unknown, which with a working Goblint in front of Dartagnan exceeds the suite's 100 s limit. |
| `ldv-races/race-4_1-thread_local_vars` | true (ILP32) | uautomizer | The only task of the category on which ULTIMATE Automizer alone answers true; the heaviest task of the suite (42 s CPU in SV-COMP 2026). |
| `pthread-theta/unwind2-nondet` | true (ILP32) | uautomizer | ULTIMATE Automizer answers true, Dartagnan does not; Goblint answers it in its own entry, so a Goblint that works hides ULTIMATE Automizer in a configuration that starts with Goblint. |
| `goblint-regression/04-mutex_50-funptr_rc` | false (ILP32) | uautomizer | ULTIMATE Automizer alone answers false on a .c task (preprocessed by the component) that calls a function through a pointer; its witness is confirmed by the Dartagnan and the ULTIMATE Automizer validators. |
| `goblint-regression/04-mutex_21-sound_base` | false (ILP32) | uautomizer | ULTIMATE Automizer alone answers false on a .c task (preprocessed by the component); its witness is confirmed by the Dartagnan and the ULTIMATE Automizer validators. |
| `goblint-regression/04-mutex_19-call_by_ptr_rc` | false (ILP32) | none; run by `svcomp26-goblint-1s` only | Level 3 of Goblint's portfolio uses more than 1 s of CPU time, so the limit of `svcomp26-goblint-1s` ends it; Dartagnan answers false in the next stage and the Dartagnan validator confirms its witness (both also in SV-COMP 2026, 8.6 s CPU time). ULTIMATE Automizer also answers false, with 20.6 s against Dartagnan's 8.6 s in SV-COMP 2026, so CoOpeRace normally stops it. Goblint's own run needs 54 s to end in unknown, too long for the other configurations. |
| `lp64/race-only-on-lp64` | false (LP64) | uautomizer, dartagnan | A data race only when sizeof(long) == 8: expected false under LP64.  ULTIMATE Automizer answers true if given ILP32, so CoOpeRace must pass the task's data model. |
| `lp64/race-only-on-ilp32` | true (LP64) | uautomizer, dartagnan | The same program with the indices swapped: no race under LP64, expected true; ULTIMATE Automizer given ILP32 reports a race. |

A production configuration that starts with Goblint spends Goblint's time on every task before another component starts, so a task on which Goblint needs long to end in `unknown` (169 s CPU time on `pthread-divine/ring_1w1r-2`, 2 s on `ring_1w1r-1`) does not fit the suite's 100 s limit; the Dartagnan and ULTIMATE Automizer tasks were chosen with Goblint ending in `unknown` within 1 to 2 s.

## Output directory

`out/` (git-ignored) holds `meta.json` (machine, BenchExec version, commit of the checkout and its `git describe --always --dirty`, `tools.txt`, limits, and the run definitions with their tasks), `defs/` (the generated BenchExec definitions), `verify/` and `validate/` (BenchExec's results, logs and result files), `witnesses/<run definition>/<task>.yml/witness.graphml` (the witnesses that were validated), `table.txt` and `results.json`. BenchExec writes the environment of the process that starts it into every result XML. `run.py` therefore starts BenchExec without any environment variable whose name contains `TOKEN`, `SECRET`, `PASSWORD` or `KEY`, in any case (and with `TMPDIR=/tmp`); other variables are still recorded, so look through a result XML before sharing it.

`run.py validate --out DIR` validates and checks the verification runs already in DIR; `run.py check --out DIR` only rebuilds the table and the checks, which is how a change to a check is tried without rerunning anything. Both take the run definitions and tasks from `DIR/meta.json`, not from the options that choose them, and ignore results in DIR of run definitions or tasks that `meta.json` does not list.

## Known limits of the suite

- ULTIMATE Automizer's and Dartagnan's witnesses differ from run to run. A task whose validator confirms one run's witness and times out on another's is a bad pick; the tasks were chosen so that the validators confirm the witnesses of the runs made when the suite was written.
- The suite checks no `true` witness, and no format other than graphml 1.0.
- The data model is tested by two LP64 programs; no task of the SV-COMP no-data-race category is LP64. Dartagnan's tool-info module passes no data model, so the LP64 tasks check that its verdicts are the LP64 ones, not that it follows the data model.
