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

To test the sv-comp package, run `./cooperace --prop tests/no-data-race.prp tests/test.i`.