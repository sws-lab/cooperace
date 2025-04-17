# Running tool combination scripts


## download_results.py

### This script is used for downloading SV-COMP result logs

download_results.py allows downloading all results of a specific category and year of SV-COMP. For example,
running:

`
python download_results --year 2024 --category no-data-race.NoDataRace-Main
`

will download all tool results that had a score of > 0 and took part in the NoDataRace-Main category of SV-COMP 2024.

## tool_combinations.py

### This script is used for analysing tool results for SV-COMP and giving theoretical scores for different combinations of verification tools

tool_combinations.py creates combinations of all the tools and gives their combined score in an effort to find what are theoretically the best combinations in solving the SV-COMP task list and hopefully analysing real life projects as well.

#### Parameters and their explanations for running this script
- -r, --result_type --- value can be either "verified" or "validated". Used to get the combination scores for either the verified combinations or validated combinations. Verified scores are scores that the tools got in SV-COMP before running the validators and validated scores are scores that the tools get after running the validators. For example: Deagle might get the correct result for a task but not provide a witness for the task. The verified score would be either +2 or +1 depending on the type of task, but the validated score would be 0 because none of the validators can confirm Deagle's result.
- -o, --output_path --- Specify what directory you want the combination results to go.
- -i, --input_path --- Specify the path to the logs of the tools you want to combine. All the logs in the specified directory must be for the same year and category, otherwise you might get unexpected results and errors.
- -v, --verbose --- If this is added, The output files will contain all individual task results for each combination
- -m, --min_score --- Optional, default is 1400, if you want higher/lower scores than this, you can specify what score combinations you would like
- -c, --max_combinations --- Optional, default is 6, maximum is the amount of logs in the input directory. Specify the biggest size of combinations with theoretical scores that will be generated

#### Example:
`
python tool_combinations.py -r verified -o /results -i /logs -v -m 0 -c 8
`

This will generate all combinations where the score is >= 0 and maximum combination size of 8 into the logs directory. The tool logs will be taken from /logs directory.

#### Output
The output is generated into different sized .csv files, with each .csv file containing combinations with specific sizes. So for the example above, in the output directory there will be generated 7 .csv files with each containing the specific sized combinations with a score >= 0.

