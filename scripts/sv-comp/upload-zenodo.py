#!/usr/bin/env python3
"""Upload the CoOpeRace SV-COMP archive to a Zenodo deposition.

This script targets private (draft) Zenodo records.  It expects an editable
record and will automatically follow the "latest_draft" link if you point it at
an already published deposition, so you can upload to the most recent draft
version.  Existing files with the same name are replaced.

Usage example:
    python scripts/sv-comp/upload-zenodo.py --record-id 123456

Configuration defaults to environment variables for the token so you can export
ZENODO_TOKEN once and run the script without flags. The record ID must be
provided explicitly via --record-id.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import IO, Any, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover - import guard
    print(
        "The requests package is required. Install it with 'pip install requests'",
        file=sys.stderr,
    )
    sys.exit(1)


DEFAULT_ARCHIVE = Path("dist/cooperace.zip")
PRODUCTION_API = "https://zenodo.org/api"
SANDBOX_API = "https://sandbox.zenodo.org/api"


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
        "--sandbox",
        action="store_true",
        help="Use the Zenodo sandbox instance instead of production",
    )
    return parser.parse_args()


def zenodo_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "User-Agent": "cooperace-zenodo-upload/1.0",
    })
    return session


def fetch_json(session: requests.Session, url: str) -> Dict[str, Any]:
    response = session.get(url, timeout=60)
    if response.status_code >= 400:
        raise ZenodoError(
            f"GET {url} failed with {response.status_code}: {response.text}"
        )
    return response.json()


def ensure_draft(
    session: requests.Session, base_url: str, record_id: int
) -> Dict[str, Any]:
    deposition_url = f"{base_url}/deposit/depositions/{record_id}"
    deposition = fetch_json(session, deposition_url)
    if deposition.get("state") == "draft":
        return deposition

    latest_draft = deposition.get("links", {}).get("latest_draft")
    if not latest_draft:
        raise ZenodoError(
            "The requested record is not editable and has no latest draft. "
            "Create a new version in the Zenodo UI first."
        )

    draft = fetch_json(session, latest_draft)
    if draft.get("state") != "draft":
        raise ZenodoError(
            f"Unable to obtain an editable draft from latest_draft link. State: {draft.get('state')}"
        )
    return draft


def delete_existing(
    session: requests.Session, base_url: str, draft: Dict[str, Any], filename: str
) -> None:
    files = draft.get("files", []) or []
    for entry in files:
        if entry.get("filename") == filename:
            file_id = entry.get("id")
            delete_url = f"{base_url}/deposit/depositions/{draft['id']}/files/{file_id}"
            response = session.delete(delete_url, timeout=60)
            if response.status_code not in {204, 404}:
                raise ZenodoError(
                    f"DELETE {delete_url} failed with {response.status_code}: {response.text}"
                )
            if response.status_code == 204:
                print(f"Removed existing file '{filename}' (id={file_id}).")
            return


class ProgressFile:
    """Wrap a file handle to emit upload progress while streaming bytes."""

    def __init__(
        self,
        fh: IO[bytes],
        total_bytes: int,
        report_every: Optional[int] = None,
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
                f"{self._label} progress: {percent:5.1f}% "
                f"({self._read:,}/{self._total:,} bytes)",
                flush=True,
            )
            self._last_report = self._read
            self._last_print_time = now

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - passthrough helper
        return getattr(self._fh, name)


def upload_file(
    session: requests.Session,
    base_url: str,
    draft_id: int,
    file_path: Path,
) -> Dict[str, Any]:
    target_url = f"{base_url}/deposit/depositions/{draft_id}/files"
    file_size = file_path.stat().st_size
    print(
        f"Uploading {file_path.name} ({file_size:,} bytes) to record {draft_id}...",
        flush=True,
    )
    with file_path.open("rb") as fh:
        progress = ProgressFile(fh, file_size, label=file_path.name)
        files = {"file": (file_path.name, progress, "application/zip")}
        data = {"name": file_path.name}
        response = session.post(target_url, data=data, files=files, timeout=300)
    if response.status_code >= 400:
        raise ZenodoError(
            f"POST {target_url} failed with {response.status_code}: {response.text}"
        )
    return response.json()


def main() -> None:
    args = parse_args()
    if not args.token:
        print("--token not provided and ZENODO_TOKEN is unset.", file=sys.stderr)
        sys.exit(2)

    archive_path: Path = args.file
    if not archive_path.exists():
        print(f"Archive '{archive_path}' does not exist. Run make svcomp first?", file=sys.stderr)
        sys.exit(2)

    base_url = SANDBOX_API if args.sandbox else PRODUCTION_API
    session = zenodo_session(args.token)

    try:
        draft = ensure_draft(session, base_url, int(args.record_id))
        delete_existing(session, base_url, draft, archive_path.name)
        uploaded = upload_file(session, base_url, draft["id"], archive_path)
    except ZenodoError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    file_id = uploaded.get("id")
    checksum = uploaded.get("checksum")
    size = uploaded.get("filesize")
    print(
        "Upload complete!",
        f"record_id={draft['id']}",
        f"file_id={file_id}",
        f"size={size}",
        f"checksum={checksum}",
    )


if __name__ == "__main__":
    main()
