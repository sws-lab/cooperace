#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Download the witness validators that tests/integration/run.py uses into
validators/ (git-ignored), one directory per validator version, named
<fm-tools tool>-<version>, which is what run.py expects.

The validators are grouped by the witness format they read, the formats of
WITNESS_FORMATS in tests/integration/run.py:

- yaml-2.2 (the default): the versions proposed for the SV-COMP 2027 track
  "Validation of Violation Witnesses v2" that check no-data-race witnesses,
  and WitnessLint, which checks the format;
- graphml-1.0: the versions SV-COMP 2026 ran on the violation witnesses of
  the no-data-race category, track "Validation of Violation Witnesses v1".

A version is looked up in data/<tool>.yml of the main branch of fm-tools on
gitlab.com, or of the checkout `--fm-tools-dir` (for a version that is only in
an open merge request, a copy of fm-tools with the request applied). A
directory that exists is left as it is.

    scripts/download-validators.py [--dir validators] [--format yaml-2.2|graphml-1.0|all]
                                   [--fm-tools-dir DIR]
"""
import argparse
from pathlib import Path

import yaml
from fm_tools.download import DownloadDelegate
from fm_tools.fmtool import FmTool
from fm_tools.fmtoolversion import FmToolVersion

FM_TOOLS_REPO = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"

# Witness format -> {fm-tools entry: version}
VALIDATORS = {
    "yaml-2.2": {
        "uautomizer": "svcomp27-violation",
        "theta": "svcomp27-validator",
        "concurrentwitness2test": "svcomp27",
        "witnesslint": "2.3-violation-witnesses-v2",
    },
    "graphml-1.0": {
        "cpachecker": "4.2.2-validation-violation",
        "dartagnan": "svcomp26-validation",
        "uautomizer": "svcomp26-violation",
    },
}


def fm_tool_data(tool, fm_tools_dir, delegate):
    """The data of the fm-tools entry `tool`, from `fm_tools_dir` if given,
    else from the main branch of fm-tools."""
    if fm_tools_dir is not None:
        return yaml.safe_load((fm_tools_dir / "data" / f"{tool}.yml").read_text())
    response = delegate.get(f"{FM_TOOLS_REPO}{tool}.yml", headers={"Accept": "application/x-yaml"},
                            follow_redirects=True, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"fm-tools entry of {tool}: status {response.status_code}")
    return yaml.safe_load(response.content)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="validators", type=Path)
    ap.add_argument("--format", choices=[*VALIDATORS, "all"], default="yaml-2.2")
    ap.add_argument("--fm-tools-dir", type=Path,
                    help="a checkout of fm-tools to read data/<tool>.yml from, instead of its main branch")
    args = ap.parse_args()
    args.dir.mkdir(exist_ok=True)
    delegate = DownloadDelegate()
    formats = list(VALIDATORS) if args.format == "all" else [args.format]
    for fmt in formats:
        for tool, version in VALIDATORS[fmt].items():
            target = args.dir / f"{tool}-{version}"
            if target.exists():
                print(f"{target} exists")
                continue
            v = FmToolVersion(FmTool(fm_tool_data(tool, args.fm_tools_dir, delegate)), version)
            print(f"{tool} {version}: {v.get_archive_location().raw}", flush=True)
            v.download_and_install_into(target, delegate=delegate, show_loading_bar=False)


if __name__ == "__main__":
    main()
