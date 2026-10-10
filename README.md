# CoOpeRace: Cooperative Data Race Freedom Verification

CoOpeRace is a cooperative verification tool. 
It is a meta-verifier that runs the components named in [`tools.txt`](tools.txt), one fm-tools entry
and Zenodo DOI per line. `tools.txt` is the lock file of the components of the SV-COMP archive:
`scripts/download-tools.py` installs exactly those archives into `tools/` and records each DOI in
`tools/<name>/.doi`, and `make svcomp` packs the components it names.
[`tools-pool.txt`](tools-pool.txt) is the lock file of the further components CoOpeRace can run, which
are not in the archive; `scripts/download-tools.py --pool` installs them too.
The options each component is run with are the `benchexec_toolinfo_options` of its version in fm-tools,
which `scripts/download-tools.py` writes into the tracked [`tools-options.json`](tools-options.json)
(`--options-only --fm-tools-dir DIR` writes it from a checkout of fm-tools without downloading anything).
CoOpeRace reads the options from that file and refuses to run a component whose `tools/<name>/.doi` is not
the DOI recorded there. `scripts/download-tools.py --check` says whether `tools/` and `tools-options.json`
match the lock files.

The goal of the CoOpeRace project is to identify the ultimate state-of-the-art in race freedom verification,
attempt better ways of communicating intermediate results between tools, and 
provide a user interface for comparing output of different tools.

CoOpeRace needs Python 3.10 or later and PyYAML, which the bundled BenchExec wheel in `lib/` imports (the
competition image has it as `python3-yaml`). The helper scripts need more: [`pyproject.toml`](pyproject.toml)
lists their packages as the `scripts` extra, and the packages of the checks in `.github/workflows/ci.yml`
as the `dev` dependency group. The project is not installed as a package; the launcher runs from its checkout.

To test the sv-comp package, run `./cooperace --prop tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i`,
which prints `CoOpeRace verdict: false` as its last line and writes `witness.graphml`.
`scripts/sv-comp/smoketest.sh` checks exactly that, and the archive carries both files at these paths.

CoOpeRace can be started from any working directory: it finds its `lib/`, `conf/` and `tools/` from the location of the launcher. `--prop` is required. CoOpeRace recognizes the property from the formulas in the property file (unreach-call, no-overflow, valid-memsafety or no-data-race, as sv-benchmarks writes them) and runs the conf's strategy for that property; a conf can give each property its own strategy ([`conf/README.md`](conf/README.md)). A property the conf has no strategy for, and a property file of none of these four properties, are refused. Every conf in `conf/`, `conf/svcomp26.json` included, has a strategy for no-data-race only, so CoOpeRace answers only no-data-race. The last line of the output is `CoOpeRace verdict: true`, `false` or `unknown` for no-data-race; for the other properties a `false` names the property, as BenchExec's result for the accepted component does: `false(unreach-call)`, `false(no-overflow)`, or `false(valid-deref)`, `false(valid-free)` or `false(valid-memtrack)` for valid-memsafety. A component's status counts as a verdict only if it belongs to the task's property: `false(unreach-call)` is no verdict on a no-data-race task, and a plain `false` is none on a valid-memsafety task, whose expected `false` names the violated sub-property. A defect of the command line, the property, the conf or the installation ends CoOpeRace with status 1 (2 for a command line that argparse refuses), one line `CoOpeRace: error: ...` on stderr and no verdict line, which BenchExec records as ERROR. A component that runs and fails is not such a defect: its status is printed and the next step runs.

CoOpeRace runs every component through BenchExec's `RunExecutor` (`src/cooperace/execution.py`), and finds out once per run which of its parts work where it runs; each component's block names the result in a line `Tool name: X Mode: ...`:

- **Under `runexec` or `benchexec` with cgroups v2**, as in the competition: each component runs in BenchExec's container, whose directory modes make the component's directory an overlay, and in a cgroup of its own, which carries the conf's limits ([`conf/README.md`](conf/README.md)). Stopping a component kills its container with every process in it.
- **From a shell on Ubuntu 24.04**, whose AppArmor gives user namespaces only to BenchExec's own commands: without the container, with the cgroup limits where BenchExec can make a cgroup (a systemd user session and pystemd). Stopping a component kills every process in its cgroup.
- **Where no cgroup is usable** (Docker or podman with their default read-only `/sys/fs/cgroup`, cgroups v1 inside BenchExec's container): with the container if user namespaces are allowed, else without it, and with no limit of the conf: each limit is printed as `not applied`, since BenchExec applies neither limit without its cgroup. Without the container, stopping a component kills its process group.

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
   override the archive path with `--file`. The helper sends the archive with one `PUT` to the
   draft's `links.bucket` URL, streamed from disk, and prints the bytes sent so you can keep an eye
   on Zenodo's slow ingestion. Afterwards it compares the MD5 of the local file with the checksum
   Zenodo reports and exits 1 on a mismatch, in which case the file in the draft is not the one
   you built and must be uploaded again before the record is published.
