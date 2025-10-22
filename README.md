# CoOpeRace: Cooperative Data Race Freedom Verification

CoOpeRace is a cooperative verification tool. 
It is a metaverfier that currently includes the following tools:
* Goblint: https://zenodo.org/records/14054652
* Dartagnan: https://zenodo.org/records/14079770
* Deagle: https://zenodo.org/records/14189745
* uAutomizer: https://zenodo.org/records/10202867
* uGemCutter: https://zenodo.org/records/10202867

The goal of the CoOpeRace project is to identify the ultimate state-of-the-art in race freedom verification,
attempt better ways of communicating intermediate results between tools, and 
provide a user interface for comparing output of different tools.

To test the sv-comp package, run `./cooperace --prop tests/no-data-race.prp tests/no-data-race/00-sanity_09-include.i`.

[How to download SV-COMP results logs](/src/tool_combinations)  
[How to get tool combinations and their theoretical scores](/src/tool_combinations)  
[How to create a tool configuration for CoOpeRace CLI](/conf)  

## SV-COMP Smoketests in Docker

1. Run the automated smoketest inside the SV-COMP competition image:
   ```bash
   make svcomp
   ```
   The smoketest output is streamed to your terminal, and the build returns non-zero if the check fails.

2. After `make svcomp`, the Docker image `cooperace-smoketest` is available. Launch an interactive shell with:
   ```bash
   docker run --rm -it cooperace-smoketest /bin/bash
   ```
   The unpacked archive lives in `/opt/cooperace`; from there you can re-run `./smoketest.sh` or execute `./cooperace` manually. The container uses the SV-COMP competition base image, so you interact with the same environment as the automated smoketest.

If you want to force a completely fresh run (no cached layers), clear BuildKit cache data before re-running:
`docker builder prune --all --force`.
