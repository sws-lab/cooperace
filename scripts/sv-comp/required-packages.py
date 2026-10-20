#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Computes the required_ubuntu_packages of CoOpeRace's fm-tools entry from
those its components declare.

    scripts/sv-comp/required-packages.py [--fm-tools-dir DIR] [--yaml | --why]
    scripts/sv-comp/required-packages.py [--fm-tools-dir DIR] --check [ENTRY] [--year 2027]

For every component of tools.txt, the components of the SV-COMP archive (not
those of tools-pool.txt, which are not packed), the script reads its fm-tools
entry, data/<name>.yml, from the checkout DIR or, without `--fm-tools-dir`,
from fm-tools' main branch on gitlab.com, and takes the version whose `doi` is
the DOI that tools.txt names. Of versions sharing a DOI it takes the verifier,
not the witness validator, as scripts/download-tools.py does (and with that
script's functions). The version's `required_ubuntu_packages` (none if absent)
and the packages of scripts/sv-comp/own-packages.txt, which the archive's own
code needs, make the list. It is printed without duplicates and sorted as
`yq unique` sorts it, the order scripts/sv-comp/fmtools-entry.py writes, one
package per line; `--yaml` prints it as the `required_ubuntu_packages:` block
of a version in data/cooperace.yml, and `--why` prints after each package the
components (and "own", for own-packages.txt) that declare it.

The packages are installed into docker.io/ubuntu:26.04 for the archive, and
the components run inside it. A component version whose
`base_container_images` is anything other than that one image, or that has
`full_container_images`, may need more than its packages in that image. Its
packages are part of the list, and the script prints a warning on stderr
naming the component and its images; the warning does not change the exit
status.

`--check` compares the list with the `required_ubuntu_packages` of the version
of ENTRY that the SV-COMP YEAR participation names, the version that
fmtools-entry.py's `checked_version` selects. ENTRY is a path or URL of
data/cooperace.yml, by default fmtools-entry.py's. The script prints the
packages missing from the entry and those the entry lists in addition and
exits 1 if either is not empty, and exits 0 and says so if they are equal.

Exits 1 with a message on stderr if tools.txt or own-packages.txt cannot be
read, if ENTRY cannot be read or checked_version refuses it, and, naming the
component, if a component's entry cannot be read or lists no verifier version
with the DOI of tools.txt.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_FILE = ROOT / "tools.txt"
OWN_FILE = Path(__file__).resolve().parent / "own-packages.txt"
OWN = "own"
# The image fm-tools' CI installs the archive's packages into.
BASE_IMAGE = "docker.io/ubuntu:26.04"


def load_script(name: str, path: Path):
    """The module of the script at `path`, imported under `name`; the scripts'
    file names hold hyphens, so they are imported by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


download_tools = load_script("download_tools", ROOT / "scripts" / "download-tools.py")
fmtools_entry = load_script("fmtools_entry", ROOT / "scripts" / "sv-comp" / "fmtools-entry.py")


def parse_own_packages(text: str) -> list[str]:
    """The packages of the text of own-packages.txt: the first word of each
    line, after removing everything from '#' on; blank lines are skipped."""
    return [line.split("#", 1)[0].split()[0] for line in text.splitlines() if line.split("#", 1)[0].strip()]


def component_versions(wanted: dict[str, str], fm_tools_dir: Path | None) -> tuple[dict[str, dict], list[str]]:
    """The version of the fm-tools entry of every component of `wanted`
    ({name: doi}, as tools.txt gives it) that has its DOI, chosen by
    download_tools.version_entry_for, as {name: version}, and a message per
    component whose entry cannot be read or lists no such version."""
    versions: dict[str, dict] = {}
    errors: list[str] = []
    for name, doi in wanted.items():
        try:
            data = download_tools.load_fm_tool_data(name, fm_tools_dir)
        except Exception as error:
            errors.append(f"{name}: cannot read the fm-tools entry: {error}")
            continue
        version = download_tools.version_entry_for(data, doi)
        if version is None:
            listed = ", ".join(download_tools.listed_references(data)) or "no versions"
            errors.append(f"{name}: fm-tools lists {doi} for no verifier version; it lists {listed}")
        else:
            versions[name] = version
    return versions, errors


def image_warning(name: str, version: dict) -> str | None:
    """A warning for the component `name` if its fm-tools `version` has
    `full_container_images` or `base_container_images` other than exactly
    BASE_IMAGE, else None."""
    full = version.get("full_container_images") or []
    base = version.get("base_container_images") or []
    if full:
        return (
            f"{name} {version['version']}: has full_container_images {', '.join(full)}; "
            f"its required_ubuntu_packages may not be all it needs in {BASE_IMAGE}"
        )
    if base != [BASE_IMAGE]:
        named = ", ".join(base) if base else "none"
        return (
            f"{name} {version['version']}: its base_container_images are {named}, not {BASE_IMAGE}; "
            f"its required_ubuntu_packages may not be all it needs in {BASE_IMAGE}"
        )
    return None


def declared_by(versions: dict[str, dict], own: list[str]) -> dict[str, list[str]]:
    """{package: the components that declare it, then OWN if `own` lists it},
    sorted by package as fmtools-entry.py sorts, for the component `versions`
    ({name: fm-tools version}) and the own packages `own`."""
    sources: dict[str, list[str]] = {}
    for name, version in versions.items():
        for package in version.get("required_ubuntu_packages") or []:
            sources.setdefault(str(package), []).append(name)
    for package in own:
        sources.setdefault(package, []).append(OWN)
    return {package: sources[package] for package in sorted(sources)}


def yaml_block(packages: list[str]) -> str:
    """`packages` as the `required_ubuntu_packages:` block of a version in
    data/cooperace.yml, indented as there."""
    return "    required_ubuntu_packages:\n" + "".join(f"      - {p}\n" for p in packages)


def compare(computed: list[str], listed: list[str]) -> tuple[list[str], list[str]]:
    """The packages of `computed` that `listed` lacks, and those `listed` has
    in addition, each sorted."""
    return sorted(set(computed) - set(listed)), sorted(set(listed) - set(computed))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fm-tools-dir",
        type=Path,
        help="read the components' entries from this checkout of fm-tools, not from gitlab.com",
    )
    parser.add_argument("--tools", type=Path, default=TOOLS_FILE, help="the lock file (default: %(default)s)")
    parser.add_argument("--own", type=Path, default=OWN_FILE, help="the archive's own packages (default: %(default)s)")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--yaml", action="store_true", help="print the required_ubuntu_packages block")
    output.add_argument("--why", action="store_true", help="print who declares each package")
    output.add_argument(
        "--check",
        nargs="?",
        const=fmtools_entry.DEFAULT_ENTRY,
        metavar="ENTRY",
        help="compare with the packages of the entry's participation version (path or URL; default: %(const)s)",
    )
    parser.add_argument("--year", type=int, default=2027, help="competition year for --check (default: %(default)s)")
    args = parser.parse_args()

    try:
        wanted = download_tools.parse_tools_file(args.tools.read_text())
        own = parse_own_packages(args.own.read_text())
    except (OSError, ValueError) as error:
        print(f"required-packages: error: {error}", file=sys.stderr)
        return 1
    versions, errors = component_versions(wanted, args.fm_tools_dir)
    if errors:
        print("\n".join(f"required-packages: error: {e}" for e in errors), file=sys.stderr)
        return 1
    warnings = [w for w in (image_warning(name, v) for name, v in versions.items()) if w]
    sources = declared_by(versions, own)
    packages = list(sources)

    status = 0
    if args.check is not None:
        import yaml

        try:
            version = fmtools_entry.checked_version(fmtools_entry.load_entry(args.check), args.year)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
            print(f"required-packages: error: {args.check}: {error}", file=sys.stderr)
            return 1
        missing, extra = compare(packages, [str(p) for p in version.get("required_ubuntu_packages") or []])
        where = f"version {version['version']} of {args.check}"
        if missing or extra:
            print(f"The required_ubuntu_packages of {where} differ from those computed from {args.tools.name}:")
            for package in missing:
                print(f"  missing: {package} (declared by {', '.join(sources[package])})")
            for package in extra:
                print(f"  extra:   {package} (declared by no component and not in {args.own.name})")
            status = 1
        else:
            print(f"The required_ubuntu_packages of {where} are those computed from {args.tools.name}.")
    elif args.yaml:
        print(yaml_block(packages), end="")
    elif args.why:
        for package, names in sources.items():
            print(f"{package}: {', '.join(names)}")
    else:
        print("".join(f"{p}\n" for p in packages), end="")

    for warning in warnings:
        print(f"required-packages: warning: {warning}", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
