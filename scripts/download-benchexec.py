#!/usr/bin/env python3
"""Installs the BenchExec wheel that CoOpeRace bundles into lib/.

CoOpeRace reads each component's output with that component's BenchExec
tool-info module, imported from the one wheel lib/benchexec-*.whl (see
src/cooperace/__init__.py). The wheel should be the BenchExec version that
the SV-COMP bench-defs pin (the `benchexec` submodule of
https://gitlab.com/sosy-lab/sv-comp/bench-defs), so that the competition and
CoOpeRace interpret a component's output in the same way.

The version and the SHA-256 of its wheel are BENCHEXEC_VERSION and
BENCHEXEC_SHA256 below. The script writes them as a requirements file with a
hash and runs `pip download --require-hashes` on it, so pip refuses a wheel
other than that file. `--version V` installs another version instead: the
hash is then looked up in PyPI's JSON API (so it protects against a damaged
download, not against a wheel that PyPI itself serves wrongly), and the script
prints the line to copy into BENCHEXEC_VERSION and BENCHEXEC_SHA256.

The script never removes a wheel unasked. If lib/ holds a benchexec wheel
other than the wanted one, it exits with status 1 and changes nothing;
`--replace` removes the other wheels and their .license files after the new
wheel has been downloaded and checked. The .license file of a wheel is the
text of the license files inside the wheel (or, failing that, the `License:`
line of its metadata), written beside the wheel.

The script can be run from any directory; lib/ is in the repository that
contains it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BENCHEXEC_VERSION = "3.35"
BENCHEXEC_SHA256 = "af196e0fa5715038a81c4ac25e7427cf312560da8664dc40e5da97454af33a91"
PYPI_JSON_URL = "https://pypi.org/pypi/benchexec/{version}/json"
ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "lib"
LICENSE_KEYWORDS = ("LICENSE", "LICENCE", "COPYING")


def wheel_name(version: str) -> str:
    """The file name of the wheel of BenchExec `version` on PyPI."""
    return f"benchexec-{version}-py3-none-any.whl"


def pypi_wheel_sha256(version: str) -> str:
    """The SHA-256 that PyPI's JSON API gives for `wheel_name(version)`.
    Raises ValueError if PyPI lists no such wheel, or urllib's error if PyPI
    cannot be reached."""
    with urllib.request.urlopen(PYPI_JSON_URL.format(version=version), timeout=30) as response:
        files = json.load(response)["urls"]
    for entry in files:
        if entry["filename"] == wheel_name(version) and not entry.get("yanked"):
            return entry["digests"]["sha256"]
    raise ValueError(f"PyPI lists no wheel {wheel_name(version)} for benchexec {version}")


def requirements_text(version: str, sha256: str) -> str:
    """A requirements file that pins benchexec `version` to the wheel with `sha256`."""
    return f"benchexec=={version} --hash=sha256:{sha256}\n"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def other_wheels(lib_dir: Path, wanted: str) -> list[Path]:
    """The benchexec wheels in `lib_dir` whose file name is not `wanted`."""
    return sorted(path for path in lib_dir.glob("benchexec-*.whl") if path.name != wanted)


def license_file(wheel: Path) -> Path:
    return wheel.with_name(wheel.name + ".license")


def extract_license(wheel: Path, target: Path) -> bool:
    """Writes the license texts inside `wheel` to `target`, each under a line
    "== <member name> ==", or, if the wheel has none, the `License:` lines of
    its METADATA. Returns False, and writes nothing, if there is neither."""
    with zipfile.ZipFile(wheel) as zf:
        license_members = [
            name
            for name in zf.namelist()
            if any(keyword in name.upper() for keyword in LICENSE_KEYWORDS)
        ]
        chunks: list[str] = []
        for name in sorted(license_members):
            text = zf.read(name).decode("utf-8", errors="replace").strip()
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


def pip_download(requirements: Path, directory: Path) -> None:
    """Downloads the wheel that `requirements` pins into `directory`. pip
    refuses a file whose hash is not in the requirements."""
    subprocess.check_call([
        sys.executable, "-m", "pip", "download",
        "--require-hashes", "--only-binary=:all:", "--no-deps",
        "-d", str(directory), "-r", str(requirements),
    ])


def install(version: str, sha256: str, lib_dir: Path, replace: bool) -> Path:
    """Makes `wheel_name(version)` the one benchexec wheel of `lib_dir`, with
    its .license file, and returns its path.

    Raises FileExistsError, before anything is downloaded or changed, if
    `lib_dir` holds another benchexec wheel and `replace` is false. An
    already present wheel of this version is kept if its SHA-256 is `sha256`
    and is a ValueError otherwise. With `replace`, the other wheels and their
    .license files are removed only after the new wheel is in place."""
    name = wheel_name(version)
    others = other_wheels(lib_dir, name)
    if others and not replace:
        raise FileExistsError(
            f"{lib_dir} holds {', '.join(path.name for path in others)}; "
            f"--replace removes it and installs {name}")
    wheel = lib_dir / name
    if wheel.exists():
        if file_sha256(wheel) != sha256:
            raise ValueError(f"{wheel} is not the wheel with SHA-256 {sha256}; remove it to download it again")
    else:
        lib_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text(requirements_text(version, sha256), encoding="utf-8")
            downloaded = Path(tmp) / "wheel"
            downloaded.mkdir()
            pip_download(requirements, downloaded)
            fetched = downloaded / name
            if not fetched.is_file() or file_sha256(fetched) != sha256:
                raise ValueError(f"pip did not produce {name} with SHA-256 {sha256}")
            shutil.move(str(fetched), wheel)
    for path in others:
        path.unlink()
        license_file(path).unlink(missing_ok=True)
    if not license_file(wheel).exists():
        if not extract_license(wheel, license_file(wheel)):
            print(f"No license text in {name}; no .license file written.", file=sys.stderr)
    return wheel


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install the BenchExec wheel that CoOpeRace bundles into lib/.")
    parser.add_argument("--version", default=BENCHEXEC_VERSION,
                        help=f"BenchExec version to install (default: {BENCHEXEC_VERSION}); "
                             "another version has its hash looked up on PyPI")
    parser.add_argument("--replace", action="store_true",
                        help="remove other benchexec wheels (and their .license files) from lib/")
    args = parser.parse_args()

    try:
        if args.version == BENCHEXEC_VERSION:
            sha256 = BENCHEXEC_SHA256
        else:
            sha256 = pypi_wheel_sha256(args.version)
            print(f"To make {args.version} the default, set in {Path(__file__).name}:\n"
                  f'BENCHEXEC_VERSION = "{args.version}"\nBENCHEXEC_SHA256 = "{sha256}"')
        wheel = install(args.version, sha256, LIB_DIR, args.replace)
    except (FileExistsError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"download-benchexec: error: {error}", file=sys.stderr)
        return 1
    print(f"lib/ holds {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
