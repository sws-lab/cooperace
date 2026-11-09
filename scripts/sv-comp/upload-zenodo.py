#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Upload the CoOpeRace SV-COMP archive to a draft of a Zenodo record, and
optionally publish it.

The archive goes into an unpublished draft as the file cooperace.zip, whatever
the local file is called (--upload-name changes that name).  --record-id names
that draft or a published version of the record.  For a published version the
script uses the draft of the record's next version if there is one (made in
the Zenodo UI with "New version", or by an earlier run); if there is none,
--new-version creates it with the deposit API's newversion action, and without
--new-version the script stops.  It never creates a second draft.  A published
version opened for editing is refused: the script changes files only in a
draft that has never been published.

An archive that the latest published version already holds (the upload's name
with the local MD5) is refused before a draft is created or changed, so that
running again after a publish whose reply was lost does not publish the same
archive a second time.

fm-tools accepts a Zenodo record with exactly one file, and Zenodo's
newversion action copies the files of the latest published version into the
new draft.  Before anything in the draft is changed, its files are sorted out:
  - a file of the upload's name (cooperace.zip) with the MD5 of the local file
    is kept and the archive is not sent again;
  - a file with the name and MD5 of a file of the latest published version was
    inherited from that version and is deleted;
  - another file of the upload's name is an earlier upload to this draft and
    is deleted;
  - any other file stops the script, which lists it and uploads nothing.

The archive is sent with one PUT request to the draft's files-API bucket
(links.bucket), streamed from disk, so the progress display counts the bytes
handed to the connection.  Afterwards the MD5 of the local file is compared
with the checksum Zenodo reports for the uploaded file, and the draft must
hold exactly one file, the archive under the upload's name; otherwise the
script prints what it found and exits 1.

The script prints the draft's record ID, the DOI Zenodo reserved for it and
its web page, so that the draft can be inspected before it is published, and
the command that publishes it.  --publish publishes the draft once both checks
have passed, then checks that the published record holds exactly that one
file and prints its DOI and page.  A published version cannot be withdrawn,
so the script publishes only when asked to.  If the publish request fails
without a clear refusal from Zenodo, whether the draft was published is
unknown: check the record's page before running again.

The usual upload takes two runs.  The first makes the draft of the next
version of the published record 123456 and uploads the archive to it as
cooperace.zip; the second, after the draft has been checked on Zenodo,
publishes that draft, named by the ID the first run printed:
    python scripts/sv-comp/upload-zenodo.py --record-id 123456 --new-version --file dist/E8-default-42d6c9a.zip
    python scripts/sv-comp/upload-zenodo.py --record-id <draft ID> --publish --file dist/E8-default-42d6c9a.zip
The second run finds the archive in the draft, sends nothing and leaves the
draft's metadata as it is.

The draft's metadata (title, creators, version, license, ...) is replaced by the
contents of --metadata before the file is uploaded; the default is
zenodo-metadata.json next to this script.  Pass --no-metadata to leave the
draft's metadata as it is.  When the draft already holds the archive and
nothing is uploaded, the metadata is not touched either (--metadata-only sets
it).

Configuration defaults to environment variables for the token so you can export
ZENODO_TOKEN once and run the script without flags. The record ID must be
provided explicitly via --record-id.

The draft's "version" is the line of cooperace/VERSION in the archive (for
example "svcomp27 745cd85": the fm-tools version and the commit the archive was
built from), so that several uploads can be told apart.  --version overrides it;
without an archive, the "version" of the metadata file is kept.

--metadata-only sets the draft's metadata and uploads nothing, so it needs no
archive; it leaves the draft's files as they are and cannot be combined with
--publish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
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
# The name of the archive on Zenodo, whatever the local file is called.
UPLOAD_NAME = "cooperace.zip"
DEFAULT_METADATA = Path(__file__).with_name("zenodo-metadata.json")
PRODUCTION_API = "https://zenodo.org/api"
SANDBOX_API = "https://sandbox.zenodo.org/api"


# States in which the deposit API accepts file changes: a new draft is
# "unsubmitted", one with uploaded files "inprogress"; a published record is "done".
# A published record opened for editing is "inprogress" too, but "submitted".
EDITABLE_STATES = {"draft", "unsubmitted", "inprogress"}


class ZenodoError(RuntimeError):
    """Wrapper for API failures with helpful context."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--record-id",
        type=int,
        required=True,
        help="Zenodo deposition/record ID: a draft, or a published version of the record",
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
        "--upload-name",
        default=UPLOAD_NAME,
        help=f"Name of the archive on Zenodo, whatever --file is called (default: {UPLOAD_NAME})",
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
        "--new-version",
        action="store_true",
        help="If --record-id is published and has no draft of a new version, create that draft",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the draft once it holds exactly one file, the archive, with the local MD5 (cannot be undone)",
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


def fetch_json(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> Any:
    response = session.get(url, params=params, timeout=60)
    if response.status_code >= 400:
        raise ZenodoError(f"GET {url} failed with {response.status_code}: {response.text}")
    return response.json()


def is_new_draft(deposition: dict[str, Any]) -> bool:
    """Whether the deposition is an unpublished draft: a new record or a new
    version of one, not a published version opened for editing."""
    return deposition.get("state") in EDITABLE_STATES and deposition.get("submitted") is False


def check_draft_of(draft: dict[str, Any], published: dict[str, Any], source: str) -> dict[str, Any]:
    """The draft, if it is an unpublished draft of a version of the same record
    as the published deposition; else ZenodoError, naming where it came from."""
    if not is_new_draft(draft):
        raise ZenodoError(
            f"{source} names deposition {draft.get('id')}, which is not an unpublished draft "
            f"(state {draft.get('state')}, submitted {draft.get('submitted')})."
        )
    if str(draft.get("conceptrecid")) != str(published.get("conceptrecid")):
        raise ZenodoError(
            f"{source} names deposition {draft.get('id')} of the concept {draft.get('conceptrecid')}, "
            f"not of record {published.get('id')}'s concept {published.get('conceptrecid')}."
        )
    return draft


def concept_depositions(session: requests.Session, base_url: str, concept: Any) -> list[dict[str, Any]]:
    """The depositions of every version of the record whose concept is
    `concept`, drafts included, as the deposit API's search finds them.  The
    search index can lag behind: the state and files it shows can be old."""
    found = fetch_json(
        session,
        f"{base_url}/deposit/depositions",
        {"q": f"conceptrecid:{concept}", "all_versions": 1, "size": 100},
    )
    return [d for d in found if str(d.get("conceptrecid")) == str(concept)]


def existing_draft(session: requests.Session, base_url: str, published: dict[str, Any]) -> dict[str, Any] | None:
    """The draft of a new version of the published deposition's record, or None.

    links.latest_draft of a published version named that draft on the old
    Zenodo; on the current one it names the published version itself, so the
    depositions of the record's concept are searched as well.  The search
    index can lag behind, so the draft it finds is fetched again by its ID and
    checked again."""
    latest_draft = published.get("links", {}).get("latest_draft")
    if latest_draft:
        candidate = fetch_json(session, latest_draft)
        if candidate.get("id") != published.get("id") and is_new_draft(candidate):
            return check_draft_of(candidate, published, f"links.latest_draft of record {published.get('id')}")
    concept = published.get("conceptrecid")
    if not concept:
        return None
    # A deposition just published can still show as a draft in the search.
    drafts = [
        d for d in concept_depositions(session, base_url, concept) if is_new_draft(d) and d.get("id") != published["id"]
    ]
    if len(drafts) > 1:
        ids = ", ".join(str(d.get("id")) for d in drafts)
        raise ZenodoError(f"The record {published.get('id')} has several drafts of a new version ({ids}).")
    if not drafts:
        return None
    fresh = fetch_json(session, f"{base_url}/deposit/depositions/{drafts[0]['id']}")
    try:
        return check_draft_of(fresh, published, "The deposit search")
    except ZenodoError as err:
        raise ZenodoError(
            f"{err} The search index is stale. Run again in a minute, or pass the draft's ID with --record-id."
        ) from None


def ensure_draft(
    session: requests.Session,
    base_url: str,
    record_id: int,
    new_version: bool = False,
    archive: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """The draft to upload to: the deposition record_id if it is a draft, else
    the draft of a new version of that published record, which is created
    with the newversion action if there is none and new_version is set.
    `archive`, the upload's name and the local MD5, is refused before a draft
    is created if the latest published version already holds it."""
    deposition = fetch_json(session, f"{base_url}/deposit/depositions/{record_id}")
    if is_new_draft(deposition):
        return deposition
    if deposition.get("state") in EDITABLE_STATES:
        raise ZenodoError(
            f"Record {record_id} is in state {deposition.get('state')} with submitted "
            f"{deposition.get('submitted')}: a published version opened for editing, or a deposition the script "
            "cannot tell from one. The script changes files only in a draft that has never been published. "
            "Discard or publish that edit in the Zenodo UI, then upload to a new version (--new-version)."
        )

    draft = existing_draft(session, base_url, deposition)
    if draft:
        print(f"Using the existing draft {draft['id']} of a new version of record {record_id}.")
        return draft
    if archive:
        refuse_if_published(latest_published(session, base_url, deposition), *archive)
    if not new_version:
        raise ZenodoError(
            f"Record {record_id} is published and has no draft of a new version. Pass --new-version to "
            "create one, or create it in the Zenodo UI (New version) first."
        )

    # On the current Zenodo the reply is the new draft; on the old one it was the
    # published deposition. Both name the draft in links.latest_draft. If a draft
    # exists that the search did not find, Zenodo returns it instead of making a
    # second one (or refuses if it holds files).
    url = f"{base_url}/deposit/depositions/{record_id}/actions/newversion"
    response = session.post(url, timeout=60)
    if response.status_code >= 400:
        raise ZenodoError(f"POST {url} failed with {response.status_code}: {response.text}")
    latest_draft = response.json().get("links", {}).get("latest_draft")
    if not latest_draft:
        raise ZenodoError(f"POST {url} returned no links.latest_draft.")
    draft = check_draft_of(fetch_json(session, latest_draft), deposition, "The newversion action's latest_draft")
    print(f"Created the draft {draft['id']} of a new version of record {record_id}.")
    return draft


def web_page(deposition: dict[str, Any]) -> str:
    """The deposition's page on Zenodo's website, as its links.html gives it."""
    return deposition.get("links", {}).get("html") or "(no web page reported)"


def describe_draft(draft: dict[str, Any]) -> str:
    """The draft's record ID, the DOI reserved for it and its web page."""
    reserved = draft.get("metadata", {}).get("prereserve_doi")
    doi = reserved.get("doi") if isinstance(reserved, dict) else reserved
    return f"Draft: record {draft['id']}, reserved DOI {doi or '(none reported)'}, {web_page(draft)}"


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


def file_name(entry: dict[str, Any]) -> str:
    """The name of a file of the deposit API ("filename") or the records API ("key")."""
    return entry.get("filename") or entry.get("key") or ""


def describe_file(entry: dict[str, Any]) -> str:
    return f"'{file_name(entry)}' (md5 {normalize_checksum(entry.get('checksum')) or 'unknown'}, id {entry.get('id')})"


def draft_files(session: requests.Session, base_url: str, draft_id: int) -> list[dict[str, Any]]:
    """The files of the draft, as the deposit API lists them."""
    return fetch_json(session, f"{base_url}/deposit/depositions/{draft_id}/files")


def latest_published(session: requests.Session, base_url: str, deposition: dict[str, Any]) -> dict[str, Any] | None:
    """The latest published version of the deposition's record, or None if no
    version is published; it is the version newversion copies files from.

    The records API's versions/latest of any published version redirects to
    it.  (The concept's own record ID does not always: on zenodo.org it can
    answer 410.)  If the records API fails, the files of the newest published
    version the deposit API knows are taken instead."""
    concept = deposition.get("conceptrecid")
    if not concept:
        return None
    published = {d["id"] for d in concept_depositions(session, base_url, concept) if d.get("state") == "done"}
    if deposition.get("state") == "done":
        published.add(deposition["id"])
    if not published:
        return None
    newest = max(published)
    try:
        return fetch_json(session, f"{base_url}/records/{newest}/versions/latest")
    except (ZenodoError, requests.RequestException) as err:
        print(
            f"The records API gave no latest version ({str(err)[:200]}); "
            f"taking the files of version {newest} from the deposit API instead."
        )
        return {"id": newest, "files": draft_files(session, base_url, newest)}


def refuse_if_published(published: dict[str, Any] | None, filename: str, local_md5: str) -> None:
    """ZenodoError if the published version holds the file `filename` with the local MD5."""
    for entry in (published or {}).get("files") or []:
        if file_name(entry) == filename and normalize_checksum(entry.get("checksum")) == local_md5:
            raise ZenodoError(
                f"This archive is already published as version {published.get('id')} of the record "
                f"('{filename}', md5 {local_md5}); nothing was created, changed or uploaded."
            )


def sort_files(
    files: list[dict[str, Any]],
    published: dict[str, Any] | None,
    filename: str,
    local_md5: str,
) -> tuple[dict[str, Any] | None, list[tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    """Sort the draft's files into (kept, deleted, foreign): the file `filename`
    (the upload's name) with the local MD5, which need not be uploaded again;
    the files to delete with the reason; and the files the script does not
    know, which stop it."""
    inherited = {
        (file_name(entry), normalize_checksum(entry.get("checksum"))) for entry in (published or {}).get("files") or []
    }
    kept = None
    deleted = []
    foreign = []
    for entry in files:
        name, md5 = file_name(entry), normalize_checksum(entry.get("checksum"))
        if name == filename and md5 == local_md5:
            kept = entry
        elif (name, md5) in inherited:
            deleted.append((entry, f"inherited from the published version {published.get('id')}"))
        elif name == filename:
            deleted.append((entry, "an earlier upload to this draft"))
        else:
            foreign.append(entry)
    return kept, deleted, foreign


def delete_file(session: requests.Session, base_url: str, draft_id: int, entry: dict[str, Any], reason: str) -> None:
    delete_url = f"{base_url}/deposit/depositions/{draft_id}/files/{entry.get('id')}"
    response = session.delete(delete_url, timeout=60)
    if response.status_code == 404:
        print(f"{describe_file(entry)} ({reason}) was already gone from the draft.")
        return
    if response.status_code != 204:
        raise ZenodoError(f"DELETE {delete_url} failed with {response.status_code}: {response.text}")
    print(f"Deleted {describe_file(entry)} from the draft: {reason}.")


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
    upload_name: str = UPLOAD_NAME,
) -> dict[str, Any]:
    """PUT `file_path` to the bucket of the deposition `draft` (its
    links.bucket) as the file `upload_name`, streaming it, and return
    Zenodo's JSON for the file."""
    bucket = draft.get("links", {}).get("bucket")
    if not bucket:
        raise ZenodoError("The draft has no links.bucket to upload to.")
    target_url = f"{bucket}/{quote(upload_name)}"
    file_size = file_path.stat().st_size
    print(
        f"Uploading {file_path} ({file_size:,} bytes) as {upload_name} to record {draft['id']}...",
        flush=True,
    )
    with file_path.open("rb") as fh:
        progress = ProgressFile(fh, file_size, label=upload_name)
        response = session.put(
            target_url,
            data=progress,
            headers={"Content-Type": "application/octet-stream"},
            timeout=(60, 900),
        )
    if response.status_code >= 400:
        raise ZenodoError(f"PUT {target_url} failed with {response.status_code}: {response.text}")
    return response.json()


def reported_checksum(uploaded: dict[str, Any], files: list[dict[str, Any]], filename: str) -> str:
    """The MD5 that Zenodo reports for the uploaded file: the checksum in the
    reply to the PUT or, if the reply has none, the one in the draft's file
    list."""
    checksum = normalize_checksum(uploaded.get("checksum"))
    if checksum:
        return checksum
    for entry in files:
        if file_name(entry) == filename:
            return normalize_checksum(entry.get("checksum"))
    return ""


def single_file_problem(files: list[dict[str, Any]], filename: str, local_md5: str, holder: str) -> str | None:
    """None if `files`, those of `holder`, are exactly one file `filename`
    with the local MD5, else what they are instead."""
    if (
        len(files) == 1
        and file_name(files[0]) == filename
        and normalize_checksum(files[0].get("checksum")) == local_md5
    ):
        return None
    listed = "; ".join(describe_file(entry) for entry in files) or "none"
    return (
        f"{holder} holds {len(files)} files ({listed}), but fm-tools accepts a record with exactly one, "
        f"here '{filename}' with md5 {local_md5}"
    )


class PublishOutcomeUnknown(ZenodoError):
    """The publish request failed in a way that does not tell whether Zenodo
    published the draft."""


def publish(session: requests.Session, base_url: str, draft_id: int) -> dict[str, Any]:
    """Publish the draft with the deposit API's publish action; Zenodo's reply
    is the published record.  A refusal (4xx) raises ZenodoError; a server
    error or a lost connection, after which the draft may have been published,
    PublishOutcomeUnknown."""
    url = f"{base_url}/deposit/depositions/{draft_id}/actions/publish"
    try:
        response = session.post(url, timeout=300)
        record = response.json() if response.status_code < 400 else None
    except requests.RequestException as err:
        raise PublishOutcomeUnknown(f"POST {url} failed: {err}") from None
    if response.status_code >= 500:
        raise PublishOutcomeUnknown(f"POST {url} failed with {response.status_code}: {response.text}")
    if record is None:
        raise ZenodoError(f"POST {url} failed with {response.status_code}: {response.text}")
    return record


def published_files(session: requests.Session, base_url: str, record_id: int) -> list[dict[str, Any]]:
    """The files of the published record as the records API shows them,
    asked up to three times."""
    for attempt in range(3):
        try:
            return fetch_json(session, f"{base_url}/records/{record_id}").get("files") or []
        except (ZenodoError, requests.RequestException):
            if attempt == 2:
                raise
            time.sleep(2)
    return []  # pragma: no cover - not reached


def archive_version(archive_path: Path) -> str | None:
    """The line of cooperace/VERSION in the archive, or None if the archive is
    missing or has no such entry."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return archive.read("cooperace/VERSION").decode().strip() or None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def publish_command(draft_id: int, args: argparse.Namespace) -> str:
    """The command that publishes the draft, as the second of the two runs."""
    command = ["python", sys.argv[0], "--record-id", str(draft_id), "--publish", "--file", str(args.file)]
    if args.upload_name != UPLOAD_NAME:
        command += ["--upload-name", args.upload_name]
    if args.sandbox:
        command.append("--sandbox")
    return shlex.join(command)


def main() -> None:
    args = parse_args()
    if not args.token:
        print("--token not provided and ZENODO_TOKEN is unset.", file=sys.stderr)
        sys.exit(2)

    archive_path: Path = args.file
    upload_name: str = args.upload_name
    if args.metadata_only:
        if args.no_metadata:
            print("--metadata-only and --no-metadata contradict each other.", file=sys.stderr)
            sys.exit(2)
        if args.publish:
            print("--publish needs the upload and its checks, so not --metadata-only.", file=sys.stderr)
            sys.exit(2)
    elif not archive_path.exists():
        print(f"Archive '{archive_path}' does not exist. Run make svcomp first?", file=sys.stderr)
        sys.exit(2)

    version = args.version or archive_version(archive_path)
    local_md5 = "" if args.metadata_only else md5_of(archive_path)
    archive = None if args.metadata_only else (upload_name, local_md5)
    base_url = SANDBOX_API if args.sandbox else PRODUCTION_API
    session = zenodo_session(args.token)

    try:
        draft = ensure_draft(session, base_url, int(args.record_id), args.new_version, archive)
        print(describe_draft(draft), flush=True)
        if args.metadata_only:
            if not args.no_metadata:
                update_metadata(session, base_url, draft, args.metadata, version)
            return
        published = latest_published(session, base_url, draft)
        refuse_if_published(published, upload_name, local_md5)
        files = draft_files(session, base_url, draft["id"])
        kept, deleted, foreign = sort_files(files, published, upload_name, local_md5)
        if foreign:
            raise ZenodoError(
                f"The draft {draft['id']} holds files that are not from the published version and not named "
                f"'{upload_name}': {'; '.join(describe_file(entry) for entry in foreign)}. "
                "The draft is left as it was and nothing was uploaded; delete them from the draft and run again."
            )
        if kept:
            print(
                f"The draft already holds {describe_file(kept)}, the local file; it is not uploaded again "
                "and the draft's metadata is left as it is."
            )
        elif not args.no_metadata:
            update_metadata(session, base_url, draft, args.metadata, version)
        for entry, reason in deleted:
            delete_file(session, base_url, draft["id"], entry, reason)
        uploaded = {} if kept else upload_file(session, draft, archive_path, upload_name)
        files = draft_files(session, base_url, draft["id"])
    except (ZenodoError, requests.RequestException) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    remote_md5 = reported_checksum(uploaded, files, upload_name)
    if remote_md5 != local_md5:
        print(
            f"Error: checksum mismatch for {upload_name}: local md5 {local_md5} ({archive_path}), "
            f"Zenodo reports {remote_md5 or 'none'}. The file in the draft is not the local file; "
            "upload it again before publishing.",
            file=sys.stderr,
        )
        sys.exit(1)
    problem = single_file_problem(files, upload_name, local_md5, "the draft")
    if problem:
        print(f"Error: {problem}. The draft is not published.", file=sys.stderr)
        sys.exit(1)

    [entry] = files
    size = entry.get("filesize")
    print(
        "Already uploaded." if kept else "Upload complete!",
        f"record_id={draft['id']}",
        f"file_id={entry.get('id')}",
        f"size={int(size) if isinstance(size, (int, float)) else size}",
        f"checksum=md5:{remote_md5} (matches the local file; the only file of the draft)",
    )
    if not args.publish:
        print(f"Not published. Check the draft at {web_page(draft)}, then publish it with:")
        print(f"    {publish_command(draft['id'], args)}")
        return

    try:
        record = publish(session, base_url, draft["id"])
    except PublishOutcomeUnknown as err:
        print(
            f"Error: {err}\nWhether Zenodo published the draft is unknown. Check {web_page(draft)} and the "
            "record's versions on Zenodo before running again (the script refuses an archive that is already "
            "published).",
            file=sys.stderr,
        )
        sys.exit(1)
    except ZenodoError as err:
        print(f"Error: {err}\nZenodo did not publish the draft.", file=sys.stderr)
        sys.exit(1)
    print(f"Published record {record.get('id')}: DOI {record.get('doi')}, {web_page(record)}")

    try:
        files = published_files(session, base_url, record.get("id") or draft["id"])
    except (ZenodoError, requests.RequestException) as err:
        print(f"Error: the published record's files could not be checked: {err}", file=sys.stderr)
        sys.exit(1)
    problem = single_file_problem(files, upload_name, local_md5, f"the published record {record.get('id')}")
    if problem:
        print(f"Error: {problem}.", file=sys.stderr)
        sys.exit(1)
    print(
        f"Checked: the published record {record.get('id')} holds one file, {upload_name}, md5 {local_md5} "
        f"(the local file). DOI {record.get('doi')}."
    )


if __name__ == "__main__":
    main()
