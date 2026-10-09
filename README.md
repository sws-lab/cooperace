# CoOpeRace: Cooperative Data Race Freedom Verification

CoOpeRace is a cooperative verification tool. 
It is a meta-verifier that runs the components named in [`tools.txt`](tools.txt), one fm-tools entry
and Zenodo DOI per line. `tools.txt` is the lock file of the components: `scripts/download-tools.py` installs
exactly those archives into `tools/` and records each DOI in `tools/<name>/.doi`
(`scripts/download-tools.py --check` says whether `tools/` matches `tools.txt`), and `make svcomp` packs
the components it names.

The goal of the CoOpeRace project is to identify the ultimate state-of-the-art in race freedom verification,
attempt better ways of communicating intermediate results between tools, and 
provide a user interface for comparing output of different tools.

To test the sv-comp package, run `./cooperace --prop tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i`,
which prints `CoOpeRace verdict: false` as its last line and writes `witness.graphml`.
`scripts/sv-comp/smoketest.sh` checks exactly that, and the archive carries both files at these paths.

CoOpeRace can be started from any working directory: it finds its `lib/`, `conf/` and `tools/` from the location of the launcher. `--prop` is required, and the property file must hold the no-data-race property (`CHECK( init(main()), LTL(G ! data-race) )`); another property is refused. The last line of the output is `CoOpeRace verdict: true`, `false` or `unknown`. A defect of the command line, the property, the conf or the installation ends CoOpeRace with status 1 (2 for a command line that argparse refuses), one line `CoOpeRace: error: ...` on stderr and no verdict line, which BenchExec records as ERROR. A component that runs and fails is not such a defect: its status is printed and the next step runs.

[How to download SV-COMP results logs](/src/tool_combinations)  
[How to get tool combinations and their theoretical scores](/src/tool_combinations)  
[How to create a tool configuration for CoOpeRace CLI](/conf)  

## SV-COMP Smoketests in Docker

1. Run the automated smoketest inside the SV-COMP competition image:
   ```bash
   make svcomp
   ```
   The smoketest output is streamed to your terminal, and the build returns non-zero if the check fails.

   The archive is built from the commit `HEAD` (`scripts/svcomp-dist.sh`), which must have no uncommitted
   changes to tracked files; `ALLOW_DIRTY=1` builds a development archive from the working tree instead,
   and such an archive must never be submitted. Set `FMTOOLS_VERSION` to the version name that the fm-tools
   entry will list, as in `FMTOOLS_VERSION=svcomp27 make svcomp`. The archive's file `VERSION` holds that
   name and `git describe --always --dirty`, and `./cooperace --version` prints them.

2. After `make svcomp`, the Docker image `cooperace-smoketest` is available. Launch an interactive shell with:
   ```bash
   docker run --rm -it --platform linux/amd64 cooperace-smoketest /bin/bash
   ```
   The unpacked archive lives in `/opt/cooperace`; from there you can re-run `./smoketest.sh` or execute `./cooperace` manually. The container uses the SV-COMP competition base image, so you interact with the same environment as the automated smoketest.

If you want to force a completely fresh run (no cached layers), clear BuildKit cache data before re-running:
`docker builder prune --all --force`.

## Uploading the SV-COMP archive to Zenodo

Use the helper script in `scripts/sv-comp/upload-zenodo.py` to attach the freshly
packaged archive to a private Zenodo record. It requires the Python `requests`
package (`pip install requests`).

1. Ensure the archive exists (`make svcomp` produces `dist/cooperace.zip`).
2. Export your Zenodo API token:
   ```bash
   export ZENODO_TOKEN="<your-token>"
   ```
   The token needs the `deposit:write` scope. The record should either be a
   draft or have a "latest draft" available (Zenodo's "New version" action).
3. Upload the archive (replace `<draft-or-record-id>` with your Zenodo record
   identifier):
   ```bash
   python scripts/sv-comp/upload-zenodo.py --record-id <draft-or-record-id>
   ```
   Pass `--sandbox` if you want to test against `https://sandbox.zenodo.org` or
   override the archive path with `--file`. The helper streams the 200 MB
   archive in chunks and prints upload progress so you can keep an eye on
   Zenodo's slow ingestion.
