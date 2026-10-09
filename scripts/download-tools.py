#!/usr/bin/env python3
"""Installs the components that the lock files name into tools/, and writes
the options each is run with into tools-options.json.

A lock file has one "<name>: <doi>" line per component, where <name> is the
fm-tools entry of the component (data/<name>.yml of the fm-tools repository)
and <doi> is the DOI of one of the versions that entry lists. There are two,
both tracked:

- tools.txt names the components of the SV-COMP archive, the ones the
  strategy conf/svcomp26.json runs; scripts/svcomp-dist.sh packs exactly
  these. They are always installed.
- tools-pool.txt names the further components that CoOpeRace's registry can
  run (src/cooperace/components.py, REGISTRY) and that are not in the
  archive. They are installed only with `--pool`.

The script installs exactly those archives, and never chooses a version
itself. Of the versions an fm-tools entry lists with a DOI, the one taken is
the first whose options hold no `${witness}`: a version with that placeholder
is a witness validator, which shares its archive with the verifier.

Each installed component records its DOI in tools/<name>/.doi, written after
the installation has finished. A component is installed again when its
directory is missing, has no record, or has a record other than the DOI in
its lock file. A directory without a record is replaced even if it was in
fact installed from the same DOI: its origin cannot be told, and an
interrupted installation leaves such a directory. To keep a directory whose
origin you know, write the record yourself:
`echo 10.5281/zenodo.NNN > tools/<name>/.doi`.

tools-options.json, tracked beside the lock files, holds for every component
of both lock files, by its name, an object with its "doi", the fm-tools
"version" whose options were taken and the "options" themselves, the
`benchexec_toolinfo_options` of that version. CoOpeRace passes these options
to the component's tool-info module and refuses to run a component whose
tools/<name>/.doi is not the DOI recorded there. fm-tools gives one list of
options per version, the same for every property; options per property would
be a further key of a component's object. Every run of the script that reads
fm-tools brings the file up to date for the components whose DOI it does not
match, before installing anything; `--options-only` does only that, so that
the file can be written from a checkout of fm-tools (`--fm-tools-dir`)
without downloading a component, and rewrites the object of every
component, also of one whose DOI matches.

Without a network connection `--check` compares tools/ with tools.txt (and
with tools-pool.txt, with `--pool`) and tools-options.json with both lock
files, and exits 1 if a component would be installed or the options file
would change; given `--fm-tools-dir`, it also compares the options with the
fm-tools entries. `--dry-run` also looks up the DOIs in fm-tools, and fails on
one that fm-tools does not list, but changes nothing.

The script can be run from any directory; the lock files, tools-options.json
and tools/ are in the repository that contains it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

FM_TOOLS_DATA_URL = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"
ROOT = Path(__file__).resolve().parent.parent
DOI_FILE = ROOT / "tools.txt"
POOL_FILE = ROOT / "tools-pool.txt"
OPTIONS_FILE = ROOT / "tools-options.json"
TOOLS_ROOT = ROOT / "tools"
RECORD_NAME = ".doi"
# In the options of a validator's version, the place of the witness file.
WITNESS_PLACEHOLDER = "${witness}"


def parse_tools_file(text: str) -> dict[str, str]:
    """The components of the text of a lock file, as {name: doi}, in file
    order. Blank lines and lines starting with '#' are skipped. A line without
    a name and a DOI, or a name given twice, is a ValueError."""
    wanted: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, doi = (part.strip() for part in line.partition(":"))
        if not separator or not name or not doi:
            raise ValueError(f"line {number}: expected '<name>: <doi>', got '{raw}'")
        if name in wanted:
            raise ValueError(f"line {number}: {name} is listed twice")
        wanted[name] = doi
    return wanted


def read_lock_files(archive_file: Path, pool_file: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The components of the lock files `archive_file` (tools.txt) and
    `pool_file` (tools-pool.txt), each as {name: doi}. A missing pool file
    names no component. Raises ValueError, naming the file, for a file that
    cannot be read or parsed and for a component that both files name."""
    locks = []
    for path, required in ((archive_file, True), (pool_file, False)):
        try:
            locks.append(parse_tools_file(path.read_text()))
        except FileNotFoundError as error:
            if required:
                raise ValueError(f"{path}: {error}") from error
            locks.append({})
        except (OSError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from error
    archive, pool = locks
    both = sorted(set(archive) & set(pool))
    if both:
        raise ValueError(f"{pool_file} names {', '.join(both)}, which {archive_file.name} names too")
    return archive, pool


def installed_doi(tool_dir: Path) -> str | None:
    """The DOI recorded in `tool_dir`, or None if the directory or its record
    is missing."""
    try:
        return (tool_dir / RECORD_NAME).read_text().strip() or None
    except OSError:
        return None


def stale_tools(wanted: dict[str, str], tools_root: Path) -> dict[str, str | None]:
    """The components of `wanted` that are to be installed, as {name: DOI
    recorded now, or None}: those whose directory under `tools_root` has no
    record or a record other than the wanted DOI."""
    recorded = {name: installed_doi(tools_root / name) for name in wanted}
    return {name: recorded[name] for name, doi in wanted.items() if recorded[name] != doi}


def version_entries(data: dict) -> list[dict]:
    """The entries of the list `versions` in the data of an fm-tools entry."""
    return [entry for entry in data.get("versions") or [] if isinstance(entry, dict)]


def listed_references(data: dict) -> list[str]:
    """The DOIs (or, for an entry given by URL, the URLs) of the versions of
    an fm-tools entry."""
    return [str(entry.get("doi") or entry.get("url")) for entry in version_entries(data)
            if entry.get("doi") or entry.get("url")]


def entry_options(entry: dict) -> list[str]:
    """The `benchexec_toolinfo_options` of the version `entry` of an fm-tools
    entry, as strings; empty if it has none."""
    return [str(option) for option in entry.get("benchexec_toolinfo_options") or []]


def version_entry_for(data: dict, reference: str) -> dict | None:
    """The first version of an fm-tools entry whose DOI (or URL) is
    `reference` and whose options hold no WITNESS_PLACEHOLDER, or None if it
    lists none. Versions that share a DOI share an archive; one with the
    placeholder runs the archive as a witness validator."""
    for entry in version_entries(data):
        if (str(entry.get("doi") or entry.get("url")) == reference
                and not any(WITNESS_PLACEHOLDER in option for option in entry_options(entry))):
            return entry
    return None


def version_id_for(data: dict, reference: str) -> str | None:
    """The name of the version version_entry_for chooses, or None."""
    entry = version_entry_for(data, reference)
    return None if entry is None else str(entry["version"])


def options_record(doi: str, entry: dict) -> dict:
    """The object tools-options.json holds for a component installed from
    `doi`, the version `entry` of its fm-tools entry."""
    return {"doi": doi, "version": str(entry["version"]), "options": entry_options(entry)}


def read_options_file(path: Path) -> dict[str, dict]:
    """The components of the options file `path`, {name: object}; empty if
    the file does not exist. Raises ValueError for a file that is not a JSON
    object of objects."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(data, dict) or not all(isinstance(value, dict) for value in data.values()):
        raise ValueError(f"{path}: expected a JSON object of one object per component")
    return data


def stale_options(wanted: dict[str, str], recorded: dict[str, dict]) -> list[str]:
    """The components of `wanted` ({name: doi}, both lock files) whose object
    in the options file `recorded` is missing or has another DOI."""
    return [name for name, doi in wanted.items() if (recorded.get(name) or {}).get("doi") != doi]


def options_text(wanted: dict[str, str], records: dict[str, dict]) -> str:
    """The text of tools-options.json for the components `wanted`, in their
    order, from their objects in `records`. A component of `records` that
    `wanted` does not name is left out."""
    return json.dumps({name: records[name] for name in wanted}, indent=2) + "\n"


def load_fm_tool_data(name: str, fm_tools_dir: Path | None) -> dict:
    """The data of the fm-tools entry `name`: data/<name>.yml of the checkout
    `fm_tools_dir` if given, else of the main branch of the fm-tools
    repository on gitlab.com."""
    import yaml

    if fm_tools_dir is not None:
        text = (fm_tools_dir / "data" / f"{name}.yml").read_text()
    else:
        request = urllib.request.Request(f"{FM_TOOLS_DATA_URL}{name}.yml", headers={"Accept": "application/x-yaml"})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
    return yaml.safe_load(text)


def look_up(names: list[str], wanted: dict[str, str], fm_tools_dir: Path | None) -> dict[str, tuple[dict, dict]]:
    """The fm-tools data and the chosen version entry of each component of
    `names`, {name: (data, entry)}, for its DOI in `wanted`. Every component is
    looked up before the script changes anything, so that a wrong line in a
    lock file stops it first: a component whose entry cannot be read or does
    not list its DOI ends the script with one message per such component."""
    found: dict[str, tuple[dict, dict]] = {}
    errors: list[str] = []
    for name in names:
        try:
            data = load_fm_tool_data(name, fm_tools_dir)
        except Exception as error:
            errors.append(f"{name}: cannot read the fm-tools entry: {error}")
            continue
        entry = version_entry_for(data, wanted[name])
        if entry is None:
            errors.append(f"{name}: fm-tools lists {wanted[name]} for no verifier version; it lists "
                          + (", ".join(listed_references(data)) or "no versions"))
        else:
            found[name] = (data, entry)
            print(f"{name}: {wanted[name]} is version {entry['version']} in fm-tools")
    if errors:
        sys.exit("\n".join(errors))
    return found


def install(name: str, data: dict, version_id: str, doi: str, tools_root: Path) -> None:
    """Downloads the archive of the version `version_id` of the fm-tools entry
    `data` into tools_root/<name>, replacing what is there, and then records
    `doi` in it."""
    from fm_tools.download import DownloadDelegate
    from fm_tools.fmtool import FmTool
    from fm_tools.fmtoolversion import FmToolVersion

    tools_root.mkdir(exist_ok=True)
    version = FmToolVersion(FmTool(data), version_id)
    version.download_and_install_into(tools_root / name, delegate=DownloadDelegate(), show_loading_bar=True)
    (tools_root / name / RECORD_NAME).write_text(doi + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install the components named in tools.txt (and tools-pool.txt) into tools/, "
                    "and write their options into tools-options.json.")
    parser.add_argument("--check", action="store_true",
                        help="only compare tools/ and tools-options.json with the lock files, offline "
                             "(with --fm-tools-dir also the options with fm-tools); exit 1 on a difference")
    parser.add_argument("--dry-run", action="store_true",
                        help="look the DOIs up in fm-tools and print what would change, but change nothing")
    parser.add_argument("--options-only", action="store_true",
                        help="write tools-options.json from fm-tools, and install nothing")
    parser.add_argument("--pool", action="store_true",
                        help="also install (or, with --check, also check) the components of tools-pool.txt, "
                             "which are not in the SV-COMP archive")
    parser.add_argument("--fm-tools-dir", type=Path,
                        help="read the fm-tools entries from this checkout of the fm-tools repository, "
                             "not from gitlab.com")
    args = parser.parse_args()

    try:
        archive, pool = read_lock_files(DOI_FILE, POOL_FILE)
        recorded = read_options_file(OPTIONS_FILE)
    except ValueError as error:
        sys.exit(f"Cannot read the lock files: {error}")
    every = {**archive, **pool}
    to_install = every if args.pool else archive

    stale = {} if args.options_only else stale_tools(to_install, TOOLS_ROOT)
    # --options-only rewrites every object, so that options changed in
    # fm-tools for the same DOI are taken too
    outdated = list(every) if args.options_only else stale_options(every, recorded)
    unlisted = [name for name in recorded if name not in every]
    if not args.options_only:
        for name, doi in to_install.items():
            print(f"{name}: {doi}: " + ("up to date" if name not in stale
                                       else f"to install (recorded: {stale[name] or 'none'})"))
    for name in outdated:
        print(f"{name}: {every[name]}: its options in {OPTIONS_FILE.name} are to be written")
    for name in unlisted:
        print(f"{name}: in {OPTIONS_FILE.name} but in no lock file, to be removed from it")

    if args.check:
        problems = []
        if stale:
            problems.append(f"{len(stale)} component(s) in {TOOLS_ROOT} do not match the lock files: "
                            + ", ".join(stale))
        if outdated or unlisted:
            problems.append(f"{OPTIONS_FILE.name} does not match the lock files: " + ", ".join(outdated + unlisted))
        elif args.fm_tools_dir is not None:
            found = look_up(list(every), every, args.fm_tools_dir)
            expected = options_text(every, {name: options_record(every[name], entry)
                                            for name, (_data, entry) in found.items()})
            if OPTIONS_FILE.read_text() != expected:
                problems.append(f"the options in {OPTIONS_FILE.name} differ from those of the fm-tools entries in "
                                f"{args.fm_tools_dir}; --options-only --fm-tools-dir {args.fm_tools_dir} rewrites them")
        if problems:
            sys.exit("\n".join(problems))
        print(f"All components in {TOOLS_ROOT} and {OPTIONS_FILE.name} match the lock files.")
        return

    if not stale and not outdated and not unlisted:
        print(f"All components in {TOOLS_ROOT} and {OPTIONS_FILE.name} match the lock files.")
        return

    entries = look_up([name for name in every if name in stale or name in outdated], every, args.fm_tools_dir)
    if args.dry_run:
        return

    if outdated or unlisted:
        records = {name: recorded[name] for name in every if name in recorded}
        records.update({name: options_record(every[name], entries[name][1]) for name in outdated})
        OPTIONS_FILE.write_text(options_text(every, records))
        print(f"Wrote {OPTIONS_FILE}")

    for name in stale:
        data, entry = entries[name]
        print(f"Installing {name} {to_install[name]} ({entry['version']})")
        install(name, data, str(entry["version"]), to_install[name], TOOLS_ROOT)

if __name__ == "__main__":
    main()
