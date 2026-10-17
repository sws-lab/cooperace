# Integration suite for the components of CoOpeRace

A regression check that takes about 7 minutes on 2 cores (on `goblint`, 16 cores, BenchExec 3.35), not the hours of a run over the whole no-data-race category. For each component CoOpeRace integrates (Goblint, Dartagnan, ULTIMATE Automizer) it asks whether the component, run inside CoOpeRace, gives the verdict it gives alone and delivers a witness in the format SV-COMP 2027 requires for no-data-race (YAML 2.2, `witness.yml`) that a validator proposed for SV-COMP 2027 confirms and WitnessLint accepts.

```sh
python3 scripts/download-tools.py            # tools/, once
python3 scripts/download-validators.py       # validators/, once
python3 tests/integration/run.py             # or: make integration
```

The SV-COMP 2027 versions of the validators are proposed in fm-tools merge requests that are open on 2026-10-17 (ULTIMATE Automizer !1172, Theta !1196, ConcurrentWitness2Test !1198, WitnessLint !1173). Until they are merged, `download-validators.py` finds them only in a copy of fm-tools with those requests applied, given as `--fm-tools-dir`.

The exit status is 0 when every check passes, 1 when one fails, 2 when the suite could not run (a missing task, tool or validator, an unreadable configuration, a failed `benchexec` call), with the reason on standard error. The table and the failed checks go to standard output and to `tests/integration/out/table.txt`; `out/results.json` has every run (status, CPU time, memory, witness, validation) and the failed and passed checks.

Requirements: Linux with BenchExec (`benchexec` on the path, cgroups as BenchExec needs them; tested with BenchExec 3.35), Python 3 with `yaml` (a BenchExec dependency), a `tools/` directory made by `scripts/download-tools.py`, `gcc` with ThreadSanitizer (ConcurrentWitness2Test compiles the program with `-fsanitize=thread`), and the Python packages WitnessLint's fm-tools entry lists (Ubuntu's `python3-lxml`, `python3-jsonschema`, `python3-yaml`, `python3-pycparser`, `python3-clang`); a package that is not installed can be put on `PYTHONPATH`, which BenchExec passes on to the runs. Every verifier, validator and `./cooperace` run goes through `benchexec`; nothing is started outside it. The `alone-` runs put the checkout's `lib/benchexec-*.whl` first on `PYTHONPATH`, so the `benchexec` command runs them with the wheel's executor and tool-info modules, the BenchExec version CoOpeRace bundles; the other runs, and the validators, use the installed BenchExec. Set `TMPDIR=/tmp` if `TMPDIR` points to a directory BenchExec overlays (BenchExec's container then refuses to start).

## What is run

Four kinds of verification run, named in the table by their BenchExec run definition:

| Run definition | What it runs |
| --- | --- |
| `alone-<component>` | the component's own tool-info module (`benchexec.tools.goblint`, `.dartagnan`, `.ultimateautomizer`) with the options `tools-options.json` records for the component, which are the `benchexec_toolinfo_options` of its fm-tools version and what CoOpeRace passes to it (for the `svcomp27` versions, Goblint: `--portfolio-conf conf/svcomp27/seq.txt`, ULTIMATE Automizer: `--full-output`, Dartagnan: none), started in the component's directory under `tools/`. This is the reference. The suite stops with status 2 if `tools/<component>/.doi` is not the DOI that `tools-options.json` records. |
| `only-<component>` | `./cooperace --conf conf/only-<component>.json`: CoOpeRace configured with that component alone, accepting both verdicts. |
| `svcomp26`, `svcomp25`, ... | `./cooperace --conf conf/<name>.json`: a production configuration of the checkout under test, and any further configuration given with `--config`. A configuration that names a component missing under `tools/` (`svcomp25` needs ULTIMATE GemCutter and Deagle, which `download-tools.py` fetches only with `--pool`) is listed as not run, not as failed. |
| `svcomp26-goblint-1s` | `./cooperace --conf tests/integration/conf/svcomp26-goblint-1s.json`: a configuration of the suite (`configurations` in `manifest.json`), `conf/svcomp26.json` with `"cpuTimeLimits": {"Goblint": 1}`. No task of the other runs reaches the 30 s limit of `conf/svcomp26.json` within the suite's 100 s, so this configuration lowers it to 1 s and runs a task of its own, `goblint-regression/04-mutex_19-call_by_ptr_rc`, on which level 3 of Goblint's portfolio reaches it and Dartagnan then answers `false`. It exercises the limit, the portfolio runner giving up the remaining levels, and the hand-over to the next stage, in about 8 s of wall time and 4 s of validation. |

`alone-` and `only-` runs use each component's own tasks only (`owners` in `manifest.json`); `--cross` runs every component on every task. The production configurations run every task, except a task that names `configurations` in `manifest.json`, which only those configurations of the suite run. `./cooperace` is started with the checkout as working directory, since it looks for `tools/` there; the witness it writes is collected from the BenchExec run's result files.

Every `false` verdict of a task expected `false` is then validated, as a BenchExec run of the validator named for the task in `manifest.json`, and the witness of every `false` is checked by WitnessLint, as one more BenchExec run (`out/lint/`). The witness is read from the verification run's result files, as the competition reads `results-verified`. The validators are those proposed for the SV-COMP 2027 track "Validation of Violation Witnesses v2" that check no-data-race, each with the `benchexec_toolinfo_options` of its fm-tools version:

| Validator | fm-tools version | DOI | Options |
| --- | --- | --- | --- |
| ULTIMATE Automizer | `svcomp27-violation` | 10.5281/zenodo.23295622 | `--validate <witness> --full-output --witness-type violation_witness` |
| Theta | `svcomp27-validator` | 10.5281/zenodo.23184251 | `--svcomp --portfolio STABLE --loglevel RESULT --witness <witness>` |
| ConcurrentWitness2Test | `svcomp27` | 10.5281/zenodo.21342108 | `--witness <witness>` |
| WitnessLint (format check) | `2.3-violation-witnesses-v2` | 10.5281/zenodo.23270765 | `--witness <witness> --expectViolationWitness --ignoreSelfLoops --expectedWitnessVersion 2` |

Each task names one validator that confirms the witness of each of the task's owners when the owner runs alone. Not used: CPAchecker's 2027 version (fm-tools !1156, 10.5281/zenodo.23209212), which Zenodo does not serve; Dartagnan's `svcomp27-validation` (!1170), whose wrapper has no `-witness` option and so cannot run as a validator; MetaVal and Witch, whose versions in fm-tools (`svcomp26`) do not support no-data-race. SV-COMP validates only violation witnesses for no-data-race, so no `true` verdict has a witness check.

The witness format is data in `run.py` (`WITNESS_FORMATS`: the file a verifier writes, its validators and its linter with BenchExec tool-info module and options, and how to read the producer from it); `--witness-format` selects the entry. `yaml-2.2`, the default, is SV-COMP 2027's format; `graphml-1.0` is SV-COMP 2026's (`witness.graphml`, the validators CPAchecker 4.2.2, Dartagnan `svcomp26-validation` and ULTIMATE Automizer `svcomp26-violation` with the options of their bench-defs definitions `*-validate-violation-witnesses-v1.xml` at tag `svcomp26`, no linter; `download-validators.py --format graphml-1.0` fetches them), and it needs a manifest whose validators are of that format.

## The checks

| # | Check | A failure means |
| --- | --- | --- |
| 1 | No run, in any configuration, gives a verdict different from the task's expected one. | A wrong verdict, in CoOpeRace or in a component alone. |
| 2 | Each component alone gives the expected verdict on its own tasks, and where that is `false`, the named validator confirms its witness. | The task is a bad pick (a component or validator behaves differently here than when the task was picked): replace the task. Not a defect of CoOpeRace. |
| 3 | CoOpeRace with `only-<component>` gives the verdict the component gives alone, wherever the component alone answered. | The integration changes what the component computes: options, data model, preprocessing, parsing of its output. |
| 4 | Every `false` of CoOpeRace on a task expected `false` delivers a witness file of the format (`witness.yml`) that the named validator confirms, that the format's linter (WitnessLint) accepts, and whose producer (`metadata.producer.name`), where it names one, is the component that answered (the `CoOpeRace result from: X` line of CoOpeRace's output). | The witness is missing, belongs to another component or run, the validator rejects it, or it does not have the form the format requires. |
| 5 | Each production configuration, and each configuration of the suite, gives the expected verdict on every task it runs. | The configuration gives no verdict (`unknown`, timeout, out of memory) or a wrong one on a task that some component answers within the limits. |
| 6 | Every output of CoOpeRace follows its protocol: the last line is `CoOpeRace verdict: true`, `false` or `unknown` (unless BenchExec ended the run); right before it, for `true` and `false` only, one `CoOpeRace result from: X`; every `---X logs---` is closed by its `---end of X logs---` and followed by `Tool name: X Status: ...`; no line starts with `CoOpeRace: error:`; and a `false` run's result files hold the file of the witness format (`witness.yml`), every file there is `witness.graphml` or `witness.yml` in the format its name says, and no two files are in one format (a component may write both, as ULTIMATE Automizer `svcomp27` does). | CoOpeRace's output or witness delivery changed in a way that `benchexec.tools.cooperace` (which reads only the last line), this suite or a validator depends on, or CoOpeRace ended with an error of its own, which it reports as a line `CoOpeRace: error: ...` and no verdict line. |
| 7 | Wherever a configuration gives a component a limit (`memoryLimits`, `cpuTimeLimits`), CoOpeRace's output has `Memory limit of X: N bytes` or `CPU-time limit of X: N s` whenever X started, with N the configured value; for a percentage, within 1 % of that percentage of the memory limit BenchExec records for the run (`memlimit` in the result XML). | CoOpeRace did not set the limit it was configured with, or set another one: for a percentage, `run_memory_limit` did not find the run's cgroup limit inside BenchExec's container on this machine, and the component ran without a limit of its own. |
| 8 | In `svcomp26-goblint-1s`, Goblint's status is `TIMEOUT` (the 1 s limit of the cgroup of Goblint's run ended its portfolio), its result is `unknown`, and the verdict comes from a later stage (`CoOpeRace result from: X` with X not Goblint) and is the expected one; check 4 validates its witness. | The CPU-time limit did not end Goblint (the limit was not applied, or Goblint answered within 1 s on this machine: then the task is a bad pick), or CoOpeRace did not start the next stage. |
| 9 | In every output of CoOpeRace, no component's block appears twice; and when the component named by `CoOpeRace result from: X` belongs to a parallel stage of the configuration (in `svcomp26`, Dartagnan beside ULTIMATE Automizer), every other component of that stage has `Status: stopped by CoOpeRace` or `Result: unknown`. | `runParallel` did not stop the components that lost, or let two of them give an accepted verdict, or CoOpeRace ran a component twice. |

Check 1 also covers what 5 says about wrong verdicts, so a wrong verdict of a production configuration fails both. Checks 6 to 9 read CoOpeRace's output in the BenchExec log of each run; `src/cooperace/` prints it (`print_component_run` and `announce_limits` in `components.py`, `execute` in `strategy.py`) and the launcher `cooperace` prints the verdict line. A task that only one component answers makes a production configuration depend on that component's integration: that is the point of choosing such tasks, since another component's answer cannot hide a broken integration.

## Limits

The suite's own limits, not the competition's (4 cores, 15 GB, 900 s CPU time for a verification run, 2 cores, 7 GB, 90 s for a validation run):

- verification run: 2 cores, 4 GB memory, 100 s CPU time (hard limit 125 s), one run at a time (`-N 1`);
- validation run: 2 cores, 4 GB memory, 90 s CPU time (hard limit 120 s).

`--cores`, `--memlimit`, `--timelimit`, `-N` and `--allowed-cores` change them. The heaviest task, `ldv-races/race-4_1-thread_local_vars`, needs 24 s CPU time of ULTIMATE Automizer alone and 27 s with CoOpeRace.

## Tasks

`manifest.json` lists each task with its owners (the components that are expected to answer it alone), the configurations of the suite that alone run it (`configurations`, where it has one), the validator, its origin, its licence and what SV-COMP 2026 recorded for each component (`sv_comp_2026`: verdict and CPU time of the component's own run, or the status). Tasks are copies from `sv-benchmarks` at tag `svcomp26` (commit 7efe28dd29576b46927b7a34e8f742bd90966a75) with the `.yml` reduced to the no-data-race property; a task of the same name there carries the original `.yml`. All are ILP32, except the two LP64 programs written for the suite. Their source is the `.c` beside each `.i`; the `.i` is `cpp -P race-only-on-lp64.c > race-only-on-lp64.i` (and the same for `race-only-on-ilp32`) on x86-64 (GCC 15.2, glibc 2.43 of Ubuntu 26.04), which leaves out line markers: Dartagnan `svcomp27` ends in `ERROR` on a `.i` whose line markers name the `.c`, since it cannot place the witness's waypoints in the file it was given. Licences are those of the task's directory in `sv-benchmarks`, texts in `LICENSES/` at the root of the repository (REUSE.toml lists the license of each file without a header).

Goblint never answers `false` for no-data-race in SV-COMP 2026: its own run reported `false` on none of the 1030 tasks of the category (it ends in `unknown`, a time-out or an error on the 236 tasks expected `false`), so Goblint has `true` tasks only. For every other task, the SV-COMP 2026 record shows the owner answering within 10 s CPU time (Dartagnan), 25 s (ULTIMATE Automizer, whose version query adds about 4 s inside CoOpeRace) or 0.3 s (Goblint), apart from the one task of 42 s.

| Task | Expected | Owner | Why |
| --- | --- | --- | --- |
| `goblint-regression/28-race_reach_41-trylock_racefree` | true (ILP32) | goblint | Goblint answers true in 0.1 s; neither Dartagnan nor ULTIMATE Automizer answers, so only a working Goblint can give the verdict. |
| `pthread-theta/unwind3-100` | true (ILP32) | goblint | Goblint answers true; the others do not. |
| `goblint-regression/28-race_reach_01-simple_racing` | true (ILP32) | goblint | Goblint answers true; the others do not. |
| `pthread-divine/tls_basic` | true (ILP32) | dartagnan | Dartagnan answers true; Goblint gives unknown and ULTIMATE Automizer no answer. |
| `pthread-divine/tls_destructor_worker` | true (ILP32) | dartagnan | Dartagnan answers true; the others do not. |
| `pthread-divine/ring_1w1r-1` | false (ILP32) | dartagnan | Dartagnan answers false and Theta's validator confirms its witness (ULTIMATE Automizer's validator reaches the 90 s limit on it); the others give no answer. Goblint ends in unknown after 2.2 s CPU time. |
| `goblint-regression/09-regions_01-list_rc` | false (ILP32) | dartagnan | Dartagnan answers false on a .c task (preprocessed by the component) and ULTIMATE Automizer's validator confirms its witness; Goblint ends in unknown after 0.8 s and ULTIMATE Automizer times out. Replaces pthread-divine/ring_1w1r-2, where Goblint's own run needs 169 s CPU time to end in unknown, which with a working Goblint in front of Dartagnan exceeds the suite's 100 s limit. |
| `ldv-races/race-4_1-thread_local_vars` | true (ILP32) | uautomizer | The only task of the category on which ULTIMATE Automizer alone answers true; the heaviest task of the suite (42 s CPU in SV-COMP 2026). |
| `pthread-theta/unwind2-nondet` | true (ILP32) | uautomizer | ULTIMATE Automizer answers true, Dartagnan does not; Goblint answers it in its own entry, so a Goblint that works hides ULTIMATE Automizer in a configuration that starts with Goblint. |
| `goblint-regression/04-mutex_50-funptr_rc` | false (ILP32) | uautomizer | ULTIMATE Automizer alone answers false on a .c task (preprocessed by the component) that calls a function through a pointer; Theta's validator confirms its witness. |
| `goblint-regression/04-mutex_21-sound_base` | false (ILP32) | uautomizer | ULTIMATE Automizer alone answers false on a .c task (preprocessed by the component); ConcurrentWitness2Test confirms its witness. |
| `goblint-regression/04-mutex_19-call_by_ptr_rc` | false (ILP32) | none; run by `svcomp26-goblint-1s` only | Level 3 of Goblint's portfolio uses more than 1 s of CPU time, so the limit of `svcomp26-goblint-1s` ends it; Dartagnan answers false in the next stage and ULTIMATE Automizer's validator confirms its witness (both also in SV-COMP 2026, 8.6 s CPU time). ULTIMATE Automizer also answers false, with 20.6 s against Dartagnan's 8.6 s in SV-COMP 2026, so CoOpeRace normally stops it. Goblint's own run needs 54 s to end in unknown, too long for the other configurations. |
| `lp64/race-only-on-lp64` | false (LP64) | uautomizer, dartagnan | A data race only when sizeof(long) == 8: expected false under LP64.  ULTIMATE Automizer answers true if given ILP32, so CoOpeRace must pass the task's data model. ULTIMATE Automizer's validator confirms the witnesses of both owners. |
| `lp64/race-only-on-ilp32` | true (LP64) | uautomizer, dartagnan | The same program with the indices swapped: no race under LP64, expected true; ULTIMATE Automizer given ILP32 reports a race. |

A production configuration that starts with Goblint spends Goblint's time on every task before another component starts, so a task on which Goblint needs long to end in `unknown` (169 s CPU time on `pthread-divine/ring_1w1r-2`, 2 s on `ring_1w1r-1`) does not fit the suite's 100 s limit; the Dartagnan and ULTIMATE Automizer tasks were chosen with Goblint ending in `unknown` within 1 to 2 s.

## Output directory

`out/` (git-ignored) holds `meta.json` (machine, BenchExec version, commit of the checkout and its `git describe --always --dirty`, `tools.txt`, limits, and the run definitions with their tasks), `defs/` (the generated BenchExec definitions), `verify/` and `validate/` (BenchExec's results, logs and result files), `witnesses/<run definition>/<task>.yml/witness.yml` (the witnesses that were validated), `lint/` (WitnessLint's runs), `table.txt` and `results.json`. BenchExec writes the environment of the process that starts it into every result XML. `run.py` therefore starts BenchExec without any environment variable whose name contains `TOKEN`, `SECRET`, `PASSWORD` or `KEY`, in any case (and with `TMPDIR=/tmp`); other variables are still recorded, so look through a result XML before sharing it.

`run.py validate --out DIR` validates and checks the verification runs already in DIR; `run.py check --out DIR` only rebuilds the table and the checks, which is how a change to a check is tried without rerunning anything. Both take the run definitions and tasks from `DIR/meta.json`, not from the options that choose them, and ignore results in DIR of run definitions or tasks that `meta.json` does not list.

## Known limits of the suite

- ULTIMATE Automizer's and Dartagnan's witnesses differ from run to run, and ConcurrentWitness2Test executes the program repeatedly. A task whose validator confirms one run's witness and times out on another's is a bad pick; the tasks were chosen so that the validators confirm the witnesses of the runs made when the validators were picked.
- The suite checks no `true` witness.
- The data model is tested by two LP64 programs; no task of the SV-COMP no-data-race category is LP64. Dartagnan's tool-info module passes no data model, so the LP64 tasks check that its verdicts are the LP64 ones, not that it follows the data model.
