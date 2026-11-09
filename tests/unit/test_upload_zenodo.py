# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""scripts/sv-comp/upload-zenodo.py against a stub of Zenodo's deposit API on
127.0.0.1: the archive goes in one PUT to the draft's bucket, as the bare
bytes of the file with a Content-Length, named cooperace.zip whatever the
local file is called, and the script exits 1 unless the checksum Zenodo
returns is the MD5 of the local file and the draft holds that file only. A
published record gets a draft of a new version only with --new-version, the
files that draft inherits are deleted, and the draft is published only with
--publish. Skipped without the `requests` package, which the script needs."""

import hashlib
import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("requests")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sv-comp" / "upload-zenodo.py"
spec = importlib.util.spec_from_file_location("upload_zenodo", SCRIPT)
uz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uz)

CONTENT = bytes(range(256)) * 1200  # 307200 bytes
OLD = b"the archive of the published version"


def md5(content):
    return hashlib.md5(content).hexdigest()


def dep(state, concept=0, files=None, submitted=None):
    """A deposition of the stub: "done" is published, "unsubmitted" a draft,
    "inprogress" with submitted=True a published version opened for editing.
    `files` maps a file name to its content."""
    return {
        "state": state,
        "submitted": state == "done" if submitted is None else submitted,
        "concept": concept,
        "files": {name: md5(content) for name, content in (files or {}).items()},
    }


class StubZenodo:
    """Serves depositions as the current Zenodo does (checked on
    sandbox.zenodo.org): links.latest_draft of a published deposition names
    the deposition itself, newversion replies with the new draft, which holds
    copies of the files of the latest published version of the concept, and
    for a concept that has a draft it returns that draft (refusing if the
    draft holds files) instead of making a second one. The records API's
    versions/latest of a version redirects to the latest published version.

    `depositions` maps an ID to a dep(...); by default deposition 1 is an
    empty draft. `checksum_in_reply` is what the reply to a file PUT reports:
    "md5:<hex>" of the body received if "right", a wrong digest if "wrong",
    nothing if "none" (the file list then has the right digest). `index` maps
    an ID to the state that a stale search index shows for it. `latest_draft`
    maps the ID of a published deposition to the deposition its
    links.latest_draft names, as on the old Zenodo. `quirks`:
      ignore_deletes: a DELETE of a file answers 204 and keeps the file;
      gone_on_delete: a DELETE of a file deletes it and answers 404;
      records_api_down: the records API answers 500;
      newversion_fails: newversion answers 500;
      newversion_other_concept: newversion makes a draft of concept 99;
      publish_500: publish publishes the draft, then answers 500;
      publish_400: publish answers 400 and publishes nothing;
      publish_adds_file: publish adds extra.txt to the record.
    `puts` collects (path, headers, body) of every PUT, `calls` (method, path)
    of every request."""

    def __init__(self, checksum_in_reply="right", depositions=None, index=None, latest_draft=None, quirks=()):
        self.checksum_in_reply = checksum_in_reply
        self.deps = depositions or {1: dep("unsubmitted")}
        self.index = index or {}
        self.latest_draft = latest_draft or {}
        self.quirks = set(quirks)
        self.puts = []
        self.calls = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10

            def log_message(self, *args):
                pass

            def reply(self, status, body=None, location=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def handle_one(self, method):
                stub.calls.append((method, self.path))
                with stub.lock:
                    status, *rest = stub.route(method, urlsplit(self.path), self.headers, self.body())
                self.reply(status, *rest)

            def do_GET(self):
                self.handle_one("GET")

            def do_PUT(self):
                self.handle_one("PUT")

            def do_POST(self):
                self.handle_one("POST")

            def do_DELETE(self):
                self.handle_one("DELETE")

        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    @staticmethod
    def file_id(name, digest):
        # Zenodo keeps the file's ID when newversion copies it.
        return f"{name}-{digest[:8]}"

    def files_json(self, dep_id):
        return [
            {"id": self.file_id(name, digest), "filename": name, "filesize": 1.0, "checksum": digest}
            for name, digest in self.deps[dep_id]["files"].items()
        ]

    def deposition_json(self, dep_id):
        d = self.deps[dep_id]
        published = d["state"] == "done"
        body = {
            "id": dep_id,
            "state": d["state"],
            "submitted": d["submitted"],
            "conceptrecid": str(d["concept"]),
            "metadata": {"prereserve_doi": {"doi": f"10.5281/zenodo.{dep_id}", "recid": dep_id}},
            "files": self.files_json(dep_id),
            "links": {
                "bucket": f"{self.url}/bucket/b{dep_id}",
                "html": f"{self.url}/records/{dep_id}" if published else f"{self.url}/deposit/{dep_id}",
                "latest_draft": f"{self.url}/api/deposit/depositions/{self.latest_draft.get(dep_id, dep_id)}",
            },
        }
        if published:
            body["doi"] = f"10.5072/zenodo.{dep_id}"
        return body

    def latest_published(self, concept):
        ids = [i for i, d in self.deps.items() if d["concept"] == concept and d["state"] == "done"]
        return max(ids) if ids else None

    def new_drafts(self, concept):
        return [i for i, d in self.deps.items() if d["concept"] == concept and not d["submitted"]]

    def route(self, method, url, headers, body):
        path = url.path
        if method == "PUT" and (m := re.fullmatch(r"/bucket/b(\d+)/(.+)", path)):
            self.puts.append((path, dict(headers), body))
            self.deps[int(m[1])]["files"][m[2]] = md5(body)
            reply = {"key": m[2], "size": len(body)}
            if self.checksum_in_reply == "right":
                reply["checksum"] = "md5:" + md5(body)
            elif self.checksum_in_reply == "wrong":
                reply["checksum"] = "md5:" + "0" * 32
            return 201, reply
        if path == "/api/deposit/depositions" and method == "GET":
            [query] = parse_qs(url.query)["q"]
            concept = int(query.removeprefix("conceptrecid:"))
            found = [self.deposition_json(i) for i, d in self.deps.items() if d["concept"] == concept]
            for entry in found:
                if entry["id"] in self.index:
                    entry["state"] = self.index[entry["id"]]
                    entry["submitted"] = entry["state"] == "done"
            return 200, found
        if path.startswith("/api/records/") and "records_api_down" in self.quirks:
            return 500, {"status": 500, "message": "Internal server error"}
        if m := re.fullmatch(r"/api/records/(\d+)/versions/latest", path):
            latest = self.latest_published(self.deps[int(m[1])]["concept"])
            return 301, None, f"/api/records/{latest}"
        if m := re.fullmatch(r"/api/records/(\d+)", path):
            n = int(m[1])
            if n not in self.deps or self.deps[n]["state"] != "done":
                return 404, {"status": 404, "message": "The persistent identifier is not registered."}
            files = [
                {"id": self.file_id(name, digest), "key": name, "checksum": "md5:" + digest}
                for name, digest in self.deps[n]["files"].items()
            ]
            return 200, {"id": n, "conceptrecid": str(self.deps[n]["concept"]), "files": files}
        m = re.fullmatch(r"/api/deposit/depositions/(\d+)(/.*)?", path)
        if not m or int(m[1]) not in self.deps:
            return 404, {}
        dep_id, rest, d = int(m[1]), m[2] or "", self.deps[int(m[1])]
        if method == "GET" and rest == "":
            return 200, self.deposition_json(dep_id)
        if method == "GET" and rest == "/files":
            return 200, self.files_json(dep_id)
        if method == "PUT" and rest == "":
            self.puts.append((path, dict(headers), body))
            return 200, self.deposition_json(dep_id)
        if method == "DELETE" and (f := re.fullmatch(r"/files/(.+)", rest)):
            names = [n for n, digest in d["files"].items() if self.file_id(n, digest) == f[1]]
            if not names:
                return 404, {}
            if "ignore_deletes" not in self.quirks:
                del d["files"][names[0]]
            return (404, {}) if "gone_on_delete" in self.quirks else (204, None)
        if method == "POST" and rest == "/actions/newversion":
            if "newversion_fails" in self.quirks:
                return 500, {"status": 500, "message": "Internal server error"}
            concept = d["concept"]
            latest = self.latest_published(concept)
            files = dict(self.deps[latest]["files"])
            drafts = self.new_drafts(concept)
            if drafts:
                if self.deps[drafts[0]]["files"]:
                    return 400, {"status": 400, "errors": [{"field": "files.enabled"}]}
                self.deps[drafts[0]]["files"] = files
                return 201, self.deposition_json(drafts[0])
            new_id = max(self.deps) + 1
            if "newversion_other_concept" in self.quirks:
                concept = 99
            self.deps[new_id] = {"state": "unsubmitted", "submitted": False, "concept": concept, "files": files}
            return 201, self.deposition_json(new_id)
        if method == "POST" and rest == "/actions/publish":
            if "publish_400" in self.quirks:
                return 400, {"status": 400, "message": "A validation error occurred."}
            d["state"], d["submitted"] = "done", True
            if "publish_adds_file" in self.quirks:
                d["files"]["extra.txt"] = md5(b"extra")
            if "publish_500" in self.quirks:
                return 500, {"status": 500, "message": "Internal server error"}
            return 202, self.deposition_json(dep_id)
        return 404, {}


def run_main(monkeypatch, stub, *argv):
    monkeypatch.setattr(uz, "PRODUCTION_API", stub.url + "/api")
    monkeypatch.setattr(sys, "argv", ["upload-zenodo.py", "--token", "t", *argv])
    uz.main()


def run_upload(monkeypatch, tmp_path, stub, *extra, record_id="1", local_name="cooperace.zip"):
    archive = tmp_path / local_name
    archive.write_bytes(CONTENT)
    run_main(monkeypatch, stub, "--record-id", record_id, "--file", str(archive), "--no-metadata", *extra)


def test_upload_is_one_streamed_put_of_the_bare_file(tmp_path, monkeypatch, capsys):
    with StubZenodo() as stub:
        run_upload(monkeypatch, tmp_path, stub)
    [(path, headers, body)] = stub.puts
    assert path == "/bucket/b1/cooperace.zip"
    assert body == CONTENT
    assert headers["Content-Length"] == str(len(CONTENT))
    assert "multipart" not in headers.get("Content-Type", "")
    out = capsys.readouterr().out
    assert f"({len(CONTENT):,}/{len(CONTENT):,} bytes)" in out
    assert "Upload complete!" in out


def test_checksum_mismatch_exits_1(tmp_path, monkeypatch, capsys):
    with StubZenodo("wrong") as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub)
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "checksum mismatch" in err and md5(CONTENT) in err


def test_checksum_from_the_file_list_when_the_reply_has_none(tmp_path, monkeypatch, capsys):
    with StubZenodo("none") as stub:
        run_upload(monkeypatch, tmp_path, stub)
    assert "Upload complete!" in capsys.readouterr().out


def test_progress_counts_the_bytes_read_for_sending(tmp_path, capsys):
    path = tmp_path / "f"
    path.write_bytes(CONTENT)
    with path.open("rb") as fh:
        progress = uz.ProgressFile(fh, len(CONTENT), report_every=100_000, label="f")
        assert len(progress) == len(CONTENT)
        assert b"".join(progress) == CONTENT
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].endswith(f"({len(CONTENT):,}/{len(CONTENT):,} bytes)")
    assert len(lines) >= 3


def test_normalize_checksum():
    assert uz.normalize_checksum("md5:ABCdef") == "abcdef"
    assert uz.normalize_checksum("abcdef") == "abcdef"
    assert uz.normalize_checksum(None) == ""


def test_metadata_only_puts_the_metadata_and_needs_no_archive(tmp_path, monkeypatch, capsys):
    with StubZenodo() as stub:
        run_main(monkeypatch, stub, "--record-id", "1", "--file", str(tmp_path / "missing.zip"), "--metadata-only")
    [(path, headers, body)] = stub.puts
    assert path == "/api/deposit/depositions/1"
    assert json.loads(body)["metadata"]["version"] == "svcomp27"
    assert "Updated metadata of record 1" in capsys.readouterr().out


def test_version_is_the_archives_version_file_unless_given(tmp_path, monkeypatch):
    import zipfile

    archive = tmp_path / "cooperace.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cooperace/VERSION", "svcomp27 745cd85\n")
    assert uz.archive_version(archive) == "svcomp27 745cd85"
    assert uz.archive_version(tmp_path / "missing.zip") is None

    for extra, want in [([], "svcomp27 745cd85"), (["--version", "x1"], "x1")]:
        with StubZenodo() as stub:
            run_main(monkeypatch, stub, "--record-id", "1", "--file", str(archive), "--metadata-only", *extra)
        [(path, headers, body)] = stub.puts
        assert json.loads(body)["metadata"]["version"] == want


def published_record():
    """Record 10 of concept 9, published with OLD as cooperace.zip."""
    return {10: dep("done", 9, {"cooperace.zip": OLD})}


def calls(stub, method, suffix):
    return [path for m, path in stub.calls if m == method and path.endswith(suffix)]


def test_new_version_creates_a_draft_and_deletes_the_inherited_file(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record()) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert calls(stub, "POST", "/actions/newversion") == ["/api/deposit/depositions/10/actions/newversion"]
    assert sorted(stub.deps) == [10, 11]
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}
    assert stub.deps[10]["files"] == {"cooperace.zip": md5(OLD)}
    assert [path for path, _, _ in stub.puts] == ["/bucket/b11/cooperace.zip"]
    assert not calls(stub, "POST", "/actions/publish")
    out = capsys.readouterr().out
    assert "Created the draft 11 of a new version of record 10." in out
    assert f"Draft: record 11, reserved DOI 10.5281/zenodo.11, {stub.url}/deposit/11" in out
    assert f"Deleted 'cooperace.zip' (md5 {md5(OLD)}" in out and "inherited from the published version 10" in out
    assert "Upload complete!" in out and "Not published." in out
    # The next step names the draft.
    assert f"upload-zenodo.py --record-id 11 --publish --file {tmp_path / 'cooperace.zip'}" in out


def test_an_inherited_file_of_another_name_is_deleted_too(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions={10: dep("done", 9, {"E8-default.zip": OLD})}) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}
    assert "Deleted 'E8-default.zip'" in capsys.readouterr().out


def test_the_archive_is_uploaded_as_cooperace_zip_whatever_its_local_name(tmp_path, monkeypatch, capsys):
    local_name = "E8-svcomp27-default-42d6c9a.zip"
    with StubZenodo(depositions=published_record()) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10", local_name=local_name)
        # A second run finds the archive already there under that name.
        run_upload(monkeypatch, tmp_path, stub, "--publish", record_id="10", local_name=local_name)
    assert [path for path, _, _ in stub.puts] == ["/bucket/b11/cooperace.zip"]
    assert stub.deps[11] == {"state": "done", "submitted": True, "concept": 9, "files": {"cooperace.zip": md5(CONTENT)}}
    out = capsys.readouterr().out
    assert "as cooperace.zip to record 11" in out and local_name in out
    assert "inherited from the published version 10" in out and "Already uploaded." in out


def test_upload_name_overrides_the_name_on_zenodo(tmp_path, monkeypatch, capsys):
    with StubZenodo() as stub:
        run_upload(monkeypatch, tmp_path, stub, "--upload-name", "other.zip", local_name="E8.zip")
    assert stub.deps[1]["files"] == {"other.zip": md5(CONTENT)}


def test_a_published_record_without_a_draft_needs_new_version(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record()) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, record_id="10")
    assert stop.value.code == 1
    assert "--new-version" in capsys.readouterr().err
    assert not calls(stub, "POST", "/actions/newversion") and not stub.puts
    assert sorted(stub.deps) == [10]


def test_an_existing_draft_is_reused_and_a_second_run_uploads_nothing(tmp_path, monkeypatch, capsys):
    # Draft 11 was made in the UI without the previous version's files; the
    # published record's links.latest_draft does not name it.
    with StubZenodo(depositions=published_record() | {11: dep("unsubmitted", 9)}) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
        out = capsys.readouterr().out
        assert "Using the existing draft 11 of a new version of record 10." in out
        assert "Upload complete!" in out
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
        out = capsys.readouterr().out
    assert not calls(stub, "POST", "/actions/newversion")
    assert sorted(stub.deps) == [10, 11]
    assert [path for path, _, _ in stub.puts] == ["/bucket/b11/cooperace.zip"]
    assert "Using the existing draft 11" in out
    assert "it is not uploaded again" in out and "Already uploaded." in out


def test_a_new_version_draft_is_created_once(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record()) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert len(calls(stub, "POST", "/actions/newversion")) == 1
    assert sorted(stub.deps) == [10, 11]
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}


def test_an_earlier_upload_of_the_same_name_is_replaced(tmp_path, monkeypatch, capsys):
    deps = published_record() | {11: dep("unsubmitted", 9, {"cooperace.zip": b"an earlier build"})}
    with StubZenodo(depositions=deps) as stub:
        run_upload(monkeypatch, tmp_path, stub, record_id="11")
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}
    assert "an earlier upload to this draft" in capsys.readouterr().out


def test_a_foreign_file_stops_the_script_before_any_change(tmp_path, monkeypatch, capsys):
    files = {"cooperace.zip": OLD, "notes.txt": b"notes"}
    with StubZenodo(depositions=published_record() | {11: dep("unsubmitted", 9, files)}) as stub:
        with pytest.raises(SystemExit) as stop:
            archive = tmp_path / "cooperace.zip"
            archive.write_bytes(CONTENT)
            run_main(monkeypatch, stub, "--record-id", "10", "--file", str(archive), "--publish")
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "'notes.txt'" in err and "nothing was uploaded" in err
    assert f"md5 {md5(OLD)}" not in err
    assert not stub.puts and not calls(stub, "DELETE", "") and not calls(stub, "POST", "")
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(OLD), "notes.txt": md5(b"notes")}


def test_the_draft_must_end_with_one_file(tmp_path, monkeypatch, capsys):
    # The DELETE of the inherited file of another name does not take effect.
    with StubZenodo(depositions={10: dep("done", 9, {"old.zip": OLD})}, quirks={"ignore_deletes"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert "the draft holds 2 files" in err and "'old.zip'" in err and "not published" in err
    assert not calls(stub, "POST", "/actions/publish")
    assert stub.deps[11]["state"] == "unsubmitted"
    assert uz.single_file_problem([], "cooperace.zip", md5(CONTENT), "the draft").startswith("the draft holds 0 files")


def test_publish_after_the_checks_prints_the_doi(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record()) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert calls(stub, "POST", "/actions/publish") == ["/api/deposit/depositions/11/actions/publish"]
    assert stub.deps[11]["state"] == "done"
    out = capsys.readouterr().out
    assert f"Published record 11: DOI 10.5072/zenodo.11, {stub.url}/records/11" in out
    assert f"Checked: the published record 11 holds one file, cooperace.zip, md5 {md5(CONTENT)}" in out
    assert "Not published." not in out


def test_the_published_record_is_checked_for_one_file(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"publish_adds_file"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert stop.value.code == 1
    assert "the published record 11 holds 2 files" in capsys.readouterr().err


def test_the_second_run_names_the_draft_and_leaves_its_metadata(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "E8.zip"
    archive.write_bytes(CONTENT)
    with StubZenodo(depositions=published_record()) as stub:
        run_main(monkeypatch, stub, "--record-id", "10", "--new-version", "--file", str(archive))
        assert [path for path, _, _ in stub.puts] == ["/api/deposit/depositions/11", "/bucket/b11/cooperace.zip"]
        run_main(monkeypatch, stub, "--record-id", "11", "--publish", "--file", str(archive))
        # The metadata is not put again, nor the file, and no search is needed
        # to find the draft.
        assert len(stub.puts) == 2
        assert stub.deps[11]["state"] == "done"
        out = capsys.readouterr().out
        assert "metadata is left as it is" in out and "Using the existing draft" not in out
    with StubZenodo(depositions=published_record() | {11: dep("unsubmitted", 9, {"cooperace.zip": CONTENT})}) as stub:
        run_main(monkeypatch, stub, "--record-id", "11", "--file", str(archive), "--metadata-only")
    assert [path for path, _, _ in stub.puts] == ["/api/deposit/depositions/11"]


def test_an_archive_already_published_is_refused(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions={10: dep("done", 9, {"cooperace.zip": CONTENT})}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert stop.value.code == 1
    assert "already published as version 10" in capsys.readouterr().err
    assert sorted(stub.deps) == [10] and not calls(stub, "POST", "") and not stub.puts
    # A draft made in the UI holds a copy of the published file: left alone.
    deps = {10: dep("done", 9, {"cooperace.zip": CONTENT}), 11: dep("unsubmitted", 9, {"cooperace.zip": CONTENT})}
    with StubZenodo(depositions=deps) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, "--publish", record_id="11")
    assert "already published as version 10" in capsys.readouterr().err
    assert not calls(stub, "POST", "") and not calls(stub, "DELETE", "") and not stub.puts


def test_a_publish_whose_outcome_is_unknown_is_not_repeated(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"publish_500"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
        assert stop.value.code == 1
        err = capsys.readouterr().err
        assert "Whether Zenodo published the draft is unknown" in err and f"{stub.url}/deposit/11" in err
        assert stub.deps[11]["state"] == "done"
        for record_id, extra in [("11", ()), ("10", ("--new-version",))]:
            with pytest.raises(SystemExit) as stop:
                run_upload(monkeypatch, tmp_path, stub, "--publish", *extra, record_id=record_id)
            assert stop.value.code == 1
            assert "already published as version 11" in capsys.readouterr().err
    assert sorted(stub.deps) == [10, 11]
    assert len(calls(stub, "POST", "/actions/publish")) == 1


def test_a_refused_publish_says_so(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"publish_400"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert stop.value.code == 1
    assert "Zenodo did not publish the draft." in capsys.readouterr().err
    assert stub.deps[11]["state"] == "unsubmitted"


def test_a_stale_search_index_is_caught(tmp_path, monkeypatch, capsys):
    # Version 11 was just published, but the search still shows it as a draft.
    deps = published_record() | {11: dep("done", 9, {"cooperace.zip": b"v2"})}
    with StubZenodo(depositions=deps, index={11: "unsubmitted"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
        assert stop.value.code == 1
        err = capsys.readouterr().err
        assert "deposition 11, which is not an unpublished draft" in err and "search index is stale" in err
        assert not stub.puts and not calls(stub, "DELETE", "") and not calls(stub, "POST", "")
        # The record named by --record-id is not taken for its own draft.
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="11")
    assert "Created the draft 12 of a new version of record 11." in capsys.readouterr().out
    assert stub.deps[12]["files"] == {"cooperace.zip": md5(CONTENT)}


def test_a_draft_named_by_latest_draft_is_used_if_it_belongs_to_the_record(tmp_path, monkeypatch, capsys):
    deps = published_record() | {11: dep("unsubmitted", 9), 20: dep("unsubmitted", 8)}
    with StubZenodo(depositions=deps, latest_draft={10: 11}) as stub:
        run_upload(monkeypatch, tmp_path, stub, record_id="10")
    assert "Using the existing draft 11" in capsys.readouterr().out
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}
    with StubZenodo(depositions=deps, latest_draft={10: 20}) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, record_id="10")
    assert stop.value.code == 1
    assert "names deposition 20 of the concept 8" in capsys.readouterr().err
    assert not stub.puts


def test_a_newversion_draft_of_another_concept_is_refused(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"newversion_other_concept"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert stop.value.code == 1
    assert "names deposition 11 of the concept 99" in capsys.readouterr().err
    assert not stub.puts


def test_a_failed_newversion_is_reported(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"newversion_fails"}) as stub:
        with pytest.raises(SystemExit) as stop:
            run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert stop.value.code == 1
    assert "newversion failed with 500" in capsys.readouterr().err
    assert sorted(stub.deps) == [10]


def test_several_drafts_are_refused(tmp_path, monkeypatch, capsys):
    deps = published_record() | {11: dep("unsubmitted", 9), 12: dep("unsubmitted", 9)}
    with StubZenodo(depositions=deps) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert stop.value.code == 1
    assert "several drafts of a new version (11, 12)" in capsys.readouterr().err
    assert not stub.puts


def test_a_deposition_without_submitted_false_is_not_a_draft(tmp_path, monkeypatch, capsys):
    deps = {1: {"state": "unsubmitted", "submitted": None, "concept": 0, "files": {}}}
    with StubZenodo(depositions=deps) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub)
    assert stop.value.code == 1
    assert "submitted None" in capsys.readouterr().err
    assert not stub.puts


def test_a_file_already_gone_is_not_reported_deleted(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"gone_on_delete"}) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    out = capsys.readouterr().out
    assert "was already gone from the draft" in out and "Deleted" not in out
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}


def test_the_deposit_api_stands_in_for_the_records_api(tmp_path, monkeypatch, capsys):
    with StubZenodo(depositions=published_record(), quirks={"records_api_down"}) as stub:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    out = capsys.readouterr().out
    assert "taking the files of version 10 from the deposit API" in out
    assert "inherited from the published version 10" in out
    assert stub.deps[11]["files"] == {"cooperace.zip": md5(CONTENT)}


def test_no_publish_after_a_checksum_mismatch(tmp_path, monkeypatch, capsys):
    with StubZenodo("wrong", depositions=published_record()) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", "--publish", record_id="10")
    assert stop.value.code == 1
    assert not calls(stub, "POST", "/actions/publish")


def test_publish_is_refused_with_metadata_only(tmp_path, monkeypatch, capsys):
    with StubZenodo() as stub, pytest.raises(SystemExit) as stop:
        run_main(monkeypatch, stub, "--record-id", "1", "--metadata-only", "--publish")
    assert stop.value.code == 2
    assert not stub.calls


def test_a_published_version_opened_for_editing_is_refused(tmp_path, monkeypatch, capsys):
    deps = {10: dep("inprogress", 9, {"cooperace.zip": OLD}, submitted=True)}
    with StubZenodo(depositions=deps) as stub, pytest.raises(SystemExit) as stop:
        run_upload(monkeypatch, tmp_path, stub, "--new-version", record_id="10")
    assert stop.value.code == 1
    assert "opened for editing" in capsys.readouterr().err
    assert not stub.puts and not calls(stub, "DELETE", "") and not calls(stub, "POST", "")
