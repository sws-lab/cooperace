"""scripts/sv-comp/upload-zenodo.py against a stub of Zenodo's deposit API on
127.0.0.1: the archive goes in one PUT to the draft's bucket, as the bare
bytes of the file with a Content-Length, and the script exits 1 unless the
checksum Zenodo returns is the MD5 of the local file. Skipped without the
`requests` package, which the script needs."""
import hashlib
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("requests")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sv-comp" / "upload-zenodo.py"
spec = importlib.util.spec_from_file_location("upload_zenodo", SCRIPT)
uz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uz)

CONTENT = bytes(range(256)) * 1200  # 307200 bytes


class StubZenodo:
    """Serves one draft deposition (id 1). `checksum_in_reply` is what the
    reply to the PUT reports: "md5:<hex>" of the body received if "right",
    a wrong digest if "wrong", nothing if "none" (the file list then has the
    right digest). `puts` collects (path, headers, body) of every PUT."""

    def __init__(self, checksum_in_reply="right"):
        self.checksum_in_reply = checksum_in_reply
        self.puts = []
        self.digest = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 10

            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/deposit/depositions/1":
                    self.reply(200, {"id": 1, "state": "draft", "files": [],
                                     "links": {"bucket": f"{stub.url}/bucket/b1"}})
                elif self.path == "/api/deposit/depositions/1/files":
                    self.reply(200, [{"filename": name, "checksum": stub.digest} for name in ["cooperace.zip"]])
                else:
                    self.reply(404, {})

            def do_PUT(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                stub.puts.append((self.path, dict(self.headers), body))
                stub.digest = hashlib.md5(body).hexdigest()
                reply = {"key": self.path.rsplit("/", 1)[1], "size": length}
                if stub.checksum_in_reply == "right":
                    reply["checksum"] = "md5:" + stub.digest
                elif stub.checksum_in_reply == "wrong":
                    reply["checksum"] = "md5:" + "0" * 32
                self.reply(201, reply)

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


def run_upload(monkeypatch, tmp_path, stub):
    archive = tmp_path / "cooperace.zip"
    archive.write_bytes(CONTENT)
    monkeypatch.setattr(uz, "PRODUCTION_API", stub.url + "/api")
    monkeypatch.setattr(sys, "argv", ["upload-zenodo.py", "--record-id", "1", "--token", "t", "--file", str(archive)])
    uz.main()


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
    assert "checksum mismatch" in err and hashlib.md5(CONTENT).hexdigest() in err


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
