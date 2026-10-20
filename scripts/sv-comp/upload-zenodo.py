#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Upload the CoOpeRace SV-COMP archive to a Zenodo deposition.

This script targets private (draft) Zenodo records.  It expects an editable
record and will automatically follow the "latest_draft" link if you point it at
an already published deposition, so you can upload to the most recent draft
version.  Existing files with the same name are replaced.

The archive is sent with one PUT request to the draft's files-API bucket
(links.bucket), streamed from disk, so the progress display counts the bytes
handed to the connection.  Afterwards the MD5 of the local file is compared
with the checksum Zenodo reports for the uploaded file; on a mismatch the
script prints both and exits 1.

Usage example:
    python scripts/sv-comp/upload-zenodo.py --record-id 123456

The draft's metadata (title, creators, version, license, ...) is replaced by the
contents of --metadata before the file is uploaded; the default is
zenodo-metadata.json next to this script.  Pass --no-metadata to leave the
draft's metadata as it is.

Configuration defaults to environment variables for the token so you can export
ZENODO_TOKEN once and run the script without flags. The record ID must be
provided explicitly via --record-id.

The draft's "version" is the line of cooperace/VERSION in the archive (for
example "svcomp27 745cd85": the fm-tools version and the commit the archive was
built from), so that several uploads can be told apart.  --version overrides it;
without an archive, the "version" of the metadata file is kept.

--metadata-only sets the draft's metadata and uploads nothing, so it needs no
archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover - import guard
    print(
        "The requests package is required. Install it with 'pip install requests'",
        file=sys.stderr,
    )
    sys.exit(1)


DEFAULT_ARCHIVE = Path("dist/cooperace.zip")
DEFAULT_METADATA = Path(__file__).with_name("zenodo-metadata.json")
PRODUCTION_API = "https://zenodo.org/api"
SANDBOX_API = "https://sandbox.zenodo.org/api"


# States in which the deposit API accepts file changes: a new draft is
# "unsubmitted", one with uploaded files "inprogress"; a published record is "done".
EDITABLE_STATES = {"draft", "unsubmitted", "inprogress"}


class ZenodoError(RuntimeError):
    """Wrapper for API failures with helpful context."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record-id",
        type=int,
        required=True,
        help="Zenodo deposition/record ID",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ZENODO_TOKEN"),
        help="Zenodo access token (or set ZENODO_TOKEN)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_ARCHIVE,
        help=f"Archive to upload (default: {DEFAULT_ARCHIVE})",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA,
        help=f'JSON file {{"metadata": {{...}}}} for the draft (default: {DEFAULT_METADATA.name} next to this script)',
    )
    parser.add_argument(
        "--version",
        help="Version string of the record (default: the line of cooperace/VERSION in the archive)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Set the draft's metadata and upload no file",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Do not touch the draft's metadata",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Use the Zenodo sandbox instance instead of production",
    )
    return parser.parse_args()


def zenodo_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "User-Agent": "cooperace-zenodo-upload/1.0",
        }
    )
    return session


def fetch_json(session: requests.Session, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=60)
    if response.status_code >= 400:
        raise ZenodoError(f"GET {url} failed with {response.status_code}: {response.text}")
    return response.json()


def ensure_draft(session: requests.Session, base_url: str, record_id: int) -> dict[str, Any]:
    deposition_url = f"{base_url}/deposit/depositions/{record_id}"
    deposition = fetch_json(session, deposition_url)
    if deposition.get("state") in EDITABLE_STATES:
        return deposition

    latest_draft = deposition.get("links", {}).get("latest_draft")
    if not latest_draft:
        raise ZenodoError(
            "The requested record is not editable and has no latest draft. Create a new version in the Zenodo UI first."
        )

    draft = fetch_json(session, latest_draft)
    if draft.get("state") not in EDITABLE_STATES:
        raise ZenodoError(f"Unable to obtain an editable draft from latest_draft link. State: {draft.get('state')}")
    return draft


def update_metadata(
    session: requests.Session,
    base_url: str,
    draft: dict[str, Any],
    metadata_path: Path,
    version: str | None = None,
) -> dict[str, Any]:
    """Replace the draft's metadata with the "metadata" object in metadata_path.

    The deposit API's PUT replaces all metadata, so fields missing from the
    file are cleared.  Zenodo's validation errors are reported in ZenodoError.
    """
    payload = json.loads(metadata_path.read_text())
    if version:
        payload["metadata"]["version"] = version
    url = f"{base_url}/deposit/depositions/{draft['id']}"
    response = session.put(url, json=payload, timeout=60)
    if response.status_code >= 400:
        raise ZenodoError(f"PUT {url} failed with {response.status_code}: {response.text}")
    updated = response.json()
    print(f"Updated metadata of record {draft['id']} from {metadata_path.name}.")
    return updated


def delete_existing(session: requests.Session, base_url: str, draft: dict[str, Any], filename: str) -> None:
    files = draft.get("files", []) or []
    for entry in files:
        if entry.get("filename") == filename:
            file_id = entry.get("id")
            delete_url = f"{base_url}/deposit/depositions/{draft['id']}/files/{file_id}"
            response = session.delete(delete_url, timeout=60)
            if response.status_code not in {204, 404}:
                raise ZenodoError(f"DELETE {delete_url} failed with {response.status_code}: {response.text}")
            if response.status_code == 204:
                print(f"Removed existing file '{filename}' (id={file_id}).")
            return


class ProgressFile:
    """Wrap a file handle to emit upload progress while streaming bytes.

    `requests` sends such an object as the body of a request: it takes the
    Content-Length from __len__ and reads the body in blocks as it writes them
    to the connection, so `_read` is the number of bytes handed to the
    connection, at most one block ahead of the bytes sent."""

    def __init__(
        self,
        fh: IO[bytes],
        total_bytes: int,
        report_every: int | None = None,
        label: str = "Upload",
    ) -> None:
        self._fh = fh
        self._total = total_bytes
        self._read = 0
        self._label = label
        # default to 5% increments, but at least every 8 MiB and at most 64 MiB
        if report_every is None and total_bytes:
            report_every = max(int(total_bytes * 0.05), 8 * 1024 * 1024)
            report_every = min(report_every, 64 * 1024 * 1024)
        self._report_every = report_every or (8 * 1024 * 1024)
        self._last_report = 0
        self._last_print_time = 0.0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        if chunk:
            self._read += len(chunk)
            self._maybe_report()
        return chunk

    def __len__(self) -> int:
        return self._total

    def __iter__(self) -> Iterator[bytes]:
        while chunk := self.read(64 * 1024):
            yield chunk

    def tell(self) -> int:  # pragma: no cover - passthrough helper
        return self._fh.tell()

    def _maybe_report(self) -> None:
        if not self._total:
            return
        now = time.time()
        if (
            self._read - self._last_report >= self._report_every
            or self._read == self._total
            or now - self._last_print_time >= 30
        ):
            percent = self._read / self._total * 100
            print(
                f"{self._label} progress: {percent:5.1f}% ({self._read:,}/{self._total:,} bytes)",
                flush=True,
            )
            self._last_report = self._read
            self._last_print_time = now

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - passthrough helper
        return getattr(self._fh, name)


def md5_of(path: Path) -> str:
    """The MD5 of the file `path`, as lower-case hex."""
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_checksum(checksum: str | None) -> str:
    """The hex digest in a checksum that Zenodo reports, with or without an
    "md5:" prefix; "" if there is none."""
    return (checksum or "").removeprefix("md5:").strip().lower()


def upload_file(
    session: requests.Session,
    draft: dict[str, Any],
    file_path: Path,
) -> dict[str, Any]:
    """PUT `file_path` to the bucket of the deposition `draft` (its
    links.bucket), streaming it, and return Zenodo's JSON for the file."""
    bucket = draft.get("links", {}).get("bucket")
    if not bucket:
        raise ZenodoError("The draft has no links.bucket to upload to.")
    target_url = f"{bucket}/{quote(file_path.name)}"
    file_size = file_path.stat().st_size
    print(
        f"Uploading {file_path.name} ({file_size:,} bytes) to record {draft['id']}...",
        flush=True,
    )
    with file_path.open("rb") as fh:
        progress = ProgressFile(fh, file_size, label=file_path.name)
        response = session.put(
            target_url,
            data=progress,
            headers={"Content-Type": "application/octet-stream"},
            timeout=(60, 900),
        )
    if response.status_code >= 400:
        raise ZenodoError(f"PUT {target_url} failed with {response.status_code}: {response.text}")
    return response.json()


def reported_checksum(
    session: requests.Session,
    base_url: str,
    draft_id: int,
    uploaded: dict[str, Any],
    filename: str,
) -> str:
    """The MD5 that Zenodo reports for the uploaded file: the checksum in the
    reply to the PUT or, if the reply has none, the one in the draft's file
    list."""
    checksum = normalize_checksum(uploaded.get("checksum"))
    if checksum:
        return checksum
    listing = session.get(f"{base_url}/deposit/depositions/{draft_id}/files", timeout=60)
    if listing.status_code >= 400:
        raise ZenodoError(f"GET files of record {draft_id} failed with {listing.status_code}: {listing.text}")
    for entry in listing.json():
        if entry.get("filename") == filename:
            return normalize_checksum(entry.get("checksum"))
    return ""


def archive_version(archive_path: Path) -> str | None:
    """The line of cooperace/VERSION in the archive, or None if the archive is
    missing or has no such entry."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return archive.read("cooperace/VERSION").decode().strip() or None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def main() -> None:
    args = parse_args()
    if not args.token:
        print("--token not provided and ZENODO_TOKEN is unset.", file=sys.stderr)
        sys.exit(2)

    archive_path: Path = args.file
    if args.metadata_only:
        if args.no_metadata:
            print("--metadata-only and --no-metadata contradict each other.", file=sys.stderr)
            sys.exit(2)
    elif not archive_path.exists():
        print(f"Archive '{archive_path}' does not exist. Run make svcomp first?", file=sys.stderr)
        sys.exit(2)

    version = args.version or archive_version(archive_path)
    base_url = SANDBOX_API if args.sandbox else PRODUCTION_API
    session = zenodo_session(args.token)

    try:
        draft = ensure_draft(session, base_url, int(args.record_id))
        if not args.no_metadata:
            update_metadata(session, base_url, draft, args.metadata, version)
        if args.metadata_only:
            return
        delete_existing(session, base_url, draft, archive_path.name)
        uploaded = upload_file(session, draft, archive_path)
        remote_md5 = reported_checksum(session, base_url, draft["id"], uploaded, archive_path.name)
    except (ZenodoError, requests.RequestException) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    local_md5 = md5_of(archive_path)
    if remote_md5 != local_md5:
        print(
            f"Error: checksum mismatch for {archive_path.name}: local md5 {local_md5}, "
            f"Zenodo reports {remote_md5 or 'none'}. The file in the draft is not the local file; "
            "upload it again before publishing.",
            file=sys.stderr,
        )
        sys.exit(1)

    file_id = uploaded.get("id") or uploaded.get("key")
    checksum = f"md5:{remote_md5} (matches the local file)"
    size = uploaded.get("filesize") or uploaded.get("size")
    print(
        "Upload complete!",
        f"record_id={draft['id']}",
        f"file_id={file_id}",
        f"size={size}",
        f"checksum={checksum}",
    )


if __name__ == "__main__":
    main()
