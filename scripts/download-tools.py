#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Dict, Iterable, Tuple

import yaml
from fm_tools.download import DownloadDelegate
from fm_tools.fmtool import FmTool
from fm_tools.fmtoolversion import FmToolVersion


FM_TOOLS_REPO = "https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/"
TOOLS = ["goblint", "dartagnan", "uautomizer"]#  "deagle", "ugemcutter", "utaipan", "nacpa", "sv-sanitizers", "cpachecker", "racerf"]
DOI_FILE = Path("tools.txt")
TOOLS_ROOT = Path("tools")
README_FILE = Path("README.md")
SV_COMP_YEAR = 2026


def fetch_fm_tool(tool_name: str, delegate: DownloadDelegate) -> FmTool:
    url = f"{FM_TOOLS_REPO}{tool_name}.yml"
    response = delegate.get(url, headers={"Accept": "application/x-yaml"}, follow_redirects=True, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch metadata for {tool_name} (status {response.status_code}).")
    return FmTool(yaml.safe_load(response.content))


def preferred_version_id(tool: FmTool, year: int) -> str | None:
    tracks = tool.competition_participations.sv_comp(year, error=False)
    try:
        return tracks.verification.tool_version
    except (AttributeError, KeyError):
        return None


def version_index(tool: FmTool) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for entry in tool.versions or []:
        ref = entry.get("doi") or entry.get("url")
        if ref is not None:
            mapping[str(ref)] = str(entry["version"])
    return mapping


def version_for_reference(tool: FmTool, mapping: Dict[str, str], default_id: str, ref: str) -> FmToolVersion:
    if ref == "":
        return FmToolVersion(tool, default_id)
    version_id = mapping.get(ref, default_id)
    return FmToolVersion(tool, version_id)


def read_existing_dois(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    records: Dict[str, str] = {}
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        name, _, doi = raw.partition(":")
        records[name.strip()] = doi.strip()
    return records


def doi_to_url(doi: str) -> str:
    if doi.startswith("10."):
        return f"https://doi.org/{doi}"
    return doi


def render_readme_tool_lines(resolved_dois: Dict[str, str], tool_names: Dict[str, str]) -> list[str]:
    lines: list[str] = []
    for tool in TOOLS:
        name = tool_names.get(tool, tool)
        lines.append(f"* {name}: {doi_to_url(resolved_dois[tool])}")
    return lines


def update_readme(resolved_dois: Dict[str, str], tool_names: Dict[str, str]) -> bool:
    if not README_FILE.exists():
        print(f"{README_FILE} not found; skipping README update.")
        return False

    marker = "It is a metaverfier that currently includes the following tools:"
    lines = README_FILE.read_text().splitlines()
    try:
        start = lines.index(marker) + 1
    except ValueError:
        print(f"Marker not found in {README_FILE}; skipping README update.")
        return False

    end = start
    while end < len(lines) and lines[end].startswith("* "):
        end += 1

    new_block = render_readme_tool_lines(resolved_dois, tool_names)
    if lines[start:end] == new_block:
        print("README tool list already up-to-date.")
        return False

    updated = lines[:start] + new_block + lines[end:]
    README_FILE.write_text("\n".join(updated) + "\n")
    print(f"Updated tool list in {README_FILE}.")
    return True


def prompt_should_update(pending_tools: Iterable[Tuple[str, str, str]], assume_yes: bool) -> bool:
    pending_list = list(pending_tools)
    if not pending_list:
        return False
    print("tools.txt is missing or outdated for:")
    for tool, current, remote in pending_list:
        print(f"  - {tool}: current={current or 'missing'}, remote={remote}")
    if assume_yes:
        return True
    answer = input("Update tools.txt to the latest DOIs? [Y/n]: ").strip().lower()
    return answer in ("", "y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download SV-COMP tool bundles listed in tools.txt.")
    parser.add_argument("--yes", action="store_true", help="Auto-accept updating tools.txt when newer DOIs are available.")
    args = parser.parse_args()

    delegate = DownloadDelegate()
    TOOLS_ROOT.mkdir(exist_ok=True)

    tool_data = {}
    tool_names: Dict[str, str] = {}
    remote_dois: Dict[str, str] = {}
    for tool in TOOLS:
        fm_tool = fetch_fm_tool(tool, delegate)
        index = version_index(fm_tool)
        if not index:
            raise ValueError(f"No versions found in metadata for {tool}.")
        default_id = preferred_version_id(fm_tool, SV_COMP_YEAR) or next(iter(index.values()))
        default_version = FmToolVersion(fm_tool, default_id)
        tool_data[tool] = (fm_tool, index, default_id, default_version)
        tool_names[tool] = getattr(fm_tool, "name", tool)
        remote_dois[tool] = default_version.get_archive_location().raw

    existing_dois = read_existing_dois(DOI_FILE)
    missing_entries = [tool for tool in TOOLS if tool not in existing_dois]

    pending_updates = [
        (tool, existing_dois.get(tool), remote_dois[tool])
        for tool in TOOLS
        if existing_dois.get(tool) != remote_dois[tool]
    ]
    allow_update = prompt_should_update(pending_updates, args.yes)

    resolved_dois: Dict[str, str] = {}
    for tool in TOOLS:
        current = existing_dois.get(tool)
        remote = remote_dois[tool]
        if allow_update or current is None:
            resolved_dois[tool] = remote
        else:
            resolved_dois[tool] = current

    downloads: Dict[str, str] = {}
    for tool in TOOLS:
        target_dir = TOOLS_ROOT / tool
        current_record = existing_dois.get(tool)
        desired_doi = resolved_dois[tool]
        if (not target_dir.exists()) or (current_record != desired_doi):
            downloads[tool] = desired_doi

    if downloads:
        print("Downloading/updating tool bundles:")
        for tool, doi in downloads.items():
            fm_tool, mapping, default_id, default_version = tool_data[tool]
            if doi == default_version.get_archive_location().raw:
                version = default_version
            else:
                version = version_for_reference(fm_tool, mapping, default_id, doi)
            print(f"  - {tool}: {doi}")
            version.download_and_install_into(TOOLS_ROOT / tool, delegate=delegate, show_loading_bar=True)
    else:
        print("All tools are present and match tools.txt.")

    if allow_update or not DOI_FILE.exists() or missing_entries:
        DOI_FILE.write_text("\n".join(f"{tool}: {resolved_dois[tool]}" for tool in TOOLS) + "\n")
        print(f"Updated {DOI_FILE} to reflect active DOIs.")
    else:
        print(f"tools.txt left unchanged ({DOI_FILE}).")

    update_readme(resolved_dois, tool_names)


if __name__ == "__main__":
    main()
