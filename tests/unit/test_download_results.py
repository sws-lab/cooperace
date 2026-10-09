"""src/tool_combinations/download_results.py, with `requests` and `bs4` replaced
by stubs, so that nothing is downloaded."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "tool_combinations"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, text="", content=b"", status=200):
        self.text, self.content, self.status = text, content, status

    def raise_for_status(self):
        if self.status != 200:
            raise FakeRequests.exceptions.RequestException(f"status {self.status}")


class FakeRequests(types.ModuleType):
    class exceptions:
        class RequestException(Exception):
            pass

    def __init__(self, responses):
        super().__init__("requests")
        self.responses, self.calls = responses, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses[url]


class Node:
    """Enough of a bs4 element for download_results: find, find_all, text, [href]."""

    def __init__(self, href=None, text="", children=()):
        self.href, self.text, self.children = href, text, list(children)

    def __getitem__(self, key):
        return self.href

    def find(self, *args, **kwargs):
        return self.children[0] if self.children else None

    def find_all(self, *args, **kwargs):
        return self.children


def fake_bs4(scores_and_hrefs):
    cells = [Node(children=[Node(href=href, text=str(score))]) for score, href in scores_and_hrefs]
    row = Node(children=cells)
    module = types.ModuleType("bs4")
    module.BeautifulSoup = lambda html, parser: Node(children=[row])
    return module


@pytest.fixture
def download(monkeypatch, tmp_path):
    """Loads download_results with the stubs; returns (module, fake requests)."""
    monkeypatch.chdir(tmp_path)

    def make(responses, scores_and_hrefs):
        requests = FakeRequests(responses)
        monkeypatch.setitem(sys.modules, "requests", requests)
        monkeypatch.setitem(sys.modules, "bs4", fake_bs4(scores_and_hrefs))
        return load("download_results"), requests

    return make


PAGE = "https://sv-comp.sosy-lab.org/2024/results/results-verified/"


def test_download_uses_the_year_in_both_urls_a_timeout_and_the_exact_suffix(download, tmp_path):
    name = "ab.table"  # str.rstrip(".table.html") would turn this into "ab"
    module, requests = download(
        {PAGE: Response(text="<html/>"), PAGE + name: Response(content=b"data")},
        [(3, name + ".table.html"), (0, "zero.xml.bz2.table.html")])

    failed = module.download_results(2024, "no-data-race.NoDataRace-Main")

    assert failed == []
    assert [url for url, _ in requests.calls] == [PAGE, PAGE + name]
    assert all(kwargs.get("timeout") for _, kwargs in requests.calls)
    assert (tmp_path / "results_2024_no-data-race.NoDataRace-Main" / name).read_bytes() == b"data"


def test_download_reports_the_files_it_could_not_get(download):
    module, _ = download(
        {PAGE: Response(text="<html/>"), PAGE + "a.xml.bz2": Response(status=404)},
        [(3, "a.xml.bz2.table.html")])

    assert module.download_results(2024, "c") == ["a.xml.bz2"]


def test_download_exits_non_zero_when_a_download_fails(download, monkeypatch):
    module, _ = download(
        {PAGE: Response(text="<html/>"), PAGE + "a.xml.bz2": Response(status=404)},
        [(3, "a.xml.bz2.table.html")])
    monkeypatch.setattr(sys, "argv", ["download_results.py", "--year", "2024", "--category", "c"])

    with pytest.raises(SystemExit) as error:
        exec(compile((DIRECTORY / "download_results.py").read_text(), "download_results.py", "exec"),
             {"__name__": "__main__"})

    assert error.value.code != 0 and "a.xml.bz2" in str(error.value)
