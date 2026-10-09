#!/usr/bin/env python3
"""Integration suite for the components of CoOpeRace.

For every component that is installed under tools/ (Goblint, Dartagnan,
ULTIMATE Automizer) the suite runs a handful of small no-data-race tasks

  * with the component alone, through the component's own BenchExec tool-info
    module and the options of its fm-tools entry for SV-COMP 2026 (the
    reference);
  * inside CoOpeRace with a one-component configuration (conf/only-*.json);
  * inside CoOpeRace with each production configuration (conf/svcomp26.json,
    conf/svcomp25.json, and any --config given),

validates the witness of every `false` verdict with a SV-COMP 2026 violation
witness validator, and prints one table of task x configuration and the result
of five checks (see README.md):

  1  no run gives a wrong verdict
  2  each component alone gives the expected verdict on its own tasks, and a
     validator confirms its witness (otherwise the task is a bad pick)
  3  CoOpeRace with one component gives the verdict that component gives alone
  4  every `false` of CoOpeRace delivers a witness file that the validator
     named in manifest.json confirms, produced by the component that answered
  5  each production configuration gives the expected verdict on every task

Every verifier, validator and ./cooperace run goes through `benchexec`.
`validate` and `check` take the runs and tasks recorded in DIR/meta.json by
`run`, and ignore the options that choose them.

    run.py [run] [--out DIR] [--cooperace-dir DIR] [--validators DIR]
                 [--config conf/x.json ...] [--components goblint,dartagnan,...]
                 [--tasks SUBSTRING ...] [--allowed-cores 14,15] [-N 1]
                 [--cores 2] [--memlimit 4GB] [--timelimit 100]
                 [--no-validation] [--cross]
    run.py validate --out DIR         # validate and check the verification runs in DIR
    run.py check --out DIR            # redo the table and the checks from DIR

Exit status: 0 when every check passes, 1 when one fails, 2 when the suite
could not run.
"""
import argparse
import bz2
import glob
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TASKS = HERE / "c"
PROPERTY = TASKS / "properties" / "no-data-race.prp"

# The components of CoOpeRace: the BenchExec tool-info module, the directory
# under tools/, the name in conf/*.json, and the options of the component's
# fm-tools entry for SV-COMP 2026 (`benchexec_toolinfo_options` of the version
# named in `competition_participations`, track Verification).
COMPONENTS = {
    "goblint": dict(module="benchexec.tools.goblint", name="Goblint",
                    options=["--portfolio-conf", "conf/svcomp26/seq.txt"]),
    "dartagnan": dict(module="benchexec.tools.dartagnan", name="Dartagnan",
                      options=[]),
    "uautomizer": dict(module="benchexec.tools.ultimateautomizer",
                       name="ULTIMATE Automizer", options=["--full-output"]),
}

# Tool directories under tools/ of the names that conf/*.json may use
# (the table of `tool_locations` in src/cooperace.py).
TOOL_DIRS = {
    "Goblint": "goblint", "Deagle": "deagle", "Dartagnan": "dartagnan",
    "ULTIMATE Automizer": "uautomizer", "ULTIMATE GemCutter": "ugemcutter",
    "ULTIMATE Taipan": "utaipan", "nacpa": "nacpa",
    "CPAchecker": "CPAchecker-4.0-unix", "sv-sanitizers": "sv-sanitizers",
    "RacerF": "racerf",
}

# Witness formats.  A format names the file a verifier writes (collected from
# the verification run's result files), the validators that read it, and how
# to read the producer out of it.  Per validator: directory under the
# validators directory (as scripts/download-validators.py names it), BenchExec
# tool-info module, the options of the validator's bench-defs definition in
# front of the witness option, and the name of the option that takes the
# witness.  The manifest names, per task, a validator of the chosen format.
# SV-COMP 2026 validates no-data-race violation witnesses as graphml 1.0
# (`*-validate-violation-witnesses-v1.xml` of bench-defs, tag svcomp26); a
# further format is a further entry here and validators fetched for it.
def graphml_producer(witness):
    """The `producer` named in a graphml witness, or None."""
    try:
        root = ET.parse(witness).getroot()
    except (ET.ParseError, OSError):
        return None
    ns = "{http://graphml.graphdrawing.org/xmlns}"
    key = {k.get("id") for k in root.iter(ns + "key") if k.get("attr.name") == "producer"}
    for d in root.iter(ns + "data"):
        if d.get("key") in key:
            return (d.text or "").strip()
    return None


WITNESS_FORMATS = {
    "graphml-1.0": dict(
        file="witness.graphml",
        producer=graphml_producer,
        validators={
            "dartagnan": dict(dir="dartagnan-svcomp26-validation",
                              module="benchexec.tools.dartagnan",
                              options=[], witness_option="-witness"),
            "uautomizer": dict(dir="uautomizer-svcomp26-violation",
                               module="benchexec.tools.ultimateautomizer",
                               options=["--full-output", "--witness-type", "violation_witness"],
                               witness_option="--validate"),
            "cpachecker": dict(dir="cpachecker-4.2.2-validation-violation",
                               module="benchexec.tools.cpachecker",
                               options=["--violation-witness-validation", "--heap", "5000m",
                                        "--benchmark",
                                        "--option", "witness.checkProgramHash=false",
                                        "--option", "cpa.predicate.memoryAllocationsAlwaysSucceed=true",
                                        "--option", "cpa.smg.memoryAllocationFunctions=malloc,__kmalloc,kmalloc,kzalloc,kzalloc_node,ldv_zalloc,ldv_malloc",
                                        "--option", "cpa.smg.arrayAllocationFunctions=calloc,kmalloc_array,kcalloc",
                                        "--option", "cpa.smg.zeroingMemoryAllocation=calloc,kzalloc,kcalloc,kzalloc_node,ldv_zalloc",
                                        "--option", "cpa.smg.deallocationFunctions=free,kfree,kfree_const"],
                               witness_option="--witness"),
        }),
}
VALIDATION_LIMITS = dict(timelimit="90 s", hardtimelimit="120 s", cores="2")

# The data model of an SV-COMP task whose .yml names none.
DEFAULT_DATA_MODEL = "ILP32"


class CouldNotRun(Exception):
    """The suite could not run: bad input, a missing tool, a failed BenchExec
    call.  main prints the message on stderr and exits with status 2."""


# ------------------------------------------------------------------ tasks

def load_tasks(selectors):
    """The tasks of manifest.json whose name contains one of `selectors` (all
    if there are none), with the expected verdict and the data model read from
    each task's own .yml; a .yml without `data_model` means DEFAULT_DATA_MODEL.
    Raises CouldNotRun for a missing .yml, a .yml without an expected verdict
    for no-data-race.prp, or two tasks with the same file name."""
    manifest = json.loads((HERE / "manifest.json").read_text())
    tasks = []
    for entry in manifest["tasks"]:
        yml = TASKS / (entry["name"] + ".yml")
        try:
            text = yml.read_text()
        except OSError as e:
            raise CouldNotRun(f"missing task {yml}: {e.strerror}")
        expected = re.search(r"no-data-race\.prp\s+expected_verdict:\s*(true|false)\b", text)
        if not expected:
            raise CouldNotRun(f"{yml} has no expected verdict for no-data-race.prp")
        data_model = re.search(r"^\s*data_model:\s*(\w+)", text, re.M)
        tasks.append(dict(entry, yml=yml, expected=expected.group(1) == "true",
                          data_model=data_model.group(1) if data_model else DEFAULT_DATA_MODEL,
                          id=Path(entry["name"]).name))
    ids = [t["id"] for t in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        # BenchExec names a task's logs and result files by ${taskdef_name}.
        raise CouldNotRun("task file names must be unique: " + ", ".join(duplicates))
    if selectors:
        tasks = [t for t in tasks if any(s in t["name"] for s in selectors)]
    return manifest, tasks


def verdict_of(status):
    """`true` / `false` from a BenchExec status, None for anything else."""
    if status == "true":
        return True
    if status.startswith("false"):
        return False
    return None


# ------------------------------------------------------------ BenchExec

def write_definition(path, tool, rundefs, limits, tasks_of, bench_options=(), required_files=None):
    """Writes a BenchExec benchmark definition to `path`.  `rundefs` is {name:
    [(option, value)]}, `tasks_of[name]` the tasks of that run definition,
    `bench_options` the options of every run definition, and
    `required_files[name]` the patterns of that run definition's
    <requiredfiles>."""
    lines = ['<?xml version="1.0"?>',
             '<!DOCTYPE benchmark PUBLIC "+//IDN sosy-lab.org//DTD BenchExec benchmark 2.3//EN"'
             ' "https://www.sosy-lab.org/benchexec/benchmark-2.3.dtd">',
             f'<benchmark tool={quoteattr(tool)} timelimit={quoteattr(limits["timelimit"])}'
             f' hardtimelimit={quoteattr(limits["hardtimelimit"])}'
             f' memlimit={quoteattr(limits["memlimit"])} cpuCores={quoteattr(limits["cores"])}>',
             "  <resultfiles>**/witness.*</resultfiles>"]
    lines += [option_xml(o, v, "  ") for o, v in bench_options]
    for name, options in rundefs.items():
        lines.append(f"  <rundefinition name={quoteattr(name)}>")
        lines += [f"    <requiredfiles>{escape(str(f))}</requiredfiles>"
                  for f in (required_files or {}).get(name, ())]
        lines += [option_xml(o, v, "    ") for o, v in options]
        lines.append('    <tasks name="t">')
        for t in tasks_of[name]:
            lines.append(f"      <include>{escape(str(t['yml']))}</include>")
        lines.append(f"      <propertyfile>{escape(str(PROPERTY))}</propertyfile>")
        lines += ["    </tasks>", "  </rundefinition>"]
    lines.append("</benchmark>")
    Path(path).write_text("\n".join(lines) + "\n")


def option_xml(name, value, indent):
    if value is None:
        return f"{indent}<option name={quoteattr(name)}/>"
    return f"{indent}<option name={quoteattr(name)}>{escape(value)}</option>"


def option_pairs(options):
    """["--conf", "x", "--full-output"] as (name, value) pairs; a flag has value None."""
    pairs, i = [], 0
    while i < len(options):
        if i + 1 < len(options) and not options[i + 1].startswith("-"):
            pairs.append((options[i], options[i + 1]))
            i += 2
        else:
            pairs.append((options[i], None))
            i += 1
    return pairs


SECRET_NAME = re.compile("TOKEN|SECRET|PASSWORD|KEY", re.IGNORECASE)


def benchexec_environment():
    """The environment `benchexec` is started with.  BenchExec writes the
    environment of its own process into every result XML, so variables whose
    name contains TOKEN, SECRET, PASSWORD or KEY are left out; TMPDIR is /tmp,
    since a TMPDIR that BenchExec overlays makes its container refuse to start."""
    env = {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}
    env["TMPDIR"] = "/tmp"
    return env


def run_benchexec(definition, workdir, tool_directory, out_dir, name, args):
    """Run `benchexec` on `definition` with `workdir` as the working directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["benchexec", "--tool-directory", str(tool_directory), "-N", str(args.parallel),
           "--no-compress-results", "--name", name, "-o", str(out_dir) + os.sep]
    if args.allowed_cores:
        cmd += ["--allowedCores", args.allowed_cores]
    cmd.append(str(definition))
    print("+ (cd", workdir, "&&", " ".join(shlex.quote(c) for c in cmd), ")", flush=True)
    with open(out_dir / f"{name}.stdout.txt", "w") as log:
        proc = subprocess.run(cmd, cwd=workdir, env=benchexec_environment(),
                              stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise CouldNotRun(f"benchexec exited with {proc.returncode}; see {out_dir}/{name}.stdout.txt")


def read_results(out_dir):
    """{(run definition, task id): dict} of every result XML in out_dir."""
    results = {}
    for xml in sorted(glob.glob(str(out_dir / "*.results.*.xml*"))):
        if xml.endswith(".txt"):
            continue
        root = ET.parse(bz2.open(xml) if xml.endswith(".bz2") else xml).getroot()
        rundef = root.get("name").rsplit(".", 1)[0]
        logs = Path(re.sub(r"\.results\..*", ".logfiles", xml))
        files = Path(re.sub(r"\.results\..*", ".files", xml))
        for run in root.iter("run"):
            cols = {c.get("title"): c.get("value") for c in run.findall("column")}
            task = Path(run.get("name")).stem
            status = cols.get("status", "")
            results[(rundef, task)] = dict(
                status=status, verdict=verdict_of(status),
                cpu=float((cols.get("cputime") or "0s").rstrip("s")),
                wall=float((cols.get("walltime") or "0s").rstrip("s")),
                memory=int((cols.get("memory") or "0B").rstrip("B")),
                reason=cols.get("terminationreason"),
                log=logs / f"{rundef}.{task}.yml.log",
                files=files / rundef / f"{task}.yml")
    return results


# ------------------------------------------------------------- planning

def conf_components(conf):
    """The component names a CoOpeRace configuration uses."""
    names = []
    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        else:
            names.extend(node)
    walk(conf["tools"])
    return names


def plan(args):
    """The run definitions: alone-<c>, only-<c> for every component c, and one
    per production configuration; with the tasks each one runs."""
    manifest, tasks = load_tasks(args.tasks)
    cdir = Path(args.cooperace_dir).resolve()
    present = [c for c in COMPONENTS if (cdir / "tools" / c).is_dir()
               and c in args.components.split(",")]
    runs, skipped = {}, {}
    for c in present:
        own = [t for t in tasks if c in t["owners"]] if not args.cross else tasks
        if own:
            runs[f"alone-{c}"] = dict(kind="alone", component=c, tasks=own)
            runs[f"only-{c}"] = dict(kind="only", component=c, tasks=own,
                                     conf=HERE / "conf" / f"only-{c}.json")
    for c in args.components.split(","):
        if c not in present:
            skipped[c] = f"tools/{c} is not installed"
    configs = args.config or [cdir / "conf" / "svcomp26.json", cdir / "conf" / "svcomp25.json"]
    for path in configs:
        path = Path(path).resolve()
        name = path.stem
        try:
            conf = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise CouldNotRun(f"cannot read the configuration {path}: {e}")
        missing = [n for n in conf_components(conf)
                   if not (cdir / "tools" / TOOL_DIRS.get(n, n)).is_dir()]
        if missing:
            skipped[name] = "not run: " + ", ".join(sorted(set(missing))) + " not installed under tools/"
        else:
            runs[name] = dict(kind="production", conf=path, tasks=tasks)
    return manifest, tasks, runs, skipped, cdir


def plan_from_meta(meta):
    """The tasks and run definitions that `run` recorded in meta.json, for
    `validate` and `check`, in the form `plan` returns them."""
    _, all_tasks = load_tasks(None)
    by_id = {t["id"]: t for t in all_tasks}
    cdir = Path(meta["cooperace_dir"])
    runs = {}
    for name, r in meta["runs"].items():
        unknown = [i for i in r["tasks"] if i not in by_id]
        if unknown:
            print(f"run.py: ignoring the results of {name} on {', '.join(unknown)}, "
                  "which manifest.json no longer lists", file=sys.stderr)
        run = dict(kind=r["kind"], tasks=[by_id[i] for i in r["tasks"] if i in by_id])
        if r["kind"] in ("alone", "only"):
            # Run definitions alone-<component>, only-<component> (plan).
            run["component"] = r.get("component", name.split("-", 1)[1])
        if r["kind"] in ("only", "production"):
            default = (HERE / "conf" / f"{name}.json" if r["kind"] == "only"
                       else cdir / "conf" / f"{name}.json")
            run["conf"] = Path(r.get("conf", default))
        runs[name] = run
    ids = {t["id"] for r in runs.values() for t in r["tasks"]}
    tasks = [t for t in all_tasks if t["id"] in ids]
    return tasks, runs, meta.get("skipped", {}), cdir


def git_describe(cdir):
    """`git describe --always --dirty` of the checkout `cdir`, or None."""
    git = subprocess.run(["git", "-C", str(cdir), "describe", "--always", "--dirty"],
                         capture_output=True, text=True)
    return git.stdout.strip() if git.returncode == 0 else None


def machine_description(cdir):
    cpu = ""
    for line in open("/proc/cpuinfo"):
        if line.startswith("model name"):
            cpu = line.split(":", 1)[1].strip()
            break
    mem = int(open("/proc/meminfo").readline().split()[1])
    git = subprocess.run(["git", "-C", str(cdir), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True)
    tools = cdir / "tools.txt"
    return dict(hostname=platform.node(), kernel=platform.release(), cpu=cpu,
                cpus=os.cpu_count(), mem_kb=mem, python=platform.python_version(),
                benchexec=subprocess.run(["benchexec", "--version"], capture_output=True,
                                         text=True).stdout.strip(),
                cooperace_commit=git.stdout.strip() if git.returncode == 0 else None,
                cooperace_describe=git_describe(cdir),
                tools=tools.read_text().splitlines() if tools.exists() else [])


# ------------------------------------------------------------ verifiers

def verify(args, runs, cdir, out):
    """Run every verification run definition: the components alone (one
    BenchExec benchmark each, since each has its own tool-info module and
    working directory) and CoOpeRace's configurations (one benchmark, the
    configuration given as the option --conf)."""
    limits = dict(timelimit=f"{args.timelimit} s", hardtimelimit=f"{int(args.timelimit * 1.25)} s",
                  memlimit=args.memlimit, cores=str(args.cores))
    vdir = out / "verify"
    shutil.rmtree(vdir, ignore_errors=True)
    (out / "defs").mkdir(parents=True, exist_ok=True)
    for c, comp in COMPONENTS.items():
        name = f"alone-{c}"
        if name not in runs:
            continue
        tool_dir = cdir / "tools" / c
        definition = out / "defs" / f"{name}.xml"
        write_definition(definition, comp["module"], {name: option_pairs(comp["options"])},
                         limits, {name: runs[name]["tasks"]})
        run_benchexec(definition, tool_dir, tool_dir, vdir, name, args)
    coop = {n: r for n, r in runs.items() if r["kind"] != "alone"}
    if coop:
        definition = out / "defs" / "cooperace.xml"
        write_definition(definition, "benchexec.tools.cooperace",
                         {n: [("--conf", str(r["conf"]))] for n, r in coop.items()},
                         limits, {n: r["tasks"] for n, r in coop.items()})
        # ./cooperace looks for tools/ in its working directory.
        run_benchexec(definition, cdir, cdir, vdir, "cooperace", args)


def collect_witnesses(results, fmt, out):
    """Copy the witness file of the format (witness.graphml) of every run with a `false` verdict from the
    verification run's result files to out/witnesses/<run definition>/<task>/."""
    for (rundef, task), r in results.items():
        r["witness"] = None
        # A component run alone writes the witness into its own output directory.
        src = next(iter(sorted(r["files"].rglob(fmt["file"]))), None)
        if r["verdict"] is False and src:
            # BenchExec's ${taskdef_name} is the task file's name, with .yml
            dst = out / "witnesses" / rundef / f"{task}.yml" / fmt["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            r["witness"] = dst


# ----------------------------------------------------------- validators

def validate(args, results, tasks, out):
    """Run the validator named in the manifest on every `false` witness of a
    run whose task is expected `false`, one BenchExec benchmark per validator,
    reading the witness from the verification run's result files."""
    by_id = {t["id"]: t for t in tasks}
    vdir = out / "validate"
    shutil.rmtree(vdir, ignore_errors=True)
    (out / "defs").mkdir(parents=True, exist_ok=True)
    todo = {}
    for (rundef, task), r in results.items():
        t = by_id.get(task)
        if t and r.get("witness") and not t["expected"] and t["validator"]:
            todo.setdefault(t["validator"], {}).setdefault(rundef, []).append(t)
    limits = dict(VALIDATION_LIMITS, memlimit=args.memlimit)
    validators = Path(args.validators).resolve()
    for v, per_rundef in todo.items():
        spec = args.fmt["validators"][v]
        tool_dir = validators / spec["dir"]
        if not tool_dir.is_dir():
            raise CouldNotRun(f"validator {v} not found in {validators}; run scripts/download-validators.py")
        definition = out / "defs" / f"validate-{v}.xml"
        witness = out / "witnesses" / "${rundefinition_name}" / "${taskdef_name}" / args.fmt["file"]
        # The witness is also a required file, as in the competition's definitions,
        # so that it is visible in the container wherever it lies.
        write_definition(
            definition, spec["module"],
            {rd: [(spec["witness_option"], str(witness))] for rd in per_rundef},
            limits, per_rundef, bench_options=option_pairs(spec["options"]),
            required_files={rd: [witness] for rd in per_rundef})
        run_benchexec(definition, tool_dir, tool_dir, vdir, f"validate-{v}", args)
        for (rundef, task), r in read_results(vdir).items():
            if (rundef, task) in results and (results[(rundef, task)].get("validator") in (None, v)):
                results[(rundef, task)]["validator"] = v
                results[(rundef, task)]["validation"] = dict(
                    status=r["status"], cpu=r["cpu"],
                    verdict="confirmed" if r["verdict"] is False else
                            "rejected" if r["verdict"] is True else "unknown")


def component_of_producer(producer):
    p = producer.lower()
    for name, c in (("dartagnan", "dartagnan"), ("goblint", "goblint"),
                    ("automizer", "uautomizer"), ("ultimate", "uautomizer")):
        if name in p:
            return c
    return None


def answered_by(r):
    """The component whose verdict CoOpeRace returned, as a key of COMPONENTS
    (or the name CoOpeRace printed, for a component not in COMPONENTS): the
    `CoOpeRace result from: X` line of CoOpeRace's output, or, in a log
    without it, the first `Tool name: X Result: true|false` line."""
    try:
        text = Path(r["log"]).read_text(errors="replace")
    except OSError:
        return None
    m = (re.search(r"^CoOpeRace result from: (.+)$", text, re.M)
         or re.search(r"^Tool name: (.+?) Result: (?:true|false)$", text, re.M))
    if not m:
        return None
    for c, comp in COMPONENTS.items():
        if comp["name"] == m.group(1):
            return c
    return m.group(1)


# --------------------------------------------------------------- checks

def check(results, runs, tasks, skipped, fmt):
    """The five checks; a list of (check, run definition, task, ok, kind, detail)."""
    out = []
    def add(n, rundef, task, ok, kind, detail):
        out.append((n, rundef, task, ok, kind, detail))
    by_id = {t["id"]: t for t in tasks}
    for (rundef, task), r in sorted(results.items()):
        if rundef not in runs or task not in by_id:
            continue  # a result of an earlier run in the same directory
        t = by_id[task]
        run = runs[rundef]
        if r["verdict"] is not None and r["verdict"] != t["expected"]:
            add(1, rundef, task, False, "wrong verdict",
                f"{r['status']}, expected {str(t['expected']).lower()} ({t['data_model']})")
        else:
            add(1, rundef, task, True, "", "")
        if r["verdict"] is False and not t["expected"]:
            v = r.get("validation")
            who = t["validator"]
            if run["kind"] == "alone":
                ok = bool(v) and v["verdict"] == "confirmed"
                add(2, rundef, task, ok, "bad task pick",
                    "" if ok else f"the witness of {run['component']} alone is not confirmed by "
                                  f"{who}: {v['verdict'] + ' (' + v['status'] + ')' if v else 'not validated'}")
            else:
                problems = []
                if not r.get("witness"):
                    problems.append(f"no {fmt['file']} delivered")
                else:
                    if not v or v["verdict"] != "confirmed":
                        problems.append(f"{who} does not confirm it: "
                                        + (f"{v['verdict']} ({v['status']})" if v else "not validated"))
                    producer = fmt["producer"](r["witness"])
                    answered = run["component"] if run["kind"] == "only" else answered_by(r)
                    if producer and component_of_producer(producer) not in (None, answered):
                        problems.append(f"witness producer {producer!r}, answered by {answered}")
                add(4, rundef, task, not problems, "witness", "; ".join(problems))
    # Check 2, verdicts of the references on their own tasks.
    for rundef, run in runs.items():
        if run["kind"] != "alone":
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r and c_owns(run, t) and r["verdict"] != t["expected"]:
                add(2, rundef, t["id"], False, "bad task pick",
                    f"{run['component']} alone gives {r['status']}, expected {str(t['expected']).lower()}")
            elif r and c_owns(run, t):
                add(2, rundef, t["id"], True, "", "")
    # Check 3.
    for rundef, run in runs.items():
        if run["kind"] != "only":
            continue
        for t in run["tasks"]:
            a, o = results.get((f"alone-{run['component']}", t["id"])), results.get((rundef, t["id"]))
            if a and o and a["verdict"] is not None:
                ok = o["verdict"] == a["verdict"]
                add(3, rundef, t["id"], ok, "differs from the component alone",
                    "" if ok else f"{run['component']} alone: {a['status']}; in CoOpeRace: {o['status']}")
    # Check 5.
    for rundef, run in runs.items():
        if run["kind"] != "production":
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r:
                ok = r["verdict"] == t["expected"]
                add(5, rundef, t["id"], ok, "missing or wrong verdict",
                    "" if ok else f"{r['status']}, expected {str(t['expected']).lower()}")
    return out


def c_owns(run, t):
    return run["component"] in t["owners"]


# ---------------------------------------------------------------- report

def cell(r, t):
    if r is None:
        return "-"
    s = r["status"] if r["verdict"] is None else str(r["verdict"]).lower()
    if r["verdict"] is not None and r["verdict"] != t["expected"]:
        s += " WRONG"
    v = r.get("validation")
    if r["verdict"] is False and not t["expected"]:
        s += " [" + ("no witness" if not r.get("witness") else v["verdict"] if v else "not validated") + "]"
    return f"{s} {r['cpu']:.0f}s"


def table(results, runs, tasks, skipped):
    cols = list(runs)
    rows = [["task (expected)"] + cols]
    for t in tasks:
        label = f"{t['name']} ({str(t['expected']).lower()}, {t['data_model']})"
        rows.append([label] + [cell(results.get((c, t["id"])), t) for c in cols])
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    for name, why in skipped.items():
        lines.append(f"{name}: {why}")
    return "\n".join(lines)


def report(checks, results, runs, tasks, skipped, out, meta=None):
    names = {1: "no wrong verdict", 2: "reference verdicts and witnesses on own tasks",
             3: "CoOpeRace with one component equals the component alone",
             4: "witness of every false is confirmed, produced by the answering component",
             5: "production configurations give the expected verdict"}
    lines = [table(results, runs, tasks, skipped), "",
             "cells: verdict, [validation of a false witness], CPU time; WRONG = verdict differs from the expected one", ""]
    failed = False
    for n in sorted(names):
        rel = [c for c in checks if c[0] == n]
        bad = [c for c in rel if not c[3]]
        failed |= bool(bad)
        lines.append(f"check {n} ({names[n]}): {'FAIL' if bad else 'pass'} "
                     f"({len(rel) - len(bad)} of {len(rel)} pass)")
        for _, rundef, task, ok, kind, detail in bad:
            lines.append(f"    FAIL [{kind}] {rundef} / {task}: {detail}")
    lines += ["", "result: " + ("FAIL" if failed else "pass")]
    text = "\n".join(lines) + "\n"
    (out / "table.txt").write_text(text)
    serial = {f"{rd}/{tk}": {k: (str(v) if isinstance(v, Path) else v) for k, v in r.items()}
              for (rd, tk), r in results.items()}
    (out / "results.json").write_text(json.dumps(
        dict(meta=meta, runs=serial,
             checks=[dict(check=n, run=rd, task=tk, ok=ok, kind=kd, detail=dt)
                     for n, rd, tk, ok, kd, dt in checks]), indent=1) + "\n")
    print(text)
    return 1 if failed else 0


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["run", "validate", "check"], default="run")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--cooperace-dir", default=os.environ.get("COOPERACE_DIR", str(REPO)),
                    help="the checkout to test; ./cooperace and tools/ are in it (default: this checkout)")
    ap.add_argument("--validators", default=os.environ.get("COOPERACE_VALIDATORS", str(REPO / "validators")),
                    help="directory made by scripts/download-validators.py (env COOPERACE_VALIDATORS)")
    ap.add_argument("--config", action="append", type=Path,
                    help="a further production configuration (JSON); default: conf/svcomp26.json and conf/svcomp25.json")
    ap.add_argument("--components", default=",".join(COMPONENTS))
    ap.add_argument("--tasks", action="append", help="only tasks whose name contains this")
    ap.add_argument("--cross", action="store_true",
                    help="run every component on every task, not only on its own")
    ap.add_argument("--no-validation", action="store_true")
    ap.add_argument("--allowed-cores", default=None, help="cores BenchExec may use, e.g. 14,15")
    ap.add_argument("-N", "--parallel", type=int, default=1)
    ap.add_argument("--cores", type=int, default=2, help="cores per run")
    ap.add_argument("--memlimit", default="4 GB")
    ap.add_argument("--timelimit", type=int, default=100, help="CPU seconds per run")
    ap.add_argument("--witness-format", choices=list(WITNESS_FORMATS), default="graphml-1.0")
    args = ap.parse_args()
    args.fmt = WITNESS_FORMATS[args.witness_format]

    try:
        sys.exit(suite(args))
    except CouldNotRun as e:
        print(f"run.py: {e}", file=sys.stderr)
        sys.exit(2)


def suite(args):
    """Runs the command of `args`; returns the exit status of `report`."""
    out = Path(args.out).resolve()
    if args.command == "run":
        out.mkdir(parents=True, exist_ok=True)
        manifest, tasks, runs, skipped, cdir = plan(args)
        if not runs:
            raise CouldNotRun("nothing to run: no component under tools/")
        meta = dict(machine=machine_description(cdir), cooperace_dir=str(cdir),
                    limits=dict(cores=args.cores, memlimit=args.memlimit,
                                cputime_s=args.timelimit, parallel=args.parallel,
                                allowed_cores=args.allowed_cores),
                    sv_benchmarks=manifest["sv_benchmarks"],
                    runs={n: dict({k: str(r[k]) for k in ("component", "conf") if k in r},
                                  kind=r["kind"], tasks=[t["id"] for t in r["tasks"]])
                          for n, r in runs.items()},
                    skipped=skipped)
        (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        verify(args, runs, cdir, out)
    else:
        try:
            meta = json.loads((out / "meta.json").read_text())
        except OSError as e:
            raise CouldNotRun(f"cannot read {out / 'meta.json'}, which `run` writes: {e.strerror}")
        except ValueError as e:
            raise CouldNotRun(f"{out / 'meta.json'} is not JSON: {e}")
        tasks, runs, skipped, cdir = plan_from_meta(meta)
    # Only the runs of this plan; out/verify may hold results of an earlier run.
    ids = {t["id"] for t in tasks}
    results = {(rundef, task): r for (rundef, task), r in read_results(out / "verify").items()
               if rundef in runs and task in ids}
    collect_witnesses(results, args.fmt, out)
    if args.command in ("run", "validate") and not args.no_validation:
        validate(args, results, tasks, out)
    elif args.command == "check":
        previous = read_results(out / "validate") if (out / "validate").exists() else {}
        for (rundef, task), r in previous.items():
            if (rundef, task) in results:
                results[(rundef, task)]["validation"] = dict(
                    status=r["status"], cpu=r["cpu"],
                    verdict="confirmed" if r["verdict"] is False else
                            "rejected" if r["verdict"] is True else "unknown")
    checks = check(results, runs, tasks, skipped, args.fmt)
    return report(checks, results, runs, tasks, skipped, out, meta)


if __name__ == "__main__":
    main()
