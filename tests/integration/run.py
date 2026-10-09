#!/usr/bin/env python3
"""Integration suite for the components of CoOpeRace.

For every component that is installed under tools/ (Goblint, Dartagnan,
ULTIMATE Automizer) the suite runs a handful of small no-data-race tasks

  * with the component alone, through the component's own BenchExec tool-info
    module and the options of its fm-tools entry for SV-COMP 2026 (the
    reference), under the BenchExec of the wheel in the checkout's lib/, the
    version whose tool-info modules CoOpeRace itself imports;
  * inside CoOpeRace with a one-component configuration (conf/only-*.json);
  * inside CoOpeRace with each production configuration (conf/svcomp26.json,
    conf/svcomp25.json, and any --config given);
  * inside CoOpeRace with each configuration of the suite (`configurations`
    in manifest.json, conf/svcomp26-goblint-1s.json), on the tasks that name
    it,

validates the witness of every `false` verdict with a SV-COMP 2026 violation
witness validator, and prints one table of task x configuration and the result
of these checks (see README.md):

  1  no run gives a wrong verdict
  2  each component alone gives the expected verdict on its own tasks, and a
     validator confirms its witness (otherwise the task is a bad pick)
  3  CoOpeRace with one component gives the verdict that component gives alone
  4  every `false` of CoOpeRace delivers a witness file that the validator
     named in manifest.json confirms, produced by the component that answered
  5  each production configuration and configuration of the suite gives the
     expected verdict on every task it runs
  6  every output of CoOpeRace follows the protocol: the verdict line last,
     `CoOpeRace result from: X` before it, each component block closed, no
     `CoOpeRace: error:`, one witness file for a `false`
  7  each component limit of the configuration (memoryLimits, cpuTimeLimits)
     is printed with the configured value whenever that component started
  8  in svcomp26-goblint-1s, the 1 s CPU-time limit ends a level of Goblint's
     portfolio and a later stage gives the expected verdict
  9  when a component of a parallel stage gives the returned verdict, every
     other component of that stage is stopped or answered unknown; no
     component runs twice

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
# (the `directory` of each entry of REGISTRY in src/cooperace/components.py).
TOOL_DIRS = {
    "Goblint": "goblint", "Deagle": "deagle", "Dartagnan": "dartagnan",
    "ULTIMATE Automizer": "uautomizer", "ULTIMATE GemCutter": "ugemcutter",
    "ULTIMATE Taipan": "utaipan", "nacpa": "nacpa",
    "CPAchecker": "cpachecker", "sv-sanitizers": "sv-sanitizers",
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
                                        "--option", "cpa.smg.memoryAllocationFunctions="
                                                    "malloc,__kmalloc,kmalloc,kzalloc,kzalloc_node,ldv_zalloc,ldv_malloc",
                                        "--option", "cpa.smg.arrayAllocationFunctions=calloc,kmalloc_array,kcalloc",
                                        "--option", "cpa.smg.zeroingMemoryAllocation="
                                                    "calloc,kzalloc,kcalloc,kzalloc_node,ldv_zalloc",
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
            raise CouldNotRun(f"missing task {yml}: {e.strerror}") from e
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


def bundled_benchexec(cdir):
    """The one BenchExec wheel `lib/benchexec-*.whl` of the checkout `cdir`,
    the BenchExec version whose tool-info modules CoOpeRace imports.  Raises
    CouldNotRun unless there is exactly one."""
    wheels = sorted((cdir / "lib").glob("benchexec-*.whl"))
    if len(wheels) != 1:
        raise CouldNotRun(f"expected one lib/benchexec-*.whl in {cdir}, found {len(wheels)}")
    return wheels[0]


def benchexec_environment(wheel=None):
    """The environment `benchexec` is started with.  BenchExec writes the
    environment of its own process into every result XML, so variables whose
    name contains TOKEN, SECRET, PASSWORD or KEY are left out; TMPDIR is /tmp,
    since a TMPDIR that BenchExec overlays makes its container refuse to start.
    With `wheel`, the wheel is first on PYTHONPATH, so that the `benchexec`
    command imports the BenchExec package (the executor and the tool-info
    modules) from the wheel and not from the system's installation."""
    env = {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}
    env["TMPDIR"] = "/tmp"
    if wheel is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(wheel)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def run_benchexec(definition, workdir, tool_directory, out_dir, name, args, wheel=None):
    """Run `benchexec` on `definition` with `workdir` as the working directory,
    with `wheel` (see benchexec_environment) as its BenchExec if given."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["benchexec", "--tool-directory", str(tool_directory), "-N", str(args.parallel),
           "--no-compress-results", "--name", name, "-o", str(out_dir) + os.sep]
    if args.allowed_cores:
        cmd += ["--allowedCores", args.allowed_cores]
    cmd.append(str(definition))
    prefix = f"PYTHONPATH={shlex.quote(str(wheel))} " if wheel is not None else ""
    print("+ (cd", workdir, "&&", prefix + " ".join(shlex.quote(c) for c in cmd), ")", flush=True)
    with open(out_dir / f"{name}.stdout.txt", "w") as log:
        proc = subprocess.run(cmd, cwd=workdir, env=benchexec_environment(wheel),
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
        memlimit = root.get("memlimit")
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
                memlimit=int(memlimit.rstrip("B")) if memlimit else None,
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


def read_conf(path):
    """The CoOpeRace configuration in the file `path`."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise CouldNotRun(f"cannot read the configuration {path}: {e}") from e


def plan(args):
    """The run definitions: alone-<c>, only-<c> for every component c, one per
    production configuration, and one per configuration of the suite
    (`configurations` in manifest.json); with the tasks each one runs.  A task
    that names `configurations` of its own is run by those only."""
    manifest, tasks = load_tasks(args.tasks)
    regular = [t for t in tasks if not t.get("configurations")]
    cdir = Path(args.cooperace_dir).resolve()
    present = [c for c in COMPONENTS if (cdir / "tools" / c).is_dir()
               and c in args.components.split(",")]
    runs, skipped = {}, {}
    for c in present:
        own = [t for t in regular if c in t["owners"]] if not args.cross else regular
        if own:
            runs[f"alone-{c}"] = dict(kind="alone", component=c, tasks=own)
            conf = HERE / "conf" / f"only-{c}.json"
            runs[f"only-{c}"] = dict(kind="only", component=c, tasks=own,
                                     conf=conf, config=read_conf(conf))
    for c in args.components.split(","):
        if c not in present:
            skipped[c] = f"tools/{c} is not installed"
    configs = args.config or [cdir / "conf" / "svcomp26.json", cdir / "conf" / "svcomp25.json"]
    production = [(Path(p).stem, Path(p), "production", regular, []) for p in configs]
    suite = [(name, HERE / entry["conf"], "suite",
              [t for t in tasks if name in t.get("configurations", ())], entry.get("checks", []))
             for name, entry in manifest.get("configurations", {}).items()]
    for name, path, kind, its_tasks, checks in production + suite:
        if not its_tasks:
            continue
        path = path.resolve()
        conf = read_conf(path)
        missing = [n for n in conf_components(conf)
                   if not (cdir / "tools" / TOOL_DIRS.get(n, n)).is_dir()]
        if missing:
            skipped[name] = "not run: " + ", ".join(sorted(set(missing))) + " not installed under tools/"
        else:
            runs[name] = dict(kind=kind, conf=path, config=conf, tasks=its_tasks, checks=checks)
    return manifest, tasks, runs, skipped, cdir


def plan_from_meta(meta):
    """The tasks and run definitions that `run` recorded in meta.json, for
    `validate` and `check`, in the form `plan` returns them."""
    manifest, all_tasks = load_tasks(None)
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
        if r["kind"] in ("only", "production", "suite"):
            default = (cdir / "conf" / f"{name}.json" if r["kind"] == "production"
                       else HERE / "conf" / f"{name}.json")
            run["conf"] = Path(r.get("conf", default))
            run["config"] = r["config"] if "config" in r else read_conf(run["conf"])
            run["checks"] = manifest.get("configurations", {}).get(name, {}).get("checks", [])
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
                benchexec_bundled=bundled_benchexec(cdir).name,
                cooperace_commit=git.stdout.strip() if git.returncode == 0 else None,
                cooperace_describe=git_describe(cdir),
                tools=tools.read_text().splitlines() if tools.exists() else [])


# ------------------------------------------------------------ verifiers

def verify(args, runs, cdir, out):
    """Run every verification run definition: the components alone (one
    BenchExec benchmark each, since each has its own tool-info module and
    working directory) and CoOpeRace's configurations (one benchmark, the
    configuration given as the option --conf).  A component alone runs under
    the BenchExec of `cdir`'s lib/ wheel, the one whose tool-info modules
    CoOpeRace uses; the other benchmarks use the installed BenchExec."""
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
        run_benchexec(definition, tool_dir, tool_dir, vdir, name, args,
                      wheel=bundled_benchexec(cdir))
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


# ---------------------------------------------------- CoOpeRace's output

VERDICT_LINE = re.compile(r"CoOpeRace verdict: (true|false|unknown)")
RESULT_FROM_LINE = re.compile(r"CoOpeRace result from: (.+)")
BLOCK_START = re.compile(r"---(?!end of )(.+) logs---")
BLOCK_END = re.compile(r"---end of (.+) logs---")
STATUS_LINE = re.compile(r"Tool name: (.+?) Status: (.*) Exit code: (.*)")
RESULT_LINE = re.compile(r"Tool name: (.+?) Result: (\w+)")


def cooperace_output(r):
    """The lines that CoOpeRace printed in the run `r`, without the header
    BenchExec writes above them in the log (the command line, blank lines and
    a line of dashes), and without trailing blank lines; None if the log
    cannot be read."""
    try:
        lines = Path(r["log"]).read_text(errors="replace").splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines[:10]):
        if re.fullmatch(r"-{40,}", line):
            lines = lines[i + 1:]
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def component_blocks(lines):
    """The component runs in CoOpeRace's output `lines`, as print_component_run
    of src/cooperace/components.py prints them: `---X logs---`, the component's output,
    `---end of X logs---`, `Tool name: X Status: S Exit code: E` and, unless
    CoOpeRace stopped the component, `Tool name: X Result: V`.  Returns the
    list of blocks in order, each a dict of name, output (list of lines),
    status, exit_code and result (None where the line is missing), and the
    list of the places where the output does not have this form."""
    blocks, problems = [], []
    i = 0
    while i < len(lines):
        start = BLOCK_START.fullmatch(lines[i])
        if not start:
            if BLOCK_END.fullmatch(lines[i]):
                problems.append(f"output line {i + 1}: {lines[i]!r} ends no block")
            i += 1
            continue
        name = start.group(1)
        j = i + 1
        while j < len(lines) and not BLOCK_START.fullmatch(lines[j]) and not BLOCK_END.fullmatch(lines[j]):
            j += 1
        if j == len(lines) or lines[j] != f"---end of {name} logs---":
            problems.append(f"output line {i + 1}: ---{name} logs--- has no ---end of {name} logs---")
            i = j
            continue
        block = dict(name=name, output=lines[i + 1:j], status=None, exit_code=None, result=None)
        k = j + 1
        status = STATUS_LINE.fullmatch(lines[k]) if k < len(lines) else None
        if status and status.group(1) == name:
            block.update(status=status.group(2), exit_code=status.group(3))
            k += 1
            result = RESULT_LINE.fullmatch(lines[k]) if k < len(lines) else None
            if result and result.group(1) == name:
                block["result"] = result.group(2)
                k += 1
        else:
            problems.append(f"output line {k + 1}: no `Tool name: {name} Status: ...` after its block")
        blocks.append(block)
        i = k
    return blocks, problems


def answering_name(lines):
    """The name, as CoOpeRace prints it, of the component whose verdict
    CoOpeRace returned: the `CoOpeRace result from: X` line of its output
    `lines`, or, in an output without it, the first `Tool name: X Result:
    true|false` line; None if there is neither."""
    for line in lines:
        m = RESULT_FROM_LINE.fullmatch(line)
        if m:
            return m.group(1)
    for line in lines:
        m = RESULT_LINE.fullmatch(line)
        if m and m.group(2) in ("true", "false"):
            return m.group(1)
    return None


def answered_by(r):
    """The component whose verdict CoOpeRace returned in the run `r`, as a key
    of COMPONENTS (or the name CoOpeRace printed, for a component not in
    COMPONENTS), from answering_name; None if there is none."""
    name = answering_name(cooperace_output(r) or [])
    for c, comp in COMPONENTS.items():
        if comp["name"] == name:
            return c
    return name


def is_graphml(path):
    try:
        return ET.parse(path).getroot().tag == "{http://graphml.graphdrawing.org/xmlns}graphml"
    except (ET.ParseError, OSError):
        return False


def is_yaml_witness(path):
    try:
        import yaml  # a BenchExec dependency
        return isinstance(yaml.safe_load(Path(path).read_text()), list)
    except Exception:
        return False


# The names CoOpeRace delivers a witness under (witness_files_to_file_root of
# src/cooperace/components.py), with a test that the file has that name's format.
DELIVERED_WITNESSES = {"witness.graphml": is_graphml, "witness.yml": is_yaml_witness}


def limit_problems(run, r, lines):
    """What is wrong with the lines in which CoOpeRace announces the limits of
    `memoryLimits` and `cpuTimeLimits` of the run's configuration (check 7):
    each component with a limit that started must have its line, and every
    such line must give the configured limit, a percentage taken of the
    memory limit BenchExec records for the run, within 1 %."""
    config = run.get("config") or {}
    if lines is None or not (config.get("memoryLimits") or config.get("cpuTimeLimits")):
        return []
    blocks, _ = component_blocks(lines)
    started = {b["name"] for b in blocks if b["exit_code"] != "none, not started"}
    problems = []
    # The lines with_resource_limits of src/cooperace/components.py prints.
    for key, label, unit in (("memoryLimits", "Memory limit", "bytes"),
                             ("cpuTimeLimits", "CPU-time limit", "s")):
        for name, value in config.get(key, {}).items():
            if isinstance(value, str) and value.endswith("%"):
                if not r.get("memlimit"):
                    problems.append(f"{key} gives {name} {value}, but BenchExec records no memory limit")
                    continue
                expected = r["memlimit"] * float(value[:-1]) / 100
                wanted = f"{value} of the run's {r['memlimit']} bytes"
            else:
                expected, wanted = float(value), f"{value} {unit}"
            line_of = re.compile(rf"{label} of {re.escape(name)}: (\d+) {unit}\b")
            printed = [int(m.group(1)) for line in lines if (m := line_of.match(line))]
            if name in started and not printed:
                problems.append(f"{name} started without a line `{label} of {name}: N {unit}` "
                                f"({key}: {wanted})")
            for n in printed:
                if abs(n - expected) > 0.01 * expected:
                    problems.append(f"{name}'s limit is printed as {n} {unit}, but {key} gives {wanted}")
    return problems


def goblint_cpu_limit_problems(r, t, lines):
    """What departs, in the run `r` of task `t`, from what a CPU-time limit of
    Goblint that ends a level of its portfolio should cause (check 8): the
    portfolio runner reports `goblint exited with code -24` (SIGXCPU) in
    Goblint's block, Goblint's stage ends without a verdict, and a component
    of a later stage gives the expected verdict."""
    if lines is None:
        return [f"cannot read the log {r['log']}"]
    blocks, _ = component_blocks(lines)
    goblint = [b for b in blocks if b["name"] == COMPONENTS["goblint"]["name"]]
    problems = []
    if not goblint:
        return ["Goblint did not run"]
    if not any("goblint exited with code -24" in line for line in goblint[0]["output"]):
        problems.append("no level of Goblint's portfolio ended with `goblint exited with code -24`")
    if goblint[0]["result"] != "unknown":
        problems.append(f"Goblint's result is {goblint[0]['result']}, not unknown")
    answered = answering_name(lines)
    if answered in (None, COMPONENTS["goblint"]["name"]):
        problems.append(f"the verdict is not from a later stage (answered by {answered})")
    if r["verdict"] != t["expected"]:
        problems.append(f"{r['status']}, expected {str(t['expected']).lower()}")
    return problems


def parallel_siblings(config, name):
    """The other components of the parallel stage of the configuration
    `config` that holds the component `name` directly, as config.load of
    src/cooperace/config.py reads it: the list `tools` runs as `runType` says, and each list nested
    in it the other way.  Empty if `name` is in a sequential list."""
    def walk(node, parallel):
        for element in node:
            if isinstance(element, list):
                found = walk(element, not parallel)
                if found is not None:
                    return found
            elif name in element:
                if not parallel:
                    return []
                return [n for e in node if isinstance(e, dict) for n in e if n != name]
        return None
    return walk(config.get("tools", []), config.get("runType") == "parallel") or []


def parallel_stop_problems(run, lines):
    """What in CoOpeRace's output `lines` shows that the components of a
    parallel stage were not stopped when one of them gave the returned
    verdict (check 9): every other component of that stage must have
    `Status: stopped by CoOpeRace` or `Result: unknown`; and no component's
    block may appear twice."""
    if lines is None:
        return []
    blocks, _ = component_blocks(lines)
    names = [b["name"] for b in blocks]
    problems = [f"{n} has {names.count(n)} blocks" for n in sorted(set(names)) if names.count(n) > 1]
    answered = answering_name(lines)
    if answered:
        by_name = {b["name"]: b for b in blocks}
        for sibling in parallel_siblings(run.get("config") or {}, answered):
            b = by_name.get(sibling)
            if b is None:
                problems.append(f"{sibling}, beside {answered}, has no block")
            elif b["status"] != "stopped by CoOpeRace" and b["result"] != "unknown":
                problems.append(f"{sibling}, beside {answered}, has status {b['status']} "
                                f"and result {b['result']}")
    return problems


def protocol_problems(r, lines):
    """What in the run `r` of CoOpeRace, whose output is `lines`, departs from
    the protocol that benchexec.tools.cooperace and this suite rely on (check
    6); empty if nothing does."""
    if lines is None:
        return [f"cannot read the log {r['log']}"]
    problems = []
    if r["reason"]:
        # BenchExec ended the run: there is no final line to check.
        pass
    elif not lines or not VERDICT_LINE.fullmatch(lines[-1]):
        problems.append(f"the last line is {lines[-1] if lines else ''!r}, "
                        "not `CoOpeRace verdict: true|false|unknown`")
    else:
        verdict = VERDICT_LINE.fullmatch(lines[-1]).group(1)
        from_lines = [line for line in lines if RESULT_FROM_LINE.fullmatch(line)]
        if verdict == "unknown" and from_lines:
            problems.append(f"{from_lines[0]!r} with verdict unknown")
        elif verdict != "unknown" and (len(from_lines) != 1 or lines[-2] != from_lines[0]):
            problems.append("not exactly one `CoOpeRace result from: X`, right before the verdict line")
    blocks, structure = component_blocks(lines)
    problems += structure
    if any(line.startswith("CoOpeRace: error:") for line in lines):
        problems.append("`CoOpeRace: error:` in the output")
    if r["verdict"] is False:
        files = sorted(p for p in Path(r["files"]).rglob("*") if p.is_file()) if Path(r["files"]).is_dir() else []
        names = [p.relative_to(r["files"]).as_posix() for p in files]
        if len(files) != 1:
            problems.append(f"{len(files)} witness files in the result files ({', '.join(names) or 'none'}), not 1")
        elif names[0] not in DELIVERED_WITNESSES:
            problems.append(f"the witness file is {names[0]}, not one of {', '.join(DELIVERED_WITNESSES)}")
        elif not DELIVERED_WITNESSES[names[0]](files[0]):
            problems.append(f"{names[0]} does not have the format its name says")
    return problems


# --------------------------------------------------------------- checks

def check(results, runs, tasks, skipped, fmt):
    """The checks; a list of (check, run definition, task, ok, kind, detail)."""
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
    # Check 6.
    for rundef, run in runs.items():
        if run["kind"] == "alone":
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r:
                problems = protocol_problems(r, cooperace_output(r))
                add(6, rundef, t["id"], not problems, "protocol", "; ".join(problems))
    # Check 7.
    for rundef, run in runs.items():
        config = run.get("config") or {}
        if run["kind"] == "alone" or not (config.get("memoryLimits") or config.get("cpuTimeLimits")):
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r:
                problems = limit_problems(run, r, cooperace_output(r))
                add(7, rundef, t["id"], not problems, "limit line", "; ".join(problems))
    # Check 8.
    for rundef, run in runs.items():
        if "goblint-cpu-limit" not in run.get("checks", ()):
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r:
                problems = goblint_cpu_limit_problems(r, t, cooperace_output(r))
                add(8, rundef, t["id"], not problems, "Goblint's CPU-time limit", "; ".join(problems))
    # Check 9.
    for rundef, run in runs.items():
        if run["kind"] == "alone":
            continue
        for t in run["tasks"]:
            r = results.get((rundef, t["id"]))
            if r:
                problems = parallel_stop_problems(run, cooperace_output(r))
                add(9, rundef, t["id"], not problems, "parallel stop", "; ".join(problems))
    # Check 5.
    for rundef, run in runs.items():
        if run["kind"] not in ("production", "suite"):
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
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    for name, why in skipped.items():
        lines.append(f"{name}: {why}")
    return "\n".join(lines)


def report(checks, results, runs, tasks, skipped, out, meta=None):
    names = {1: "no wrong verdict", 2: "reference verdicts and witnesses on own tasks",
             3: "CoOpeRace with one component equals the component alone",
             4: "witness of every false is confirmed, produced by the answering component",
             5: "production and suite configurations give the expected verdict",
             6: "every CoOpeRace output follows the protocol",
             7: "CoOpeRace prints the configured component limits",
             8: "Goblint's CPU-time limit ends a level and a later stage answers",
             9: "a parallel stage stops its other components once one answers"}
    lines = [table(results, runs, tasks, skipped), "",
             "cells: verdict, [validation of a false witness], CPU time; "
             "WRONG = verdict differs from the expected one", ""]
    failed = False
    for n in sorted(names):
        rel = [c for c in checks if c[0] == n]
        bad = [c for c in rel if not c[3]]
        failed |= bool(bad)
        lines.append(f"check {n} ({names[n]}): {'FAIL' if bad else 'pass'} "
                     f"({len(rel) - len(bad)} of {len(rel)} pass)")
        for _, rundef, task, _ok, kind, detail in bad:
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
                    help="a further production configuration (JSON); "
                         "default: conf/svcomp26.json and conf/svcomp25.json")
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
                                  **({"config": r["config"]} if "config" in r else {}),
                                  kind=r["kind"], tasks=[t["id"] for t in r["tasks"]])
                          for n, r in runs.items()},
                    skipped=skipped)
        (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        verify(args, runs, cdir, out)
    else:
        try:
            meta = json.loads((out / "meta.json").read_text())
        except OSError as e:
            raise CouldNotRun(f"cannot read {out / 'meta.json'}, which `run` writes: {e.strerror}") from e
        except ValueError as e:
            raise CouldNotRun(f"{out / 'meta.json'} is not JSON: {e}") from e
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
