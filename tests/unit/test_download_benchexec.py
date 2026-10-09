"""scripts/download-benchexec.py: the requirements file it hands to pip, what it
does with a wheel already in lib/, and the .license file. pip and PyPI are
replaced by stubs."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download-benchexec.py"
spec = importlib.util.spec_from_file_location("download_benchexec", SCRIPT)
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


def make_wheel(path, licence="Apache text"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("benchexec/__init__.py", "")
        if licence:
            zf.writestr("benchexec-9.9.dist-info/licenses/LICENSES/Apache-2.0.txt", licence)
        zf.writestr("benchexec-9.9.dist-info/METADATA", "Name: benchexec\nLicense: Apache-2.0\n")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def fake_pip(tmp_path, monkeypatch):
    """Replaces pip_download by a function that writes a wheel for the pinned
    version and records the requirements text it was given."""
    calls = []

    def download(requirements, directory):
        calls.append(requirements.read_text())
        make_wheel(directory / db.wheel_name("9.9"))

    monkeypatch.setattr(db, "pip_download", download)
    wheel = tmp_path / "source.whl"
    sha256 = make_wheel(wheel)
    return calls, sha256


def test_default_version_is_pinned_with_a_hash():
    assert db.BENCHEXEC_VERSION == "3.35"
    assert len(db.BENCHEXEC_SHA256) == 64
    assert db.requirements_text("3.35", "ab" * 32) == f"benchexec==3.35 --hash=sha256:{'ab' * 32}\n"


def test_pypi_wheel_sha256(monkeypatch):
    listing = {"urls": [
        {"filename": "benchexec-9.9.tar.gz", "digests": {"sha256": "sdist"}},
        {"filename": "benchexec-9.9-py3-none-any.whl", "digests": {"sha256": "wheel"}},
    ]}
    monkeypatch.setattr(db.urllib.request, "urlopen", lambda url, timeout: io.StringIO(json.dumps(listing)))
    assert db.pypi_wheel_sha256("9.9") == "wheel"
    with pytest.raises(ValueError, match="no wheel benchexec-1.0-py3-none-any.whl"):
        db.pypi_wheel_sha256("1.0")


def test_install_downloads_the_pinned_wheel_and_writes_the_license(tmp_path, fake_pip):
    calls, sha256 = fake_pip
    lib = tmp_path / "lib"
    wheel = db.install("9.9", sha256, lib, replace=False)
    assert wheel == lib / "benchexec-9.9-py3-none-any.whl"
    assert calls == [f"benchexec==9.9 --hash=sha256:{sha256}\n"]
    assert db.license_file(wheel).read_text() == (
        "== benchexec-9.9.dist-info/licenses/LICENSES/Apache-2.0.txt ==\nApache text\n")
    assert sorted(path.name for path in lib.iterdir()) == [wheel.name, wheel.name + ".license"]


def test_install_refuses_another_wheel_without_replace(tmp_path, fake_pip):
    calls, sha256 = fake_pip
    lib = tmp_path / "lib"
    lib.mkdir()
    old = lib / "benchexec-3.31-py3-none-any.whl"
    old.write_text("old")
    with pytest.raises(FileExistsError, match="benchexec-3.31-py3-none-any.whl"):
        db.install("9.9", sha256, lib, replace=False)
    assert calls == []
    assert [path.name for path in lib.iterdir()] == [old.name]


def test_install_with_replace_removes_the_other_wheel_and_its_license(tmp_path, fake_pip):
    _, sha256 = fake_pip
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "benchexec-3.31-py3-none-any.whl").write_text("old")
    (lib / "benchexec-3.31-py3-none-any.whl.license").write_text("old license")
    (lib / "other.txt").write_text("unrelated")
    db.install("9.9", sha256, lib, replace=True)
    assert sorted(path.name for path in lib.iterdir()) == [
        "benchexec-9.9-py3-none-any.whl", "benchexec-9.9-py3-none-any.whl.license", "other.txt"]


def test_install_keeps_a_wheel_that_has_the_hash(tmp_path, fake_pip):
    calls, sha256 = fake_pip
    lib = tmp_path / "lib"
    lib.mkdir()
    wheel = lib / "benchexec-9.9-py3-none-any.whl"
    make_wheel(wheel)
    assert db.file_sha256(wheel) == sha256
    db.install("9.9", sha256, lib, replace=False)
    assert calls == []
    assert db.license_file(wheel).exists()


def test_install_refuses_a_wheel_with_another_hash(tmp_path, fake_pip):
    _, sha256 = fake_pip
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "benchexec-9.9-py3-none-any.whl").write_text("damaged")
    with pytest.raises(ValueError, match="is not the wheel with SHA-256"):
        db.install("9.9", sha256, lib, replace=False)


def test_install_refuses_a_download_with_another_hash(tmp_path, fake_pip):
    lib = tmp_path / "lib"
    with pytest.raises(ValueError, match="pip did not produce"):
        db.install("9.9", "0" * 64, lib, replace=False)
    assert list(lib.iterdir()) == []


def test_extract_license_falls_back_to_the_metadata(tmp_path):
    wheel = tmp_path / "w.whl"
    make_wheel(wheel, licence="")
    target = tmp_path / "w.whl.license"
    assert db.extract_license(wheel, target)
    assert target.read_text() == "License: Apache-2.0\n"
