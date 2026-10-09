import argparse
import bz2
import csv
import os
import xml.etree.ElementTree as ET
from itertools import combinations


def read_result_xml(path):
    """Returns the text of a BenchExec result file, plain (.xml) or compressed
    as SV-COMP publishes it (.xml.bz2)."""
    opener = bz2.open if path.endswith(".bz2") else open
    with opener(path, 'rb') as file:
        return file.read().decode('utf-8-sig')


class ToolData:
    def __init__(self, name, score, results):
        self.name = name
        self.score = score
        self.results = results

    @classmethod
    def from_combination(cls, name, results1: dict, results2: dict):
        score = 0
        results = {}

        for key in results1.keys():
            value1 = results1[key]
            value2 = results2[key]

            if value1 == value2:
                score += value1[1]
                results[key] = value1
            elif value1[0] == "unknown" and value2[0] != "unknown":
                score += value2[1]
                results[key] = value2
            elif value1[0] != "unknown" and value2[0] == "unknown":
                score += value1[1]
                results[key] = value1
            elif value1[0] != "unknown" and value2[0] != "unknown":
                if value1[0] == "wrong":
                    score += value1[1]
                    results[key] = value1
                elif value2[0] == "wrong":
                    score += value2[1]
                    results[key] = value2
                else:
                    score += value1[1]
                    results[key] = value1

        return cls(name=name, score=score, results=results)

    @classmethod
    def from_xml_file(cls, name, path, result_type):
        results, score = cls.tool_results_per_task(path, result_type)
        return cls(name, score, results)

    @staticmethod
    def task_result(expected_verdict, category, status, result_type):
        """Returns (kind, points) of one run, or None when its category is not
        one of those scored (for example "missing"): such a task has no entry.

        expected_verdict is the run's expectedVerdict, "true" or "false";
        category and status are the values of the columns of that name. In
        the "verified" result type a correct result whose witness no
        validator confirmed (category "correct-unconfirmed", or category
        "error" with status "witness missing (false(no-data-race))") counts
        as correct for a task expecting "false"."""
        if expected_verdict == "false":
            #Tool gives true when expected false
            if category == "wrong":
                return ("wrong", -32)
            if category == "correct":
                return ("correct", 1)
            if result_type == "verified":
                if category == "correct-unconfirmed":
                    return ("correct", 1)
                if category == "error" and status == "witness missing (false(no-data-race))":
                    return ("correct", 1)
            if category in ("error", "unknown", "correct-unconfirmed"):
                return ("unknown", 0)
        elif expected_verdict == "true":
            #Tool gives false when expected true
            if category == "wrong":
                return ("wrong", -16)
            if category == "correct":
                return ("correct", 2)
            if category in ("error", "unknown", "correct-unconfirmed"):
                return ("unknown", 0)
        return None

    @staticmethod
    def tool_results_per_task(path, result_type="validated"):
        """Returns ({task name: (kind, points)}, score) for a result file;
        score is the sum of the points of the tasks."""
        root = ET.fromstring(read_result_xml(path))

        tasks = {}

        for run in root.findall("run"):
            columns = {column.attrib["title"]: column.attrib.get("value") for column in run if "title" in column.attrib}
            if "category" not in columns:
                continue
            result = ToolData.task_result(run.attrib["expectedVerdict"], columns["category"],
                                          columns.get("status"), result_type)
            if result is not None:
                tasks[run.attrib["name"]] = result

        return tasks, sum(points for _, points in tasks.values())


def parse_xml_data(result_type, results_folder):
    if results_folder is None:
        dir = os.getcwd()
    else:
        dir = os.path.abspath(results_folder)

    tools = {}

    for file_name in sorted(os.listdir(dir)):
        if os.path.isfile(os.path.join(dir, file_name)) and file_name.endswith((".xml", ".xml.bz2")):
            path = os.path.join(dir, file_name)
            tool_name = file_name.split(".")[0]
            tools[tool_name] = ToolData.from_xml_file(tool_name, path, result_type)

    return tools

#Gives all combinations of given tools.
#Returns in the format of a dict, where key is r, for r from 1 to min(n, number of tools),
#and value is a list of lists containing r tools
def n_combinations(tools: dict, n=5):
    tool_names = list(tools.keys())

    sublists_dict = {}

    for r in range(1, min(n, len(tool_names)) + 1):
        sublists_dict[r] = [list(combination) for combination in combinations(tool_names, r)]

    return sublists_dict

#Takes a list of tool names and gets the result and score of that combination.
#The list is not modified.
def tools_list_score_result(tool_names: list, tools_dict: dict, base: ToolData = None):
    names = list(tool_names)

    if base is None:
        base_tool = tools_dict[names.pop(0)]
    else:
        base_tool = ToolData(
            name=base.name,
            score=base.score,
            results=base.results
        )

    for name in names:
        base_tool = ToolData.from_combination(base_tool.name + "_" + name, base_tool.results, tools_dict[name].results)

    return base_tool

#Writes results into a csv file, if individual_tasks is set to false, then csv will have combinations and their
#theoretical scores, otherwise it will also show results for each task for each combination.
#Only combinations with a score of at least score_limit are written.
#Raises OSError, with the file name, when the file cannot be written.
def write_result_csv(location: str, data: list, score_limit, individual_tasks):
    rows = []

    new_data = list(filter(lambda x: x[1] >= score_limit, data))

    header = ["Tool combination"] + [name for name, _, _ in new_data]
    rows.append(header)

    score_row = ["Score"] + [score for _, score, _ in new_data]
    rows.append(score_row)

    if individual_tasks and len(new_data) > 0:
        tasks_list = new_data[0][2].keys()

        for task_name in tasks_list:
            row = [task_name]
            for tool in new_data:
                row.append(tool[2][task_name][0])
            rows.append(row)

    #Transpose the table: one row per combination
    transposed_rows = list(map(list, zip(*rows, strict=True)))

    with open(location, "w", newline="") as outputfile:
        writer = csv.writer(outputfile)
        writer.writerows(transposed_rows)

def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="Process tool results and generate combination scores.")

    parser.add_argument('-r', '--result_type', type=str, required=True, choices=['verified', 'validated'],
                        help='Type of result to parse (verified, validated)')

    parser.add_argument('-o', '--output_path', type=str, required=True,
                        help='Directory path where result CSVs will be saved')

    parser.add_argument('-i', '--input_path', type=str, required=True,
                        help='Directory path to the input XML data (.xml or .xml.bz2)')

    parser.add_argument('-v', '--verbose', action='store_true',
                    help='CSV files will also include data about individual task results')

    parser.add_argument('-m', '--min_score', type=int, default=1400,
                    help='Minimum score (integer) required for a combination to be included in the CSV (default: 1400)')

    parser.add_argument('-c', '--max_combination', type=int, default=6,
                    help='Maximum combination size (default 6)')

    return parser.parse_args(argv)

def main(argv=None):
    args = parse_arguments(argv)

    tools_dict = parse_xml_data(result_type=args.result_type, results_folder=args.input_path)

    all_combinations = n_combinations(tools_dict, args.max_combination)

    for size, size_combinations in all_combinations.items():
        combination_list = []
        for tool_combination in size_combinations:
            data = tools_list_score_result(tool_combination, tools_dict)
            combination_list.append((data.name, data.score, data.results))
        location = os.path.join(args.output_path, f"results-{size}-combinations-{args.result_type}.csv")
        try:
            write_result_csv(location, combination_list, args.min_score, args.verbose)
        except OSError as error:
            raise SystemExit(f"Could not write {location}: {error.strerror}") from error

if __name__ == "__main__":
    main()
