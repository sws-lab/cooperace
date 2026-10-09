#!/usr/bin/env python3
"""Installs the components that the lock files name into tools/.

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
itself.

Each installed component records its DOI in tools/<name>/.doi, written after
the installation has finished. A component is installed again when its
directory is missing, has no record, or has a record other than the DOI in
its lock file. A directory without a record is replaced even if it was in
fact installed from the same DOI: its origin cannot be told, and an
interrupted installation leaves such a directory. To keep a directory whose origin you know, write the
record yourself: `echo 10.5281/zenodo.NNN > tools/<name>/.doi`.

Without a network connection `--check` compares tools/ with tools.txt (and
with tools-pool.txt, with `--pool`) and exits 1 if any component would be
installed. `--dry-run` also looks up the
DOIs in fm-tools, and fails on one that fm-tools does not list, but installs
nothing.

The script can be run from any directory; the lock files and tools/ are in
the repository that contains it.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

FM_TOOLS_DATA_URL = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"
ROOT = Path(__file__).resolve().parent.parent
DOI_FILE = ROOT / "tools.txt"
POOL_FILE = ROOT / "tools-pool.txt"
TOOLS_ROOT = ROOT / "tools"
RECORD_NAME = ".doi"


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


def version_id_for(data: dict, reference: str) -> str | None:
    """The name of the first version of an fm-tools entry whose DOI (or URL)
    is `reference`, or None if it lists none. Versions that share a DOI share
    an archive."""
    for entry in version_entries(data):
        if str(entry.get("doi") or entry.get("url")) == reference:
            return str(entry["version"])
    return None


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
        description="Install the components named in tools.txt (and tools-pool.txt) into tools/.")
    parser.add_argument("--check", action="store_true",
                        help="only compare tools/ with the lock files, offline; exit 1 if a component would be installed")
    parser.add_argument("--dry-run", action="store_true",
                        help="look the DOIs up in fm-tools and print what would be installed, but install nothing")
    parser.add_argument("--pool", action="store_true",
                        help="also install (or, with --check, also check) the components of tools-pool.txt, "
                             "which are not in the SV-COMP archive")
    parser.add_argument("--fm-tools-dir", type=Path,
                        help="read the fm-tools entries from this checkout of the fm-tools repository, "
                             "not from gitlab.com")
    args = parser.parse_args()

    try:
        archive, pool = read_lock_files(DOI_FILE, POOL_FILE)
    except ValueError as error:
        sys.exit(f"Cannot read the lock files: {error}")
    wanted = {**archive, **pool} if args.pool else archive
    stale = stale_tools(wanted, TOOLS_ROOT)
    for name, doi in wanted.items():
        recorded = stale.get(name, doi)
        print(f"{name}: {doi}: " + ("up to date" if name not in stale
                                   else f"to install (recorded: {recorded or 'none'})"))
    if not stale:
        print(f"All components in {TOOLS_ROOT} match the lock files.")
        return
    if args.check:
        sys.exit(f"{len(stale)} component(s) in {TOOLS_ROOT} do not match the lock files: {', '.join(stale)}")

    # Look every DOI up before installing any, so that a wrong line in
    # tools.txt stops the script before it changes tools/.
    entries: dict[str, tuple[dict, str]] = {}
    errors: list[str] = []
    for name in stale:
        try:
            data = load_fm_tool_data(name, args.fm_tools_dir)
        except Exception as error:
            errors.append(f"{name}: cannot read the fm-tools entry: {error}")
            continue
        version_id = version_id_for(data, wanted[name])
        if version_id is None:
            errors.append(f"{name}: fm-tools does not list {wanted[name]}; it lists "
                          + (", ".join(listed_references(data)) or "no versions"))
        else:
            entries[name] = (data, version_id)
            print(f"{name}: {wanted[name]} is version {version_id} in fm-tools")
    if errors:
        sys.exit("\n".join(errors))
    if args.dry_run:
        return

    for name, (data, version_id) in entries.items():
        print(f"Installing {name} {wanted[name]} ({version_id})")
        install(name, data, version_id, wanted[name], TOOLS_ROOT)


if __name__ == "__main__":
    main()
