# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""src/tool_combinations/tool_combinations.py: the per-task results and score of
a BenchExec result file (plain or bzip2), the combination sizes, the
combination of two tools, and the CSV output."""
import bz2
import csv
import importlib.util
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "tool_combinations"


def load(name, directory=DIRECTORY):
    spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tc = load("tool_combinations")

WITNESS_MISSING = "witness missing (false(no-data-race))"


def result_xml(runs):
    """A result file from (name, expectedVerdict, category, status) tuples."""
    lines = ['<?xml version="1.0" ?>', '<result benchmarkname="t">']
    for name, expected, category, status in runs:
        lines += [
            f'  <run name="{name}" expectedVerdict="{expected}">',
            f'    <column title="category" value="{category}" hidden="true"/>',
            f'    <column title="status" value="{status}"/>',
            '    <column title="cputime" value="1s"/>',
            '  </run>',
        ]
    return "\n".join(lines + ["</result>"]).encode()


RUNS = [
    ("a", "false", "correct", "false(no-data-race)"),
    ("b", "false", "error", WITNESS_MISSING),
    ("c", "false", "error", "TIMEOUT"),
    ("d", "false", "wrong", "true"),
    ("e", "true", "correct", "true"),
    ("f", "true", "wrong", "false(no-data-race)"),
    ("g", "true", "unknown", "unknown"),
    ("h", "false", "correct-unconfirmed", "false(no-data-race)"),
]


@pytest.mark.parametrize("result_type, per_task, score", [
    ("validated", {"a": ("correct", 1), "b": ("unknown", 0), "c": ("unknown", 0), "d": ("wrong", -32),
                   "e": ("correct", 2), "f": ("wrong", -16), "g": ("unknown", 0), "h": ("unknown", 0)},
     1 - 32 + 2 - 16),
    ("verified", {"a": ("correct", 1), "b": ("correct", 1), "c": ("unknown", 0), "d": ("wrong", -32),
                  "e": ("correct", 2), "f": ("wrong", -16), "g": ("unknown", 0), "h": ("correct", 1)},
     1 + 1 - 32 + 2 - 16 + 1),
])
@pytest.mark.parametrize("suffix", [".xml", ".xml.bz2"])
def test_score_is_the_sum_of_the_per_task_results(tmp_path, suffix, result_type, per_task, score):
    path = tmp_path / f"tool.2024{suffix}"
    data = result_xml(RUNS)
    path.write_bytes(bz2.compress(data) if suffix.endswith("bz2") else data)

    results, total = tc.ToolData.tool_results_per_task(str(path), result_type)

    assert results == per_task
    assert total == score == sum(points for _, points in results.values())


def test_a_category_that_is_not_scored_gives_no_entry():
    assert tc.ToolData.task_result("false", "missing", "invalid task", "verified") is None
    assert tc.ToolData.task_result("true", "correct-unconfirmed", "x", "verified") == ("unknown", 0)


def test_parse_xml_data_reads_both_kinds_of_file_by_tool_name(tmp_path):
    (tmp_path / "goblint.1.results.x.xml").write_bytes(result_xml(RUNS))
    (tmp_path / "deagle.2.results.x.xml.bz2").write_bytes(bz2.compress(result_xml(RUNS)))
    (tmp_path / "notes.txt").write_text("not a result")

    tools = tc.parse_xml_data("validated", str(tmp_path))

    assert list(tools) == ["deagle", "goblint"]
    assert tools["deagle"].score == tools["goblint"].score == -45


@pytest.mark.parametrize("n, sizes", [(0, []), (1, [1]), (2, [1, 2]), (3, [1, 2, 3]), (5, [1, 2, 3])])
def test_n_combinations_includes_the_full_set_when_n_is_at_least_the_number_of_tools(n, sizes):
    combinations = tc.n_combinations({"a": 0, "b": 0, "c": 0}, n)

    assert list(combinations) == sizes
    assert [len(c) for c in combinations.values()] == [{1: 3, 2: 3, 3: 1}[size] for size in sizes]
    if 3 in sizes:
        assert combinations[3] == [["a", "b", "c"]]


def tool(name, results):
    return tc.ToolData(name, sum(points for _, points in results.values()), results)


def test_tools_list_score_result_combines_results_and_leaves_the_list_alone():
    unknown, correct, wrong = ("unknown", 0), ("correct", 1), ("wrong", -32)
    tools = {
        "a": tool("a", {"t1": correct, "t2": unknown, "t3": wrong, "t4": unknown}),
        "b": tool("b", {"t1": unknown, "t2": correct, "t3": correct, "t4": unknown}),
    }
    names = ["a", "b"]

    combined = tc.tools_list_score_result(names, tools)

    assert names == ["a", "b"]
    assert combined.name == "a_b"
    assert combined.results == {"t1": correct, "t2": correct, "t3": wrong, "t4": unknown}
    assert combined.score == 1 + 1 - 32 == sum(points for _, points in combined.results.values())


def test_write_result_csv_keeps_a_score_equal_to_the_minimum(tmp_path):
    data = [("a", 1400, {"t": ("correct", 1)}), ("b", 1399, {"t": ("unknown", 0)})]
    location = tmp_path / "out.csv"

    tc.write_result_csv(str(location), data, 1400, True)

    assert list(csv.reader(location.open())) == [["Tool combination", "Score", "t"], ["a", "1400", "correct"]]


def test_write_result_csv_reports_the_file_it_cannot_write(tmp_path):
    location = tmp_path / "missing" / "out.csv"

    with pytest.raises(FileNotFoundError) as error:
        tc.write_result_csv(str(location), [("a", 1, {})], 0, False)

    assert error.value.filename == str(location)


def test_main_writes_into_the_output_directory_without_a_trailing_slash(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "goblint.1.xml").write_bytes(result_xml(RUNS))
    out = tmp_path / "out"
    out.mkdir()

    tc.main(["-r", "validated", "-i", str(results), "-o", str(out), "-m", "-100", "-c", "4"])

    assert [p.name for p in out.iterdir()] == ["results-1-combinations-validated.csv"]


def test_main_says_which_file_it_could_not_write(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "goblint.1.xml").write_bytes(result_xml(RUNS))

    with pytest.raises(SystemExit) as error:
        tc.main(["-r", "validated", "-i", str(results), "-o", str(tmp_path / "nope"), "-m", "-100"])

    message = str(error.value)
    assert message.startswith("Could not write ") and "results-1-combinations-validated.csv" in message


def test_min_score_help_gives_the_default(capsys):
    with pytest.raises(SystemExit):
        tc.parse_arguments(["--help"])

    assert "(default: 1400)" in " ".join(capsys.readouterr().out.split())
    assert tc.parse_arguments(["-r", "verified", "-o", "o", "-i", "i"]).min_score == 1400
