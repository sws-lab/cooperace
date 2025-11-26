#!/usr/bin/env python3
"""
Download the latest benchexec wheel into lib/ and extract its license.

By default existing benchexec wheels in lib/ are removed before downloading.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple


ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "lib"
LICENSE_KEYWORDS = ("LICENSE", "LICENCE", "COPYING")


def version_key(path: Path) -> Tuple[Tuple[int, object], ...]:
    match = re.match(r"benchexec-([^-]+)-", path.name)
    if not match:
        return tuple()
    version = match.group(1)
    parts: List[Tuple[int, object]] = []
    for token in re.split(r"[.-]", version):
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token))
    return tuple(parts)


def cleanup_old_files() -> None:
    removed = False
    for pattern in ("benchexec-*.whl", "benchexec-*.whl.license"):
        for path in LIB_DIR.glob(pattern):
            path.unlink()
            removed = True
    if removed:
        print("Removed existing benchexec artifacts from lib/.")


def run_pip_download() -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--no-deps",
        "-d",
        str(LIB_DIR),
        "benchexec",
    ]
    subprocess.check_call(cmd)


def select_wheel(paths: Iterable[Path]) -> Path:
    wheels = sorted(paths, key=version_key)
    if not wheels:
        raise FileNotFoundError("No benchexec wheel found in lib/.")
    return wheels[-1]


def extract_license(wheel: Path, target: Path) -> bool:
    with zipfile.ZipFile(wheel) as zf:
        license_members = [
            name
            for name in zf.namelist()
            if any(keyword in name.upper() for keyword in LICENSE_KEYWORDS)
        ]
        chunks: List[str] = []
        for name in sorted(license_members):
            data = zf.read(name)
            text = data.decode("utf-8", errors="replace").strip()
            if text:
                chunks.append(f"== {name} ==\n{text}")

        if not chunks:
            metadata_entry = next((n for n in zf.namelist() if n.endswith("METADATA")), None)
            if metadata_entry:
                metadata = zf.read(metadata_entry).decode("utf-8", errors="replace")
                license_lines = [line for line in metadata.splitlines() if line.startswith("License:")]
                if license_lines:
                    chunks.append("\n".join(license_lines))

    if not chunks:
        return False

    target.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Download benchexec wheel into lib/ and extract its license.")
    parser.add_argument("--keep-old", action="store_true", help="Do not delete existing benchexec wheels before downloading.")
    args = parser.parse_args()

    LIB_DIR.mkdir(exist_ok=True)
    if not args.keep_old:
        cleanup_old_files()

    run_pip_download()
    wheel = select_wheel(LIB_DIR.glob("benchexec-*.whl"))
    print(f"Downloaded {wheel.name}")

    license_path = LIB_DIR / f"{wheel.name}.license"
    if extract_license(wheel, license_path):
        print(f"Extracted license to {license_path}")
    else:
        print("No license files found in wheel; .license file not created.")


if __name__ == "__main__":
    main()
