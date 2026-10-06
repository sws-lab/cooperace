#!/usr/bin/env python3
"""Download the witness validators that tests/integration/run.py uses into
validators/ (git-ignored), one directory per validator version.

The validators are the ones SV-COMP 2026 ran on the violation witnesses of the
no-data-race category (witness format graphml 1.0): the version named in each
tool's fm-tools entry for the track "Validation of Violation Witnesses v1".
Directory names are <fm-tools tool>-<version>, which is what run.py expects.

    scripts/download-validators.py [--dir validators]
"""
import argparse
from pathlib import Path

import yaml
from fm_tools.download import DownloadDelegate
from fm_tools.fmtool import FmTool
from fm_tools.fmtoolversion import FmToolVersion

FM_TOOLS_REPO = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"

# fm-tools entry -> versions of SV-COMP 2026's violation-witness validation
VALIDATORS = {
    "cpachecker": "4.2.2-validation-violation",
    "dartagnan": "svcomp26-validation",
    "uautomizer": "svcomp26-violation",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="validators", type=Path)
    args = ap.parse_args()
    args.dir.mkdir(exist_ok=True)
    delegate = DownloadDelegate()
    for tool, version in VALIDATORS.items():
        target = args.dir / f"{tool}-{version}"
        if target.exists():
            print(f"{target} exists")
            continue
        response = delegate.get(f"{FM_TOOLS_REPO}{tool}.yml", headers={"Accept": "application/x-yaml"},
                                follow_redirects=True, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"fm-tools entry of {tool}: status {response.status_code}")
        fm_tool = FmTool(yaml.safe_load(response.content))
        v = FmToolVersion(fm_tool, version)
        print(f"{tool} {version}: {v.get_archive_location().raw}", flush=True)
        v.download_and_install_into(target, delegate=delegate, show_loading_bar=False)


if __name__ == "__main__":
    main()
