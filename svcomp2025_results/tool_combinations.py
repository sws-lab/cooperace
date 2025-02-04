import os
import csv
import xml.etree.ElementTree as ET

from itertools import combinations

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
    def from_xml_file(cls, name, path):
        results, score = cls.tool_results_per_task(path)
        return cls(name, score, results)

    @staticmethod
    def tool_results_per_task(path):
        with open(path, 'rb') as file:
            data = file.read().decode('utf-8-sig')

        tree = ET.ElementTree(ET.fromstring(data))
        root = tree.getroot()

        tasks = {}
        score = 0

        for run in root:
            if run.tag == "run":
                name = run.attrib["name"]
                for column in run:
                    if column.attrib["title"] == "category":
                        if run.attrib["expectedVerdict"] == "false":
                            #Tool gives true when expected false
                            if column.attrib["value"] == "wrong":
                                tasks[name] = ("wrong", -32)
                                score -= 32
                            elif column.attrib["value"] == "correct":
                                tasks[name] = ("correct", 1)
                                score += 1
                            elif column.attrib["value"] == "error" or column.attrib["value"] == "unknown" or column.attrib["value"] == "correct-unconfirmed":
                                tasks[name] = ("unknown", 0)
                        elif run.attrib["expectedVerdict"] == "true":
                            #Tool gives false when expected true
                            if column.attrib["value"] == "wrong":
                                tasks[name] = ("wrong", -16)
                                score -= 16
                            elif column.attrib["value"] == "correct":
                                tasks[name] = ("correct", 2)
                                score += 2
                            elif column.attrib["value"] == "error" or column.attrib["value"] == "unknown" or column.attrib["value"] == "correct-unconfirmed":
                                tasks[name] = ("unknown", 0)

        return tasks, score


    def tool_score(self, path):
        with open(path, 'rb') as file:
            data = file.read().decode('utf-8-sig')

        tree = ET.ElementTree(ET.fromstring(data))
        root = tree.getroot()

        score = 0

        for run in root:
            if run.tag == "run":
                for column in run:
                    if column.attrib["title"] == "category":
                        if run.attrib["expectedVerdict"] == "false":
                            #Tool gives true when expected false
                            if column.attrib["value"] == "wrong":
                                score -= 32
                            elif column.attrib["value"] == "correct":
                                score += 1
                        elif run.attrib["expectedVerdict"] == "true":
                            #Tool gives false when expected true
                            if column.attrib["value"] == "wrong":
                                score -= 16
                            elif column.attrib["value"] == "correct":
                                score += 2
        return score


def parse_xml_data(results_folder=None):
    if results_folder is None:
        dir = os.getcwd()
    else:
        dir = os.path.abspath(results_folder)

    tools = {}

    for file_name in os.listdir(dir):
        if os.path.isfile(os.path.join(dir, file_name)) and file_name.endswith(".xml"):
            path = os.path.join(dir, file_name)
            tool_name = file_name.split(".")[0]
            tools[tool_name] = ToolData.from_xml_file(tool_name, path)

    return tools

#Gives all combinations of given tools.
#Returns in the format of a dict, where key is n and value is a list of lists containing n tools
def n_combinations(tools: dict, n=5):
    tool_names = []

    for name in tools.keys():
        tool_names.append(name)

    sublists_dict = {}

    for r in range(1, min(n, len(tool_names))):
        sublists = [list(combination) for combination in combinations(tool_names, r)]
        sublists_dict[r] = sublists
    
    if n == len(tool_names):
        sublists_dict[n] = [tool_names]

    return sublists_dict

#Takes a list of tool names and gets the result and score of that combination
def tools_list_score_result(tool_names: list, tools_dict: dict, base: ToolData = None):
    if base is None:
        base_tool_name = tool_names[0]
        tool_names.remove(base_tool_name)
        base_tool = tools_dict[base_tool_name]
    else:  
        base_tool = ToolData(
            name=base.name,
            score=base.score,
            results=base.results
        )

    for name in tool_names:
        base_tool = ToolData.from_combination(base_tool.name + "_" + name, base_tool.results, tools_dict[name].results)

    return base_tool

def write_result_csv(location: str, data: list, score_limit = 1600, individual_tasks=False):
    rows = []

    new_data = list(filter(lambda x: x[1] > score_limit, data))

    header = ["Tool combination"] + [name for name, _, _ in new_data]
    rows.append(header)

    score_row = ["Score"] + [score for _, score, _ in new_data]
    rows.append(score_row)

    if individual_tasks:
        tasks_list = new_data[0][2].keys()

        for task_name in tasks_list:
            row = [task_name]
            for tool in new_data:
                row.append(tool[2][task_name][0])
            rows.append(row)

    #Some gigahack line of code from Chat-GPT to make the data table transposed
    transposed_rows = list(map(list, zip(*rows)))

    with open(location, "w", newline="") as outputfile:
        writer = csv.writer(outputfile)
        writer.writerows(transposed_rows)

if __name__ == "__main__":
    tools_dict = parse_xml_data()

    #goblint = tools_dict.pop("goblint")
    #dartagnan = tools_dict.pop("dartagnan")
    #default_combination = ToolData.from_combination("goblint_dartagnan", goblint.results, dartagnan.results)
    #print(default_combination.score)

    all_combinations = n_combinations(tools_dict, len(tools_dict))

    for i in range(len(all_combinations)):
        combination_list = []
        for tool_combination in all_combinations[i+1]:
            data = tools_list_score_result(tool_combination, tools_dict)
            combination_list.append((data.name, data.score, data.results))
        write_result_csv(f"results-{i+1}-combinations.csv", combination_list, 0)





