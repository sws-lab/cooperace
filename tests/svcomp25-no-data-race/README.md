# Tasks of the SV-COMP 2025 no-data-race category

The 1028 task definitions (`*.yml`) and their sources (`*.i`, `*.c`) of the no-data-race category of
SV-COMP 2025, copied from `sv-benchmarks` with `scripts/get_files_with_property.sh`. `tests/Race.set` lists them,
and the benchmark definitions in `tests/bench-defs/` (the single tools, the validator run and every
`combinations*` directory) use that set, so a run needs no checkout of `sv-benchmarks`. The copy is kept instead
of a set pointing into a checkout because the results in `svcomp2025_results/` are for exactly these tasks, and a
checkout of another `sv-benchmarks` version has other tasks, and no set file for this category: the category is
selected by the property file of each task.
