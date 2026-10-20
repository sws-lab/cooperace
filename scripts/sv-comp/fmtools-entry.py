#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Reads the version that fm-tools' CI checks out of CoOpeRace's fm-tools entry.

    scripts/sv-comp/fmtools-entry.py [--entry PATH_OR_URL] [--year 2027] OUTPUT_DIR

The entry (data/cooperace.yml of fm-tools) is a local file, or a URL that is
downloaded; the default is the file on fm-tools' branch `cooperace-2027`. The
versions taken are those that the participations of SV-COMP YEAR and Test-Comp
YEAR name as `tool_version` and that have no `full_container_images`, the
selection that fm-tools' CI jobs `check-valid-required-packages-YEAR` and
`check-installed-required-packages-YEAR` make with `yq` in its
ci/.gitlab-ci.yml. Their `required_ubuntu_packages`, without duplicates and
sorted as `yq unique` sorts them, are written one per line to
OUTPUT_DIR/required-packages.txt; the version name to OUTPUT_DIR/version and
its DOI, or nothing if it has none, to OUTPUT_DIR/doi.

Exits with status 1 and a message on stderr if no participation of YEAR
names a version, if more than one version is named, or if the version named
has `full_container_images`: for such a version the CI checks no package,
and its job `smoke-test` runs the archive in that image, which no check here
reproduces.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import yaml

DEFAULT_ENTRY = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/cooperace-2027/data/cooperace.yml"


def load_entry(location: str) -> dict:
    """The parsed entry at `location`, a URL (http or https) or a file path."""
    if location.startswith(("http://", "https://")):
        with urllib.request.urlopen(location, timeout=60) as response:
            text = response.read().decode()
    else:
        text = Path(location).read_text()
    return yaml.safe_load(text)


def checked_version(entry: dict, year: int) -> dict:
    """The one version of `entry` whose packages fm-tools' CI checks for `year`.

    Raises ValueError naming what is wrong when there is not exactly one
    participation version, or when it has `full_container_images`.
    """
    competitions = {f"SV-COMP {year}", f"Test-Comp {year}"}
    named = sorted({p["tool_version"] for p in entry.get("competition_participations") or []
                    if p.get("competition") in competitions})
    if not named:
        raise ValueError(f"no participation of SV-COMP {year} or Test-Comp {year} names a tool_version")
    if len(named) > 1:
        raise ValueError(f"the participations of {year} name more than one tool_version: {', '.join(named)}")
    versions = [v for v in entry.get("versions") or [] if v.get("version") == named[0]]
    if len(versions) != 1:
        raise ValueError(f"tool_version {named[0]!r} is listed {len(versions)} times under versions, not once")
    version = versions[0]
    if version.get("full_container_images"):
        raise ValueError(
            f"version {named[0]!r} has full_container_images, so fm-tools' CI checks none of its "
            "required_ubuntu_packages and runs its smoke test in that image; give the participation a "
            "version without full_container_images")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entry", default=DEFAULT_ENTRY,
                        help="path or URL of data/cooperace.yml (default: %(default)s)")
    parser.add_argument("--year", type=int, default=2027, help="competition year (default: %(default)s)")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    try:
        version = checked_version(load_entry(args.entry), args.year)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
        print(f"fmtools-entry: error: {args.entry}: {error}", file=sys.stderr)
        return 1

    packages = sorted(set(version.get("required_ubuntu_packages") or []))
    doi = version.get("doi") or ""
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "required-packages.txt").write_text("".join(f"{p}\n" for p in packages))
    (args.output_dir / "version").write_text(f"{version['version']}\n")
    (args.output_dir / "doi").write_text(f"{doi}\n" if doi else "")
    print(f"fm-tools entry: {args.entry}")
    print(f"Version of the SV-COMP {args.year} participation: {version['version']}, DOI {doi or '(none yet)'}")
    print(f"Its required_ubuntu_packages: {' '.join(packages) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
