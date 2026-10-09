# SV-COMP 2025 results of the no-data-race category

One BenchExec result file per verifier for `SV-COMP25_no-data-race.NoDataRace-Main`, bzip2-compressed
(`<tool>.<start>.results.SV-COMP25_no-data-race.NoDataRace-Main.xml.bz2`), read by
`src/tool_combinations/tool_combinations.py`, which opens `.xml.bz2` directly.

The files are the 2025 results with the witness validation applied. They differ from the
`results-verified` files that `src/tool_combinations/download_results.py` downloads from
<https://sv-comp.sosy-lab.org/2025/results/results-verified/>: a run whose witness no validator confirmed has
status `witness missing (false(no-data-race))` and category `error` here, and `false(no-data-race)` and `correct`
there (203 runs of Deagle), and the DOCTYPE lines are removed. The scorer's `verified` result type counts
the first kind of run as correct, `validated` as unknown.
