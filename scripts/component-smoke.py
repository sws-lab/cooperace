#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Component smoke harness: does CoOpeRace run this archive of a component?

When a component's archive changes, what breaks usually shows only inside
CoOpeRace: a renamed witness file, a new option the tool-info module must
pass, an output line the module no longer reads. This script takes one
archive of one component and answers within minutes:

1. It fetches the archive as scripts/download-tools.py does (the single file
   of the Zenodo record, its MD5 checked against Zenodo's, unpacked with one
   top-level directory and the file modes kept), records its size, SHA-1 and
   SHA-256, and runs the static checks of scripts/archive-static-check.py.
   A Zenodo record id, a local zip and an unpacked directory are accepted;
   the default is the DOI that tools.txt or tools-pool.txt names. Each
   archive goes into a new directory of its own under --work.
2. It runs the component the way CoOpeRace runs it: a copy of CoOpeRace (its
   src/, lib/ and conf/witnesses.json) whose default conf has one strategy
   per property with the component alone, with the limits, `ignoreIfCalls`
   and phase wall-time limit that conf/svcomp27.json gives it, and whose
   tools-options.json holds the options of the archive's fm-tools version.
   It runs through BenchExec's `benchexec` and CoOpeRace's tool-info module in
   the competition image, with the competition's BenchExec options, at the
   pre-run limits (3 GB, 1 core, 15 min), one task at a time
   (scripts/component-smoke-inside.sh). The launcher of the copy runs
   cli.main unchanged and only observes: it keeps CoOpeRace's work directory
   (the component's run directory, with its witness files) and lists every
   file each component run wrote anywhere in its container.
3. It reports per task the component's status, what CoOpeRace made of it
   (the "Result:" of confirm_verdict, the verdict line), the expected verdict
   and the pre-run row of the task, the witness files written (name, entry
   type, format version), whether CoOpeRace's witness check passed the one it
   delivered, WitnessLint's exit code on each, CPU time and peak memory
   against the pre-run, the peak number of tasks of the component's cgroup,
   the files written outside the component's directory, and witness files
   the archive ships. It writes results.tsv and summary.md into the run's
   directory and exits 1 with one line "FAIL <component> <task>: <reason>"
   per failure.

Commands (python3 3.10+, PyYAML for the fm-tools entries):

  component-smoke.py run COMPONENT [--archive ID|ZIP|DIR|registered] ...
  component-smoke.py all [--tools-list FILE ...] ...
  component-smoke.py report RUN_DIR
  component-smoke.py select --table verified.parquet --svb DIR > tasks.tsv

COMPONENT is the component's fm-tools name (its directory under tools/). A
component without a registry entry is not run: the script says "no registry
entry" and lists what an entry would need. The tasks are those of
scripts/component-smoke-tasks.tsv for the component (`select` wrote it from
the pre-run table); a component of the registry that no conf property uses
is run on the properties that file gives it, without limits of its own."""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "scripts" / "component-smoke-tasks.tsv"
INSIDE_SCRIPT = ROOT / "scripts" / "component-smoke-inside.sh"
STATIC_CHECK = ROOT / "scripts" / "archive-static-check.py"
ZENODO_API = "https://zenodo.org/api/records/{record}"
FM_TOOLS_DATA_URL = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/{name}.yml"
DOI_PREFIX = "10.5281/zenodo."
WITNESS_PLACEHOLDER = "${witness}"
# The pre-run limits: what the organizers' pre-runs give a verifier
PRERUN_ATTRS = 'timelimit="15 min" hardtimelimit="16 min" memlimit="3 GB" cpuCores="1"'
PRERUN_MEMORY = 3 * 10**9
# Free space that must remain on the disk of --work after unpacking an archive
MIN_FREE_BYTES = 15 * 10**9
PROPERTIES = ("no-data-race", "unreach-call", "no-overflow", "valid-memsafety")
TASK_COLUMNS = ("component", "property", "role", "task", "expected", "prerun_doi", "prerun_status",
                "prerun_cpu", "prerun_mem")
RESULT_COLUMNS = ("id", "component", "archive", "property", "role", "task", "expected", "status",
                  "result", "verdict", "conf_trusts", "prerun_status", "prerun_of", "cpu", "prerun_cpu",
                  "mem_MB", "prerun_mem_MB", "termination", "tasks_peak", "witness_files", "witness_delivered",
                  "witness_check", "witnesslint", "written_outside", "notes", "failures")
# The launcher of the CoOpeRace copy: cli.main unchanged, plus two observations
LAUNCHER = '''#!/usr/bin/env python3
"""The launcher of a CoOpeRace copy made by scripts/component-smoke.py. It runs
src/cooperace/cli.py's main unchanged and only observes: before CoOpeRace
removes its work directory it copies it to smoke/work/ in the working
directory, and when BenchExec collects a component run's result files it
first lists every file the run wrote anywhere in its container into
smoke/written/<run directory>.tsv. A run without result files gets a pattern
that matches no file, so that BenchExec calls the collection."""
import os
import shutil

from src.cooperace import cli, components
from benchexec import container, containerexecutor

SMOKE = os.path.join(os.getcwd(), "smoke")
NO_FILE = "component-smoke-matches-no-file"
_cleanup = components.ComponentRunner.cleanup
_patterns = components.result_files_patterns
_transfer = containerexecutor.ContainerExecutor._transfer_output_files


def cleanup(self):
    if self.work_dir and os.path.isdir(self.work_dir):
        shutil.copytree(self.work_dir, os.path.join(SMOKE, "work"), symlinks=True, dirs_exist_ok=True)
    _cleanup(self)


def result_files_patterns(witness):
    return _patterns(witness) or [NO_FILE]


def transfer(self, tool_output_dir, working_dir, output_dir, patterns):
    try:
        os.makedirs(os.path.join(SMOKE, "written"), exist_ok=True)
        with open(os.path.join(SMOKE, "written", os.path.basename(output_dir) + ".tsv"), "w") as out:
            out.write(f"#cwd\\t{working_dir}\\t{output_dir}\\n")
            for root, _dirs, files in os.walk(tool_output_dir):
                for name in files:
                    path = os.path.join(root, name)
                    inside = os.path.join("/", os.path.relpath(path, tool_output_dir))
                    if (os.path.isfile(path) and not os.path.islink(path)
                            and not container.is_container_system_config_file(inside)):
                        out.write(f"{inside}\\t{os.path.getsize(path)}\\n")
    except OSError as error:
        print(f"component-smoke: cannot list the written files: {error}")
    return _transfer(self, tool_output_dir, working_dir, output_dir, patterns)


components.ComponentRunner.cleanup = cleanup
components.result_files_patterns = result_files_patterns
containerexecutor.ContainerExecutor._transfer_output_files = transfer

if __name__ == "__main__":
    cli.main()
'''


class SmokeError(Exception):
    """A problem that stops the smoke of one component before any task runs."""


# ---------------------------------------------------------------------------
# CoOpeRace's own registry and conf, from the tree given with --cooperace

def load_cooperace(tree: Path):
    """The modules components, config and witnesses of the CoOpeRace tree
    `tree` (its src/cooperace, which puts the tree's BenchExec wheel first on
    sys.path)."""
    sys.path.insert(0, str(tree))
    from src.cooperace import components, config, witnesses  # noqa: PLC0415
    return components, config, witnesses


def registry_entry(components, name: str):
    """The ComponentSpec whose directory (fm-tools name) is `name`, or None."""
    for spec in components.REGISTRY.values():
        if spec.directory == name:
            return spec
    return None


def single_component_conf(conf: dict, conf_name: str, properties: list[str]) -> dict:
    """A conf with one strategy per property of `properties`: the component
    `conf_name` alone, with the limits, `ignoreIfCalls` entries, wall-time
    limit of its first phase and runCpuTimeBudget that `conf` gives it there,
    trusted for both verdicts and supplying its own witness. A property in
    which `conf` does not run it gets the component without limits of its
    own, under the budget of 870 s of CPU time."""
    strategies = {}
    for prop in properties:
        base = conf.get("properties", {}).get(prop, {})
        phases = [phase if isinstance(phase, dict) else {"tools": phase} for phase in base.get("phases", [])]
        mine = [phase for phase in phases if conf_name in phase["tools"]]
        phase = {"tools": [conf_name]}
        if mine and "wallTimeLimit" in mine[0]:
            phase["wallTimeLimit"] = mine[0]["wallTimeLimit"]
        strategy = {"phases": [phase], "answers": [[conf_name]], "witnesses": [conf_name],
                    "runCpuTimeBudget": base.get("runCpuTimeBudget", 870)}
        for key in ("cpuTimeLimits", "memoryLimits"):
            if conf_name in base.get(key, {}):
                strategy[key] = {conf_name: base[key][conf_name]}
        guards = {entry: names for entry, names in base.get("ignoreIfCalls", {}).items()
                  if entry.split(":")[0] == conf_name}
        if guards:
            strategy["ignoreIfCalls"] = guards
        strategies[prop] = strategy
    return {"properties": strategies}


def conf_properties(conf: dict, conf_name: str) -> list[str]:
    """The properties in whose strategy `conf` runs the component `conf_name`."""
    found = []
    for prop, strategy in conf.get("properties", {}).items():
        for phase in strategy.get("phases", []):
            tools = phase["tools"] if isinstance(phase, dict) else phase
            if conf_name in tools:
                found.append(prop)
                break
    return found


def conf_trusts(conf: dict, conf_name: str, prop: str) -> str:
    """Which verdicts of `conf_name` the default conf's `answers` of `prop`
    trust: "true", "false", "both" or "none"."""
    verdicts = set()
    for bracket in conf.get("properties", {}).get(prop, {}).get("answers", []):
        for entry in bracket:
            tool, _, verdict = entry.partition(":")
            if tool == conf_name:
                verdicts.update([verdict] if verdict else ["true", "false"])
    return "both" if verdicts == {"true", "false"} else (verdicts.pop() if verdicts else "none")


# ---------------------------------------------------------------------------
# fm-tools, lock files, archives

def read_lock(path: Path) -> dict[str, str]:
    """{name: doi} of a lock file (tools.txt, tools-pool.txt)."""
    wanted = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                name, _, doi = line.partition(":")
                wanted[name.strip()] = doi.strip().split()[0]
    return wanted


def read_unlisted(path: Path) -> dict[str, tuple[str, str]]:
    """{name: (doi, fm-tools commit)} of tools-unlisted.txt."""
    found = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if len(parts) == 2:
                    found[name.strip()] = (parts[0], parts[1])
    return found


def fm_tools_data(name: str, fm_tools_dir: Path | None, revision: str | None = None) -> dict | None:
    """The fm-tools entry `name` (data/<name>.yml) of the checkout
    `fm_tools_dir` at `revision` (its HEAD if None; `git show`), else of
    fm-tools main on gitlab.com; None if there is none."""
    import yaml  # noqa: PLC0415

    try:
        if fm_tools_dir is not None:
            if revision is None:
                path = fm_tools_dir / "data" / f"{name}.yml"
                text = path.read_text() if path.is_file() else None
            else:
                shown = subprocess.run(["git", "-C", str(fm_tools_dir), "show", f"{revision}:data/{name}.yml"],
                                       capture_output=True, text=True)
                text = shown.stdout if shown.returncode == 0 else None
        else:
            url = FM_TOOLS_DATA_URL.format(name=name).replace("/main/", f"/{revision or 'main'}/")
            with urllib.request.urlopen(url, timeout=30) as response:
                text = response.read().decode()
    except Exception:  # noqa: BLE001 - reported as no entry
        return None
    return yaml.safe_load(text) if text else None


def verifier_versions(data: dict) -> list[dict]:
    """The versions of an fm-tools entry that are not witness validators."""
    return [entry for entry in data.get("versions") or [] if isinstance(entry, dict)
            and not any(WITNESS_PLACEHOLDER in str(option) for option in entry.get("benchexec_toolinfo_options") or [])]


def version_for(data: dict | None, doi: str) -> dict | None:
    """The first verifier version of the entry `data` with the DOI `doi`."""
    for entry in verifier_versions(data or {}):
        if str(entry.get("doi")) == doi:
            return entry
    return None


@dataclass
class Archive:
    """An archive of a component under test: `doi` (or "local:<path>"),
    `directory` the unpacked component directory, `version` and `options` of
    its fm-tools version, `record` what is known of its origin (size,
    hashes, Zenodo's MD5), `version_source` where the options come from."""

    component: str
    doi: str
    directory: Path
    version: str
    options: list[str]
    record: dict = field(default_factory=dict)
    version_source: str = ""
    packages: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.doi.removeprefix(DOI_PREFIX) if self.doi.startswith(DOI_PREFIX) else Path(self.doi).name


def file_hashes(path: Path) -> dict:
    """Size, MD5, SHA-1 and SHA-256 of the file `path`."""
    digests = {name: hashlib.new(name) for name in ("md5", "sha1", "sha256")}
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            for digest in digests.values():
                digest.update(chunk)
    return {"size": path.stat().st_size, **{name: digest.hexdigest() for name, digest in digests.items()}}


def zenodo_file(record: str) -> tuple[str, str, int, str]:
    """(url, md5, size, name) of the single file of the Zenodo record
    `record`; SmokeError for a record with another number of files."""
    with urllib.request.urlopen(ZENODO_API.format(record=record), timeout=60) as response:
        data = json.load(response)
    files = data.get("files") or []
    if len(files) != 1:
        raise SmokeError(f"Zenodo record {record} has {len(files)} files; fm-tools takes exactly one")
    entry = files[0]
    return entry["links"]["self"], entry["checksum"].removeprefix("md5:"), int(entry["size"]), entry["key"]


def check_free_space(directory: Path, needed: int) -> None:
    """SmokeError if fewer than MIN_FREE_BYTES would remain on the disk of
    `directory` after writing `needed` more bytes."""
    free = shutil.disk_usage(directory).free
    if free - needed < MIN_FREE_BYTES:
        raise SmokeError(f"{free / 1e9:.1f} GB free on the disk of {directory}; {needed / 1e9:.1f} GB more "
                         f"would leave less than {MIN_FREE_BYTES / 1e9:.0f} GB")


def unpack(zip_path: Path, target: Path, name: str) -> Path:
    """Unpacks `zip_path` as fm-tools' unzip does (exactly one top-level
    directory; members extracted one by one with their file modes) into the
    new directory `target`, and returns target/<name>, the component
    directory. zipfile.extract keeps every member under `target`."""
    with zipfile.ZipFile(zip_path) as archive:
        tops = {member.filename.split("/")[0] for member in archive.infolist() if member.filename.count("/") <= 1}
        if len(tops) != 1:
            raise SmokeError(f"{zip_path}: {len(tops)} top-level entries; fm-tools expects exactly one directory")
        size = sum(member.file_size for member in archive.infolist())
        check_free_space(target.parent, size)
        target.mkdir(parents=True)
        for member in archive.infolist():
            extracted = archive.extract(member, target)
            mode = member.external_attr >> 16
            if mode:
                os.chmod(extracted, mode & 0o7777)
    top = target / tops.pop()
    component_dir = target / name
    if top != component_dir:
        top.rename(component_dir)
    return component_dir


def resolve_archive(component: str, archive_arg: str | None, tree: Path, work: Path,
                    fm_tools_dir: Path | None) -> Archive:
    """The Archive of `component` that `archive_arg` names (see the module
    docstring), fetched and unpacked into a new directory under
    work/archives/ unless it is an unpacked directory."""
    lock = {**read_lock(tree / "tools-pool.txt"), **read_lock(tree / "tools.txt")}
    unlisted = read_unlisted(tree / "tools-unlisted.txt")
    data = fm_tools_data(component, fm_tools_dir)
    if archive_arg in (None, "bundled"):
        if component not in lock:
            raise SmokeError(f"{component} is in neither tools.txt nor tools-pool.txt; give --archive")
        doi = lock[component]
    elif archive_arg == "registered":
        versions = [entry for entry in verifier_versions(data or {}) if entry.get("doi")]
        if not versions:
            raise SmokeError(f"fm-tools registers no verifier version of {component} with a DOI")
        doi = str(versions[0]["doi"])
    elif re.fullmatch(r"\d+", archive_arg):
        doi = DOI_PREFIX + archive_arg
    elif archive_arg.startswith(DOI_PREFIX):
        doi = archive_arg
    else:
        doi = None
    local = Path(archive_arg) if doi is None and archive_arg else None

    # The fm-tools version: of the entry now, else of the commit tools-unlisted.txt names
    entry, source = None, ""
    if doi is not None:
        entry = version_for(data, doi)
        source = "fm-tools " + ("main" if fm_tools_dir is None else str(fm_tools_dir))
        if entry is None and component in unlisted and unlisted[component][0] == doi:
            commit = unlisted[component][1]
            entry = version_for(fm_tools_data(component, fm_tools_dir, commit), doi)
            source = f"fm-tools {commit} (tools-unlisted.txt)"
        if entry is None:
            raise SmokeError(f"fm-tools lists {doi} for no verifier version of {component}")
    elif local is not None and local.is_dir():
        recorded = (local / ".doi").read_text().strip() if (local / ".doi").is_file() else None
        if not recorded:
            # An unpacked archive without a record: the record id in its path names the DOI
            match = re.findall(r"(?<!\d)(\d{7,9})(?!\d)", str(local))
            if not match:
                raise SmokeError(f"{local} has no .doi and no Zenodo record id in its path; give the zip")
            recorded = DOI_PREFIX + match[-1]
        return resolve_installed(component, local, recorded, data, unlisted, fm_tools_dir)
    if local is not None and not local.exists():
        raise SmokeError(f"{local} does not exist")
    if local is not None and entry is None:
        # A local archive: take the version of fm-tools whose DOI the file name holds, else the newest
        match = re.findall(r"(?<!\d)(\d{7,9})(?!\d)", str(local))
        entry = version_for(data, DOI_PREFIX + match[-1]) if match else None
        if entry is None:
            versions = verifier_versions(data or {})
            entry = versions[0] if versions else {"version": "unknown", "benchexec_toolinfo_options": []}
            source = "newest fm-tools version (local archive of unknown DOI)"
        else:
            source = "fm-tools, by the record id in the path"
        doi = str(entry.get("doi") or f"local:{local}")

    label = doi.removeprefix(DOI_PREFIX)
    target = work / "archives" / f"{component}-{label}-{stamp()}"
    record: dict = {"doi": doi}
    if local is not None:
        zip_path = local
        record["source"] = str(local)
        target.mkdir(parents=True)
    else:
        url, md5, size, name = zenodo_file(label)
        check_free_space(work, 3 * size)
        target.mkdir(parents=True)
        zip_path = target / name
        with urllib.request.urlopen(url, timeout=120) as response, open(zip_path, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        record.update(source=url, zenodo_md5=md5, zenodo_size=size)
    record.update(file_hashes(zip_path))
    if "zenodo_md5" in record and record["zenodo_md5"] != record["md5"]:
        raise SmokeError(f"{zip_path}: MD5 {record['md5']} is not Zenodo's {record['zenodo_md5']}")
    component_dir = unpack(zip_path, target / "unpacked", component)
    (component_dir / ".doi").write_text(doi + "\n")
    (target / "archive.json").write_text(json.dumps(record, indent=1) + "\n")
    return Archive(component, doi, component_dir, str(entry["version"]),
                   [str(option) for option in entry.get("benchexec_toolinfo_options") or []], record, source,
                   [str(name) for name in entry.get("required_ubuntu_packages") or []])


def resolve_installed(component, directory, doi, data, unlisted, fm_tools_dir) -> Archive:
    """The Archive of a directory that download-tools.py installed (its
    .doi names the DOI); it is run where it is, read-only."""
    entry = version_for(data, doi)
    source = "fm-tools " + ("main" if fm_tools_dir is None else str(fm_tools_dir))
    if entry is None and component in unlisted and unlisted[component][0] == doi:
        entry = version_for(fm_tools_data(component, fm_tools_dir, unlisted[component][1]), doi)
        source = f"fm-tools {unlisted[component][1]} (tools-unlisted.txt)"
    if entry is None:
        raise SmokeError(f"fm-tools lists {doi} (from {directory}/.doi) for no verifier version of {component}")
    return Archive(component, doi, directory, str(entry["version"]),
                   [str(option) for option in entry.get("benchexec_toolinfo_options") or []],
                   {"doi": doi, "source": f"installed directory {directory}"}, source,
                   [str(name) for name in entry.get("required_ubuntu_packages") or []])


def stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# Static checks and leftovers

def static_checks(component: str, directory: Path) -> list[tuple[str, str]]:
    """[(severity, line)] of scripts/archive-static-check.py on `directory`
    ("ok", "FAIL" or "warn"); one "note" if it has no checks for `component`."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("archive_static_check", STATIC_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if component not in module.COMPONENTS:
        return [("note", f"{component}: archive-static-check.py has no checks for it")]
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main(component, str(directory))
    return [(line.split()[0], line.split(None, 1)[1]) for line in buffer.getvalue().splitlines() if line.strip()]


def package_check(archive: Archive, fm_tools_dir: Path | None) -> list[tuple[str, str]]:
    """One line: whether the newest version of CoOpeRace's own fm-tools entry
    lists every required_ubuntu_packages of the archive's version."""
    own = verifier_versions(fm_tools_data("cooperace", fm_tools_dir) or {})
    if not own:
        return [("note", "no fm-tools entry of CoOpeRace to compare required_ubuntu_packages with")]
    missing = sorted(set(archive.packages) - {str(name) for name in own[0].get("required_ubuntu_packages") or []})
    if missing:
        return [("FAIL", f"CoOpeRace's fm-tools entry ({own[0].get('version')}) lacks required_ubuntu_packages "
                         f"{', '.join(missing)} of {archive.component} {archive.version}")]
    return [("ok", f"CoOpeRace's fm-tools entry ({own[0].get('version')}) lists the required_ubuntu_packages "
                   f"of {archive.component} {archive.version}")]


def shipped_witnesses(directory: Path) -> list[str]:
    """The files of the archive whose name contains "witness" (any case) and
    ends in yml, yaml or graphml: leftovers that a run must not take."""
    found = []
    for path in directory.rglob("*"):
        name = path.name.lower()
        if path.is_file() and "witness" in name and name.endswith(("yml", "yaml", "graphml")):
            found.append(str(path.relative_to(directory)))
    return sorted(found)


# ---------------------------------------------------------------------------
# Tasks

def read_tasks(path: Path, component: str, properties: list[str] | None) -> list[dict]:
    """The rows of the task file `path` for `component`, of `properties` only
    if given."""
    with open(path, newline="") as file:
        rows = [row for row in csv.DictReader((line for line in file if not line.startswith("#")), delimiter="\t")]
    return [row for row in rows if row["component"] == component and (not properties or row["property"] in properties)]


# ---------------------------------------------------------------------------
# Parsing CoOpeRace's output

# Lines of a component's output that look like a verdict (SV-COMP result: of Goblint and sv-sanitizers,
# Result: and FALSE(...) of ULTIMATE, VERIFICATION SUCCESSFUL/FAILED of CBMC-based tools, PASS/FAIL and
# Flag data-race of Dartagnan, SafetyResult of Theta, IKOS-ConC's SAFE/UNSAFE lines)
VERDICT_WORDS = re.compile(r"SV-COMP result|^\s*(RESULT|Result)\b|VERIFICATION (SUCCESSFUL|FAILED)|SafetyResult|"
                           r"^\s*(TRUE|FALSE|UNKNOWN)\b|\bFALSE\(|Flag data-race|^\s*(FAIL|PASS)\b|"
                           r"definitely UNSAFE|program is SAFE|^\s*(SUCCESSFUL|FAILED)\s*$")


@dataclass
class CoOpeRaceRun:
    """What one CoOpeRace run printed about its one component."""

    component_output: list[str] = field(default_factory=list)
    status: str | None = None
    exit_code: str | None = None
    result: str | None = None
    cpu: float | None = None
    wall: float | None = None
    memory: int | None = None
    termination: str | None = None
    verdict: str | None = None
    ignored: list[str] = field(default_factory=list)
    probe_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def parse_run(text: str) -> CoOpeRaceRun:
    """The CoOpeRaceRun of the output `text` of a CoOpeRace run with one
    component (the protocol of components.py and strategy.py)."""
    run = CoOpeRaceRun(lines=text.splitlines())
    inside = None
    for line in run.lines:
        if inside is not None:
            if line == f"---end of {inside} logs---":
                inside = None
            else:
                run.component_output.append(line)
            continue
        match = re.fullmatch(r"---(.+) logs---", line)
        if match and not line.startswith("---end of "):
            inside = match.group(1)
            continue
        match = re.fullmatch(r"Tool name: (.+?) Status: (.*) Exit code: (.*)", line)
        if match:
            run.status, run.exit_code = match.group(2), match.group(3)
            continue
        match = re.fullmatch(r"Tool name: (.+?) Result: (.*)", line)
        if match:
            run.result = match.group(2)
            continue
        match = re.fullmatch(r"Tool name: (.+?) Measured: cputime=(\S+) walltime=(\S+) memory=(\S+) "
                             r"terminationreason=(\S+)", line)
        if match:
            run.cpu = None if match.group(2) == "-" else float(match.group(2).rstrip("s"))
            run.wall = None if match.group(3) == "-" else float(match.group(3).rstrip("s"))
            run.memory = None if match.group(4) == "-" else int(match.group(4).rstrip("B"))
            run.termination = None if match.group(5) == "none" else match.group(5)
            continue
        if line.startswith("CoOpeRace verdict: "):
            run.verdict = line.removeprefix("CoOpeRace verdict: ")
        elif (re.match(r"CoOpeRace: the task calls .*: .* is ignored", line)
              or "every entry of the strategy is ignored" in line):
            run.ignored.append(line)
        elif re.match(r"CoOpeRace: the version of .* could not be determined", line):
            run.probe_failures.append(line)
        elif line.startswith(("CoOpeRace: error: ", "Traceback ")) or "component-smoke: cannot" in line:
            run.errors.append(line)
        elif re.match(r"(Memory|CPU-time|Task) limit of ", line):
            run.limits.append(line)
    return run


def verdict_like_lines(output: list[str], last: int = 40) -> list[str]:
    """The lines among the last `last` of a component's output that look
    like a verdict, for a status the tool-info module did not read as one."""
    return [line.strip() for line in output[-last:] if VERDICT_WORDS.search(line)][:5]


# ---------------------------------------------------------------------------
# Witness files

def witness_facts(path: Path) -> dict:
    """entry_type and format_version of the YAML witness `path`, read with
    regular expressions (a broken file still gets a line)."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {"entry_type": "?", "format_version": "?"}
    entry = re.search(r"entry_type['\"]?\s*:\s*['\"]?([\w-]+)", text)
    version = re.search(r"format_version['\"]?\s*:\s*['\"]?([\d.]+)", text)
    return {"entry_type": entry.group(1) if entry else "-", "format_version": version.group(1) if version else "-"}


def is_witness_name(name: str) -> bool:
    lower = name.lower()
    return "witness" in lower and lower.endswith(("yml", "yaml", "graphml"))


def written_outside(manifest: list[tuple[str, int]], tool_dir: str) -> list[str]:
    """The paths of `manifest` (files a component run wrote in its
    container) that lie outside its directory `tool_dir`."""
    prefix = tool_dir.rstrip("/") + "/"
    return [path for path, _size in manifest if not path.startswith(prefix)]


def read_manifest(path: Path) -> tuple[str | None, list[tuple[str, int]]]:
    """(working directory, [(path, size)]) of a smoke/written/*.tsv file."""
    cwd, files = None, []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if parts[0] == "#cwd":
            cwd = parts[1]
        elif len(parts) == 2:
            files.append((parts[0], int(parts[1])))
    return cwd, files


# ---------------------------------------------------------------------------
# Evaluation of one task

def raw_status(status: str) -> str:
    """A status of the pre-run table without the validation's wrapping
    ("witness invalid (false(no-data-race))" is "false(no-data-race)")."""
    match = re.fullmatch(r"witness (?:invalid|missing|unconfirmed) \((.*)\)", status or "")
    return match.group(1) if match else (status or "")


def is_answer(status: str | None) -> bool:
    return bool(status) and (status == "true" or status.startswith("false"))


def evaluate(task: dict, run: CoOpeRaceRun, *, same_archive: bool, trusts: str, needs_witness: bool,
             witnesses: list[dict], delivered: bool, lint: dict[str, int | None], outside: list[str],
             tasks_peak: int | None, task_limit: int | None,
             takes_no_witness: bool = False) -> tuple[list[str], list[str]]:
    """(notes, failures) of one task, from its row of the task file `task`
    and the CoOpeRaceRun `run`. `same_archive`: the pre-run row is of the
    archive under test. `witnesses`: the witness files the component wrote
    ({"name", "entry_type", "format_version", "named", "passes"}),
    `delivered` whether CoOpeRace delivered one, `lint` WitnessLint's exit
    code per file name (None: not linted), `outside` the files written
    outside the component's directory, `tasks_peak` the peak number of tasks
    of its cgroup. `takes_no_witness`: the registry names no witness file
    of the component (WitnessSpec files ()), so its witness files are
    ignored by design and reported as a note."""
    notes, failures = [], []
    prerun = raw_status(task.get("prerun_status", ""))
    expected = task.get("expected", "")
    if run.errors or run.verdict is None:
        failures.append("CoOpeRace ended without a verdict line" + (f": {run.errors[0]}" if run.errors else ""))
        return notes, failures
    if run.ignored:
        notes.append("guard: " + "; ".join(line.removeprefix("CoOpeRace: ") for line in run.ignored))
        return notes, failures
    if run.status is None:
        failures.append("no status line of the component")
        return notes, failures
    for line in run.probe_failures:
        failures.append("version probe: " + line.removeprefix("CoOpeRace: "))
    answered = run.result in ("true", "false")
    if is_answer(run.status) and not answered:
        failures.append(f"status {run.status!r} is not accepted as a verdict on {task['property']}")
    if answered:
        if run.result == "true":
            correct = expected == "true"
        else:
            correct = expected.startswith("false") and (
                run.status == expected or (task["property"] != "valid-memsafety" and expected == "false"))
        if not correct:
            failures.append(f"wrong: {run.status} where {expected} is expected")
        if trusts not in ("both", run.result):
            notes.append(f"the default conf does not trust {run.result} of this component here")
    what = "this archive's pre-run" if same_archive else "the bundled archive's pre-run"
    if is_answer(prerun) and not answered:
        line = f"lost: {run.status} where {what} answered {prerun}"
        output = verdict_like_lines(run.component_output)
        if output and not is_answer(run.status):
            line += f"; output has {output[-1]!r}"
        if run.termination is None:
            failures.append(line)
        else:
            notes.append(line + f" (ended by its {run.termination} limit)")
    elif answered and not is_answer(prerun):
        notes.append(f"gained: {run.status} where {what} gave {prerun or 'no row'}")
    elif answered and prerun != run.status:
        failures.append(f"differs from {what}: {run.status} against {prerun}")
    if not is_answer(run.status) and not run.termination:
        output = verdict_like_lines(run.component_output)
        if output and run.status not in ("unknown",):
            notes.append(f"status {run.status!r}, output has {output[-1]!r}")
    # Witnesses
    unnamed = [w["name"] for w in witnesses if not w["named"] and not w["name"].endswith(".graphml")]
    if takes_no_witness:
        if unnamed:
            notes.append("the registry takes no witness from this component; it wrote " + ", ".join(unnamed))
    else:
        if unnamed:
            failures.append("witness file not named by the registry: " + ", ".join(unnamed))
        if answered and needs_witness and not delivered:
            failures.append(f"no witness delivered for {run.result}, which needs one"
                            + (f" ({', '.join(w['name'] for w in witnesses)} written)" if witnesses else ""))
    for name, code in lint.items():
        if code not in (0, None):
            failures.append(f"WitnessLint exit {code} on {name}")
    refused = [w["name"] for w in witnesses if w["passes"] is False]
    if answered and refused:
        notes.append(f"CoOpeRace's witness check refuses {', '.join(refused)} for {run.status}"
                     + ("" if needs_witness else " (the verdict needs none, so it is delivered without)"))
    # Resources
    if outside:
        kept = [path for path in outside if not path.startswith(("/tmp/", "/home/", "/run/"))]
        notes.append(f"{len(outside)} files written outside its directory (discarded with the container)"
                     + (f", {len(kept)} outside /tmp, /home and /run: {', '.join(kept[:3])}" if kept else ""))
    if tasks_peak is not None and task_limit and tasks_peak > task_limit // 2:
        failures.append(f"{tasks_peak} tasks, over half its task limit {task_limit}")
    try:
        prerun_cpu = float(task.get("prerun_cpu") or "nan")
        prerun_mem = float(task.get("prerun_mem") or "nan")
    except ValueError:
        prerun_cpu = prerun_mem = float("nan")
    if run.cpu is not None and prerun_cpu == prerun_cpu and run.cpu > 2 * prerun_cpu + 10 and answered:
        notes.append(f"CPU time {run.cpu:.1f} s against {prerun_cpu:.1f} s in the pre-run")
    if run.memory is not None and prerun_mem == prerun_mem and run.memory > 1.5 * prerun_mem + 200e6:
        notes.append(f"memory {run.memory / 1e6:.0f} MB against {prerun_mem / 1e6:.0f} MB in the pre-run")
    return notes, failures


# ---------------------------------------------------------------------------
# Running

def find_log(out_dir: Path) -> Path | None:
    """The tool output log of BenchExec's one run in `out_dir`."""
    logs = sorted(out_dir.glob("*.logfiles/*.log"))
    return logs[0] if logs else None


def find_files_dir(out_dir: Path) -> Path | None:
    """The result-files directory of BenchExec's one run in `out_dir`."""
    dirs = sorted(path for path in out_dir.glob("*.files/*/*") if path.is_dir())
    return dirs[0] if dirs else None


def cgroup_peaks(path: Path) -> tuple[int | None, int | None]:
    """(peak tasks, peak memory) of the component's cgroup from the sampler
    file `path`: the maximum over the cgroups that hold no other cgroup and
    lie below the run's own (two levels or more below BenchExec's), which
    are those of CoOpeRace's component runs."""
    if not path.is_file():
        return None, None
    pids = memory = None
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or parts[4] != "0" or parts[1].count("/") < 3:
            continue
        try:
            pids = max(pids or 0, int(parts[2]))
            memory = max(memory or 0, int(parts[3]))
        except ValueError:
            continue
    return pids, memory


def build_root(run_dir: Path, tree: Path, archive: Archive, spec, conf: dict, properties: list[str]) -> Path:
    """The CoOpeRace copy of the run: src/, lib/, conf/witnesses.json of
    `tree`, the single-component conf as conf/svcomp27.json, tools-options.json
    and tools.txt with the archive alone, the smoke launcher, and an empty
    tools/<name> on which the archive is mounted."""
    root = run_dir / "root"
    shutil.copytree(tree / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(tree / "lib", root / "lib")
    (root / "conf").mkdir()
    shutil.copy2(tree / "conf" / "witnesses.json", root / "conf" / "witnesses.json")
    smoke_conf = single_component_conf(conf, spec.name, properties)
    (root / "conf" / "svcomp27.json").write_text(json.dumps(smoke_conf, indent=1) + "\n")
    (root / "tools-options.json").write_text(json.dumps(
        {archive.component: {"doi": archive.doi, "version": archive.version, "options": archive.options}},
        indent=2) + "\n")
    (root / "tools.txt").write_text(f"{archive.component}: {archive.doi}\n")
    (root / "VERSION").write_text(f"component smoke of {archive.component} {archive.doi}\n")
    launcher = root / "cooperace"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    (root / "tools" / archive.component).mkdir(parents=True)
    return root


def image_of(wrapper: Path) -> str:
    """The image that the lane's container.sh pins (IMG=...)."""
    match = re.search(r"^IMG=(\S+)", (wrapper / "container.sh").read_text(), re.M)
    if not match:
        raise SmokeError(f"{wrapper}/container.sh pins no image (IMG=)")
    return match.group(1)


def docker_run(args, run_dir: Path, root: Path, archive: Archive, tasks: list[dict]) -> None:
    """Runs the tasks in the competition image, one at a time, with a
    deadline: the inside script, then WitnessLint is run by lint_witnesses."""
    lane = Path(args.lane)
    wrapper = Path(args.lane_wrapper)
    out = run_dir / "out"
    out.mkdir()
    (run_dir / "smoke").mkdir()
    with open(run_dir / "smoke" / "queue.tsv", "w") as queue:
        for task in tasks:
            queue.write(f"{task['id']}\t{task['property']}\t{task['task'].removeprefix('c/')}\n")
    shutil.copy2(INSIDE_SCRIPT, run_dir / "smoke" / "inside.sh")
    name = f"smoke-{archive.component}-{archive.label}-{run_dir.name[-6:]}"
    # The tmpfs holds the copies of CoOpeRace, the archive and BenchExec, and counts against the
    # container's memory, which is the run's 3 GB plus the tmpfs plus 1 GB for BenchExec itself
    size_mb = sum(path.stat().st_size for path in archive.directory.rglob("*")
                  if path.is_file() and not path.is_symlink()) // 10**6
    tmpfs_mb = size_mb + 400
    memory = args.container_memory or f"{4000 + tmpfs_mb}m"
    command = ["docker", "run", "--rm", "--name", name, "--privileged", "--cpuset-cpus", args.cpus,
               "--memory", memory, "--memory-swap", memory,
               "--tmpfs", f"/home/cwd:exec,size={tmpfs_mb}m",
               "-v", f"{lane / 'svb'}:/home/cwd/sv-benchmarks:ro",
               "-v", f"{lane / 'latest' / 'benchexec'}:/in/benchexec-src:ro",
               "-v", f"{root}:/in/root:ro", "-v", f"{archive.directory}:/in/tool:ro",
               "-v", f"{run_dir / 'smoke'}:/smoke:ro", "-v", f"{out}:/out",
               "-v", f"{wrapper / 'benchexec-launcher'}:/usr/bin/benchexec:ro",
               "-e", f"COMPONENT={archive.component}", "-e", f"DOI={archive.doi}",
               "-e", f"ATTRS={args.attrs}", "-e", f"CORES={args.cores}",
               "-e", f"RUN_TIMEOUT={args.run_timeout}", image_of(wrapper), "bash", "/smoke/inside.sh"]
    deadline = args.run_timeout * len(tasks) + 300
    with open(run_dir / "docker.log", "w") as log:
        log.write(" ".join(command) + "\n")
        log.flush()
        try:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=deadline, check=False)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            log.write(f"component-smoke: docker run killed after {deadline} s\n")


def lint_witnesses(args, run_dir: Path, items: list[tuple[str, str, str]]) -> dict[str, int | None]:
    """WitnessLint's exit code on each (witness path, kind, task .c path
    under c/) of `items`, run in the competition image; {} without
    --witnesslint."""
    if not items or not args.witnesslint:
        return {}
    lane = Path(args.lane)
    lint_dir = run_dir / "lint"
    lint_dir.mkdir(exist_ok=True)
    script = lint_dir / "lint.sh"
    lines = ["#!/bin/bash"]
    mounts = []
    for number, (witness, kind, program) in enumerate(items):
        flag = "--expectViolationWitness" if kind == "violation" else "--expectCorrectnessWitness"
        mounts += ["-v", f"{witness}:/w/{number}.yml:ro"]
        lines.append(f"python3 /lint/witnesslinter.py --witness /w/{number}.yml {flag} --ignoreSelfLoops "
                     f"--expectedWitnessVersion 2 /svb/c/{program} > /lintout/{number}.txt 2>&1; "
                     f"echo $? > /lintout/{number}.exit")
    script.write_text("\n".join(lines) + "\n")
    command = ["docker", "run", "--rm", "--memory", "1g", "--cpuset-cpus", args.cpus,
               "-v", f"{args.witnesslint}:/lint:ro", "-v", f"{lane / 'svb'}:/svb:ro",
               "-v", f"{lint_dir}:/lintout", *mounts, image_of(Path(args.lane_wrapper)), "bash", "/lintout/lint.sh"]
    subprocess.run(command, capture_output=True, timeout=60 + 60 * len(items), check=False)
    codes = {}
    for number, (witness, _kind, _program) in enumerate(items):
        exit_file = lint_dir / f"{number}.exit"
        codes[witness] = int(exit_file.read_text().strip()) if exit_file.is_file() else None
    return codes


def task_program(svb: Path, task: str) -> str | None:
    """The program file (under c/) of the task definition `task` (c/....yml)."""
    import yaml  # noqa: PLC0415

    path = svb / task
    try:
        data = yaml.safe_load(path.read_text())
    except OSError:
        return None
    files = data.get("input_files")
    first = files[0] if isinstance(files, list) else files
    return str((path.parent / first).relative_to(svb / "c")) if first else None


def smoke(args, component: str, archive_arg: str | None) -> int:
    """The smoke of one archive of one component; 0 if no failure."""
    tree = Path(args.cooperace).resolve()
    components, config, witnesses = load_cooperace(tree)
    spec = registry_entry(components, component)
    fm_tools_dir = Path(args.fm_tools_dir).resolve() if args.fm_tools_dir else None
    if spec is None:
        print(no_registry_entry(component, fm_tools_dir, tree))
        print(f"FAIL {component}: no registry entry")
        return 1
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    try:
        archive = resolve_archive(component, archive_arg, tree, work, fm_tools_dir)
    except SmokeError as error:
        print(f"FAIL {component}: {error}")
        return 1
    conf_path = Path(args.conf).resolve() if args.conf else tree / "conf" / "svcomp27.json"
    conf = json.loads(conf_path.read_text())
    properties = conf_properties(conf, spec.name)
    tasks = read_tasks(Path(args.tasks), component, args.properties)
    if not properties:
        properties = sorted({task["property"] for task in tasks})
    tasks = [task for task in tasks
             if task["property"] in properties and (not args.roles or task["role"] in args.roles)]
    for number, task in enumerate(tasks, start=1):
        task["id"] = f"{component}-{number:02d}"
    run_dir = work / "runs" / f"{component}-{archive.label}-{stamp()}"
    run_dir.mkdir(parents=True)
    meta = {"component": component, "conf_name": spec.name, "archive": archive.doi, "version": archive.version,
            "options": archive.options, "version_source": archive.version_source, "record": archive.record,
            "directory": str(archive.directory), "cooperace": str(tree), "cooperace_revision": git_revision(tree),
            "conf": str(conf_path), "packages": archive.packages,
            "fm_tools_dir": None if fm_tools_dir is None else str(fm_tools_dir),
            "properties": properties, "attrs": args.attrs, "started": stamp(),
            "static": static_checks(component, archive.directory) + package_check(archive, fm_tools_dir),
            "shipped": shipped_witnesses(archive.directory),
            "tasks": tasks, "task_limit": spec.task_limit,
            "witness_spec": {"directory": spec.witness.directory, "options": list(spec.witness.options),
                             "files": None if spec.witness.files is None else list(spec.witness.files)}}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{component} {archive.doi} ({archive.version}, options {archive.options} from {archive.version_source}): "
          f"{len(tasks)} tasks in {run_dir}", flush=True)
    if tasks and not args.static_only:
        root = build_root(run_dir, tree, archive, spec, conf, properties)
        docker_run(args, run_dir, root, archive, tasks)
    return report(args, run_dir)


def git_revision(tree: Path) -> str:
    done = subprocess.run(["git", "-C", str(tree), "describe", "--always", "--dirty"], capture_output=True, text=True)
    return done.stdout.strip() or "unknown"


def report(args, run_dir: Path) -> int:
    """Evaluates the run in `run_dir`, writes results.tsv and summary.md
    there and prints one line per failure; 0 if there is none."""
    meta = json.loads((run_dir / "meta.json").read_text())
    tree = Path(meta["cooperace"])
    components, config, witnesses = load_cooperace(tree)
    conf = json.loads(Path(meta.get("conf", tree / "conf" / "svcomp27.json")).read_text())
    spec = components.REGISTRY[meta["conf_name"]]
    svb = Path(args.lane) / "svb" if getattr(args, "lane", None) else None
    tool_dir = f"/home/cwd/bin/cooperace/tools/{meta['component']}"
    rows, failures_all = [], []
    if Path(meta["directory"]).is_dir():
        # Checked again, so that a report after a change of the checks uses them
        given = meta.get("fm_tools_dir") or getattr(args, "fm_tools_dir", None)
        fm_tools_dir = Path(given) if given else None
        packages = meta.get("packages")
        if packages is None:
            entry = version_for(fm_tools_data(meta["component"], fm_tools_dir), meta["archive"])
            unlisted = read_unlisted(tree / "tools-unlisted.txt").get(meta["component"])
            if entry is None and unlisted and unlisted[0] == meta["archive"]:
                entry = version_for(fm_tools_data(meta["component"], fm_tools_dir, unlisted[1]), meta["archive"])
            packages = [str(name) for name in (entry or {}).get("required_ubuntu_packages") or []]
        archive = Archive(meta["component"], meta["archive"], Path(meta["directory"]), meta["version"],
                          meta["options"], packages=packages)
        meta["static"] = (static_checks(meta["component"], archive.directory)
                          + package_check(archive, fm_tools_dir))
    for severity, line in meta["static"]:
        if severity == "FAIL":
            failures_all.append(f"FAIL {meta['component']} static: {line}")
    pending_lint, per_task = [], []
    if not (run_dir / "out").is_dir():
        meta["tasks"] = []
    for task in meta["tasks"]:
        out_dir = run_dir / "out" / task["id"]
        log = find_log(out_dir) if out_dir.is_dir() else None
        run = parse_run(log.read_text(errors="replace")) if log else CoOpeRaceRun(errors=["no BenchExec log"])
        files_dir = find_files_dir(out_dir) if out_dir.is_dir() else None
        written = []
        delivered = None
        manifests = []
        if files_dir is not None:
            if (files_dir / "witness.yml").is_file():
                delivered = files_dir / "witness.yml"
            work = files_dir / "smoke" / "work"
            for path in sorted(work.rglob("*")) if work.is_dir() else []:
                if path.is_file() and is_witness_name(path.name):
                    written.append(path)
            written_dir = files_dir / "smoke" / "written"
            manifests = sorted(written_dir.glob("*.tsv")) if written_dir.is_dir() else []
        outside, unnamed_written = [], []
        named = set(spec.witness.files or ())
        for manifest in manifests:
            _cwd, files = read_manifest(manifest)
            outside += written_outside(files, tool_dir)
            for path, _size in files:
                if path.startswith(tool_dir + "/") and is_witness_name(path) and spec.witness.directory == "component":
                    relative = path[len(tool_dir) + 1:]
                    if spec.witness.files is not None and relative not in named:
                        unnamed_written.append(relative)
        verdict = run.result if run.result in ("true", "false") else None
        result_text = components.reported_result(task["property"], run.status) if verdict else ""
        subproperty = witnesses.subproperty_of(result_text) if verdict else None
        infos = []
        for path in written:
            relative = str(path.relative_to(files_dir / "smoke" / "work"))
            base = relative.split("/", 1)[1] if "/" in relative else relative
            # None: no verdict, or no witness counts for it (witnesses.witness_format)
            passes = (witnesses.file_passes(str(path), task["property"], verdict, subproperty)
                      if verdict and witnesses.witness_format(task["property"], verdict, subproperty) else None)
            info = {"name": base, "path": str(path), "named": spec.witness.files is None or base in named,
                    "passes": passes, **witness_facts(path)}
            infos.append(info)
        for relative in unnamed_written:
            infos.append({"name": relative, "path": None, "named": False, "passes": None,
                          "entry_type": "?", "format_version": "?"})
        program = task_program(svb, task["task"]) if svb else None
        if program:
            for info in infos:
                if info["path"] and verdict:
                    pending_lint.append((info["path"], "violation" if verdict == "false" else "correctness", program))
        per_task.append((task, run, infos, delivered, outside, verdict))
    lint = lint_witnesses(args, run_dir, pending_lint) if not getattr(args, "no_lint", False) else {}
    for task, run, infos, delivered, outside, verdict in per_task:
        tasks_peak, _mem_peak = cgroup_peaks(run_dir / "out" / f"{task['id']}.cgroups.tsv")
        trusts = conf_trusts(conf, meta["conf_name"], task["property"])
        needs = witnesses.needs_witness(task["property"], verdict) if verdict else False
        lint_by_name = {info["name"]: lint.get(info["path"]) for info in infos if info["path"]}
        notes, failures = evaluate(task, run, same_archive=task.get("prerun_doi") == meta["archive"], trusts=trusts,
                                   needs_witness=needs, witnesses=infos, delivered=delivered is not None,
                                   lint=lint_by_name, outside=outside, tasks_peak=tasks_peak,
                                   task_limit=meta["task_limit"], takes_no_witness=spec.witness.files == ())
        for failure in failures:
            failures_all.append(f"FAIL {meta['component']} {task['id']} {task['task']}: {failure}")
        witness_text = "; ".join(f"{i['name']} {i['entry_type']} {i['format_version']}" for i in infos) or "-"
        rows.append({
            "id": task["id"], "component": meta["component"], "archive": meta["archive"].removeprefix(DOI_PREFIX),
            "property": task["property"], "role": task["role"], "task": task["task"], "expected": task["expected"],
            "status": run.status or "-", "result": run.result or "-", "verdict": run.verdict or "-",
            "conf_trusts": trusts, "prerun_status": task.get("prerun_status", ""),
            "prerun_of": "this archive" if task.get("prerun_doi") == meta["archive"] else
            task.get("prerun_doi", "").removeprefix(DOI_PREFIX),
            "cpu": "-" if run.cpu is None else f"{run.cpu:.1f}", "prerun_cpu": fmt(task.get("prerun_cpu"), 1),
            "mem_MB": "-" if run.memory is None else f"{run.memory / 1e6:.0f}",
            "prerun_mem_MB": fmt(task.get("prerun_mem"), 1e-6, 0),
            "termination": run.termination or "-", "tasks_peak": "-" if tasks_peak is None else str(tasks_peak),
            "witness_files": witness_text,
            "witness_delivered": "witness.yml" if delivered else "-",
            "witness_check": ",".join("pass" if i["passes"] else ("fail" if i["passes"] is False else "-")
                                      for i in infos) or "-",
            "witnesslint": ",".join("-" if lint.get(i["path"]) is None else str(lint.get(i["path"]))
                                    for i in infos if i["path"]) or "-",
            "written_outside": str(len(outside)), "notes": " | ".join(notes), "failures": " | ".join(failures)})
    with open(run_dir / "results.tsv", "w", newline="") as file:
        writer = csv.DictWriter(file, RESULT_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (run_dir / "summary.md").write_text(summary(meta, rows, failures_all))
    for line in failures_all:
        print(line)
    print(f"{meta['component']} {meta['archive']}: {len(rows)} tasks, {len(failures_all)} failures; "
          f"{run_dir / 'summary.md'}")
    return 1 if failures_all else 0


def fmt(value, scale=1.0, digits=1) -> str:
    try:
        number = float(value) * scale
    except (TypeError, ValueError):
        return "-"
    return "-" if number != number else f"{number:.{digits}f}"


def summary(meta: dict, rows: list[dict], failures: list[str]) -> str:
    """The Markdown summary of one smoke run."""
    lines = [f"# Component smoke: {meta['component']} {meta['archive']}", "",
             f"- Version {meta['version']}, options `{' '.join(meta['options']) or '(none)'}` "
             f"({meta['version_source']}); CoOpeRace {meta['cooperace_revision']} as `{meta['conf_name']}`, "
             f"limits of {Path(meta.get('conf', 'conf/svcomp27.json')).name}",
             f"- Archive: {meta['record'].get('source', '?')}"
             + (f", {meta['record']['size']} bytes, SHA-1 {meta['record']['sha1']}"
                if "sha1" in meta["record"] else ""),
             f"- Limits: {meta['attrs']}; properties {', '.join(meta['properties'])}",
             f"- Registry witness: {meta['witness_spec']}",
             f"- Shipped witness-like files: {', '.join(meta['shipped']) or 'none'}", "",
             "## Static checks", ""]
    lines += [f"- {severity} {line}" for severity, line in meta["static"]]
    lines += ["", "## Tasks", "",
              "| id | property | role | task | expected | status | Result | verdict | pre-run | CPU s (pre) | "
              "MB (pre) | tasks | witness | check | lint | notes / failures |",
              "|" + "---|" * 16]
    for row in rows:
        lines.append(f"| {row['id']} | {row['property']} | {row['role']} | {row['task']} | {row['expected']} | "
                     f"{row['status']} | {row['result']} | {row['verdict']} | {row['prerun_status']} "
                     f"({row['prerun_of']}) | {row['cpu']} ({row['prerun_cpu']}) | {row['mem_MB']} "
                     f"({row['prerun_mem_MB']}) | {row['tasks_peak']} | {row['witness_files']} | "
                     f"{row['witness_check']} | {row['witnesslint']} | "
                     f"{' / '.join(x for x in (row['notes'], row['failures']) if x)} |")
    lines += ["", "## Failures", ""] + ([f"- {line}" for line in failures] or ["none"])
    return "\n".join(lines) + "\n"


def no_registry_entry(component: str, fm_tools_dir: Path | None, tree: Path) -> str:
    """What a registry entry for `component` would need, from its fm-tools entry."""
    data = fm_tools_data(component, fm_tools_dir) or {}
    versions = verifier_versions(data)
    newest = versions[0] if versions else {}
    module = data.get("benchexec_toolinfo_module", "?")
    wheel = sorted((tree / "lib").glob("benchexec-*.whl"))
    in_wheel = "?"
    if wheel and isinstance(module, str) and module.startswith("benchexec.tools."):
        in_wheel = "yes" if module.replace(".", "/") + ".py" in zipfile.ZipFile(wheel[0]).namelist() else "no"
    participations = [p for p in data.get("competition_participations") or [] if "2027" in str(p.get("competition"))]
    return "\n".join([
        f"{component}: no registry entry in {tree / 'src/cooperace/components.py'} (REGISTRY).",
        "An entry would need:",
        f"- ComponentSpec(<conf name>, {module!r}, {component!r}, witness=WitnessSpec(...), task_limit=...): "
        f"the tool-info module (fm-tools benchexec_toolinfo_module; in the bundled BenchExec wheel: {in_wheel}),",
        "  where its witness is written (its own directory or a directory given by an option) and under which "
        "names, which only a run shows,",
        "  its peak number of tasks, and whether its module needs the run's limits (rlimits) for its command line;",
        f"- a line in tools-pool.txt (or tools.txt): {component}: {newest.get('doi', '?')} "
        f"(fm-tools version {newest.get('version', '?')}),",
        f"  and its options in tools-options.json: {newest.get('benchexec_toolinfo_options', [])};",
        f"- its required_ubuntu_packages in CoOpeRace's fm-tools entry: {newest.get('required_ubuntu_packages', [])};",
        f"- 2027 participations: {[(p.get('track'), p.get('tool_version')) for p in participations]}.",
    ])


def run_all(args) -> int:
    """The smoke of every component of the lock files `args.tools_list`."""
    tree = Path(args.cooperace).resolve()
    names = []
    for path in args.tools_list or [str(tree / "tools.txt")]:
        names += [name for name in read_lock(Path(path)) if name not in names]
    status = 0
    for name in names:
        print(f"== {name}", flush=True)
        try:
            status |= smoke(args, name, args.archive)
        except Exception as error:  # noqa: BLE001 - one component's defect does not stop the others
            print(f"FAIL {name}: {type(error).__name__}: {error}")
            status = 1
    return status


# ---------------------------------------------------------------------------
# Task selection from the pre-run table

def select(args) -> int:
    """Writes the task file: for each component and property, the fastest
    task the component's newest pre-run answered `true` correctly, the
    fastest it answered `false` correctly, the fastest non-answer that ends
    within a few seconds or at the conf's CPU-time limit, and the fastest
    pair of ilp32/lp64 twins it answered, of the main Concurrency category,
    leaving out tasks whose program calls a function of the component's
    `ignoreIfCalls` in that property."""
    import pandas as pd  # noqa: PLC0415

    tree = Path(args.cooperace).resolve()
    _components, config, _witnesses = load_cooperace(tree)
    conf = json.loads(Path(args.conf or tree / "conf" / "svcomp27.json").read_text())
    svb = Path(args.svb)
    names = {line.split("=")[0]: line.split("=")[1] for line in args.component}
    columns = ["tool", "version", "task", "property", "expected_verdict", "status", "cputime_s", "memory_B",
               "subcategory", "run_date"]
    table = pd.read_parquet(args.table, columns=columns, filters=[("tool", "in", list(names))])
    table = table[table.subcategory.str.endswith(".Concurrency")]
    out = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    print("# Written by scripts/component-smoke.py select from the pre-run table " + Path(args.table).name +
          "; one row per task; prerun_* are of the archive prerun_doi")
    out.writerow(TASK_COLUMNS)
    for tool, doi in names.items():
        spec = registry_entry(_components, tool)
        conf_name = spec.name if spec is not None else tool
        rows = table[table.tool == tool].copy()
        rows["raw"] = rows.status.map(raw_status)
        props = conf_properties(conf, conf_name) or list(PROPERTIES)
        for prop in props:
            ignore = conf.get("properties", {}).get(prop, {}).get("ignoreIfCalls", {})
            guards = {name for entry, names_ in ignore.items()
                      if entry.split(":")[0] == conf_name for name in names_}
            cap = conf.get("properties", {}).get(prop, {}).get("cpuTimeLimits", {}).get(conf_name)
            mine = rows[rows.property == prop]
            if guards:
                def calls(task, guards=guards):
                    program = task_program(svb, task)
                    try:
                        text = (svb / "c" / program).read_text(errors="replace") if program else ""
                    except OSError:
                        text = ""
                    return bool(config.called_functions(text, guards))
                mine = mine[~mine.task.map(calls)]
            mine = mine[mine.task.map(lambda task: (svb / task).is_file())]
            chosen = []
            right = ((mine.raw == mine.expected_verdict)
                     | ((mine.expected_verdict == "false") & mine.raw.str.startswith("false")))
            good = mine[right & (mine.cputime_s >= 0.3)].sort_values("cputime_s")
            plain = good[~good.task.str.contains("twins")]
            for role, pick in (("true", plain[plain.raw == "true"]),
                               ("false", plain[plain.raw.str.startswith("false")])):
                if len(pick):
                    chosen.append((role, pick.iloc[0]))
            other = mine[~mine.raw.map(is_answer)].sort_values("cputime_s")
            quick = other[other.cputime_s < 30]
            capped = other[other.raw.isin(["TIMEOUT"])] if cap and cap <= 60 else other.iloc[0:0]
            pick = quick if len(quick) else capped
            if len(pick):
                chosen.append(("non-answer", pick.iloc[0]))
            # A pair of twins: a task of pthread-ilp32-twins (lp64-twins) and the task of the same name
            # without the suffix -ilp32 (-lp64) elsewhere, which is its LP64 (ILP32) twin; both answered
            # correctly if there is such a pair, else both within 30 s
            answered_twins = set(good.task)
            by_name = {}
            for task in mine.task:
                if "-twins/" not in task:
                    by_name.setdefault(task.rsplit("/", 1)[1], []).append(task)
            pairs = []
            for _, row in mine[mine.task.str.contains("-twins/")].iterrows():
                model = "ilp32" if "ilp32-twins/" in row.task else "lp64"
                name = row.task.rsplit("/", 1)[1].replace(f"-{model}.yml", ".yml")
                for partner in by_name.get(name, []):
                    other_row = mine[mine.task == partner].iloc[0]
                    both = row.task in answered_twins and partner in answered_twins
                    if both or max(row.cputime_s, other_row.cputime_s) < 30:
                        twin, other = (row, other_row) if model == "ilp32" else (other_row, row)
                        pairs.append((not both, row.cputime_s + other_row.cputime_s, twin, other))
            if pairs:
                _, _, ilp, lp = min(pairs, key=lambda pair: pair[:2])
                chosen += [("ilp32", ilp), ("lp64", lp)]
            for role, row in chosen:
                out.writerow([tool, prop, role, row.task, row.expected_verdict, doi, row.status,
                              f"{row.cputime_s:.2f}", f"{row.memory_B:.0f}"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--cooperace", default=str(ROOT), help="the CoOpeRace tree whose registry and conf are used")
        p.add_argument("--conf", help="the conf whose limits and guards the component gets "
                                      "(default: conf/svcomp27.json of --cooperace)")
        p.add_argument("--work", default=os.environ.get("COMPONENT_SMOKE_WORK"),
                       help="directory for archives and runs (env COMPONENT_SMOKE_WORK)")
        p.add_argument("--lane", default=os.environ.get("COOPERACE_LANE"),
                       help="directory with svb/ (sv-benchmarks) and latest/benchexec (env COOPERACE_LANE)")
        p.add_argument("--lane-wrapper", default=os.environ.get("COOPERACE_LANE_WRAPPER"),
                       help="directory with the lane's container.sh (image pin) and benchexec-launcher")
        p.add_argument("--witnesslint", default=os.environ.get("WITNESSLINT_DIR"),
                       help="unpacked WitnessLint (witnesslinter.py); without it no lint")
        p.add_argument("--fm-tools-dir", help="fm-tools checkout (default: fm-tools main on gitlab.com)")
        p.add_argument("--tasks", default=str(TASKS_FILE))
        p.add_argument("--properties", nargs="*")
        p.add_argument("--roles", nargs="*")
        p.add_argument("--cpus", default="15", help="docker --cpuset-cpus")
        p.add_argument("--cores", default=None, help="BenchExec --allowedCores (default: --cpus)")
        p.add_argument("--attrs", default=PRERUN_ATTRS)
        p.add_argument("--container-memory", help="docker --memory (default: 4 GB plus the tmpfs)")
        p.add_argument("--run-timeout", type=int, default=1150, help="seconds per BenchExec call")
        p.add_argument("--static-only", action="store_true", help="fetch and check the archive, run no task")
        p.add_argument("--no-lint", action="store_true")

    one = sub.add_parser("run")
    one.add_argument("component")
    one.add_argument("--archive", help="Zenodo record id or DOI, zip, unpacked directory, 'bundled' (default) "
                                       "or 'registered' (fm-tools' newest)")
    common(one)
    every = sub.add_parser("all")
    every.add_argument("--tools-list", nargs="*", help="lock files (default: tools.txt)")
    every.add_argument("--archive", help="'bundled' (default) or 'registered' for every component")
    common(every)
    rep = sub.add_parser("report")
    rep.add_argument("run_dir")
    common(rep)
    sel = sub.add_parser("select")
    sel.add_argument("--table", required=True)
    sel.add_argument("--svb", required=True)
    sel.add_argument("--component", nargs="+", required=True, help="NAME=DOI of the pre-run rows")
    sel.add_argument("--cooperace", default=str(ROOT))
    sel.add_argument("--conf")
    args = parser.parse_args()
    if args.command == "select":
        return select(args)
    args.cores = args.cores or args.cpus
    if args.command == "report":
        return report(args, Path(args.run_dir))
    if not args.work or (not args.static_only and not (args.lane and args.lane_wrapper)):
        parser.error("--work, and for runs --lane and --lane-wrapper, are needed (or their environment variables)")
    if args.command == "run":
        return smoke(args, args.component, args.archive)
    return run_all(args)


if __name__ == "__main__":
    sys.exit(main())
