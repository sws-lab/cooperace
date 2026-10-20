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

## Running CoOpeRace by hand

CoOpeRace runs each component in a BenchExec container, which needs user namespaces. In the competition,
`benchexec` runs `./cooperace` through BenchExec's tool-info module, and CoOpeRace makes the components'
containers inside its own. From a shell, run it under BenchExec's `runexec` with the competition's options
(`/` read-only, `/home` hidden, CoOpeRace's directory an overlay, `pids.max=5000`, 900 s of CPU time,
15 GB of memory), from CoOpeRace's directory:

```bash
TMPDIR=/tmp runexec --container --read-only-dir / --hidden-dir /home --overlay-dir . \
  --set-cgroup-value pids.max=5000 --timelimit 900s --memlimit 15GB \
  --output-directory out --result-files 'witness*' \
  -- ./cooperace --prop tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i
```

CoOpeRace's output is in `output.log`, whose last line is `CoOpeRace verdict: false`, and the witness of the
component that answered is `out/witness.yml`, if it wrote one in the format its verdict needs
(`src/cooperace/witnesses.py`, by the witness table `conf/witnesses.json`); `runexec` prints its report (CPU time,
wall time, memory) and exits 0 whether or not CoOpeRace succeeded. A task under `/home` is hidden: add
`--read-only-dir` with its directory. As root in a container, as in fm-tools' CI, BenchExec can make the
components' containers without `runexec`, and `./cooperace` runs directly; `scripts/sv-comp/smoketest.sh` does
that.

The repository also has `./cooperace-run`, which is not in the SV-COMP archive. It runs the same `runexec`
command from any directory, with the task's directory readable, prints CoOpeRace's output on stdout and
`runexec`'s report on stderr, copies the witness files into the working directory, and exits with
CoOpeRace's exit status. `runexec` options go before a `--` and replace the competition's limits:

```bash
./cooperace-run --prop tests/properties/no-data-race.prp tests/no-data-race/00-sanity_09-include.i
./cooperace-run --timelimit 60s --memlimit 4GB -- --prop task.prp task.i
```

The machine needs:

- Linux with cgroups v2, in which BenchExec can make a cgroup: from a shell under systemd that takes
  `pystemd` (Ubuntu's `python3-pystemd`). `python3 -m benchexec.check_cgroups` says whether it can.
- User namespaces for BenchExec's container. On Ubuntu 24.04, AppArmor allows them only to programs with a
  profile that permits them: install BenchExec's Debian package (with its recommended packages, among them
  `python3-pystemd` and `fuse-overlayfs`), whose `/usr/bin/runexec` has one and which `cooperace-run` uses
  when it exists, or set `sysctl kernel.apparmor_restrict_unprivileged_userns=0`. Without
  `/usr/bin/runexec`, `cooperace-run` runs the `runexec` of the bundled wheel in `lib/`.
- `fuse-overlayfs` where CoOpeRace's directory lies on an overlay file system (in a container) and is
  itself an overlay of `runexec`: the components' overlays are then a third level, which the kernel's
  overlayfs refuses.
- What the components need, the `required_ubuntu_packages` of their fm-tools entries (Java 21 for Dartagnan
  and ULTIMATE GemCutter; `gcc` for sv-sanitizers).

CoOpeRace can be started from any working directory: it finds its `lib/`, `conf/` and `tools/` from the location of the launcher. `--prop` is required. CoOpeRace recognizes the property from the formulas in the property file (unreach-call, no-overflow, valid-memsafety or no-data-race, as sv-benchmarks writes them) and runs the conf's strategy for that property; a conf can give each property its own strategy ([`conf/README.md`](conf/README.md)). A property the conf has no strategy for, and a property file of none of these four properties, are refused. The default conf, `conf/svcomp27.json`, has a strategy for each of the four. The last line of the output is `CoOpeRace verdict: true`, `false` or `unknown` for no-data-race; for the other properties a `false` names the property, as BenchExec's result for the component the verdict is taken from does: `false(unreach-call)`, `false(no-overflow)`, or `false(valid-deref)`, `false(valid-free)` or `false(valid-memtrack)` for valid-memsafety. When two tools of the bracket that decides contradict each other, it is `CoOpeRace verdict: ERROR (contradiction: Goblint true, Dartagnan false)`, naming them, which BenchExec scores as an error. A component's status counts as a verdict only if it belongs to the task's property: `false(unreach-call)` is no verdict on a no-data-race task, and a plain `false` is none on a valid-memsafety task, whose expected `false` names the violated sub-property. A defect of the command line, the property, the conf or the installation ends CoOpeRace with status 1 (2 for a command line that argparse refuses), one line `CoOpeRace: error: ...` on stderr and no verdict line, which BenchExec records as ERROR. A component that runs and fails is not such a defect: its status is printed and it counts as a component that gave no verdict.

CoOpeRace runs every component through BenchExec's `RunExecutor` in its container (`src/cooperace/execution.py`), as CoVeriTeam runs its actors: the component's directory is an overlay, from which its witness files are collected, the conf's limits are limits of the cgroup of the component's run ([`conf/README.md`](conf/README.md)), and stopping a component kills its container with every process in it. Each component's block ends with a line `Tool name: X Measured: ...` with the CPU time, wall time and memory BenchExec measured. The container needs user namespaces and the limits need cgroups v2, which CoOpeRace has inside the container of `runexec` or `benchexec`. Before any component starts, CoOpeRace checks that BenchExec can make the container; where it cannot, as from a plain shell on Ubuntu 24.04, it ends with status 1, a line `CoOpeRace: error: ...` that points to "Running CoOpeRace by hand" in this README, and no verdict line. It has no way of running a component without the container.

[How to download SV-COMP results logs](/src/tool_combinations)  
[How to get tool combinations and their theoretical scores](/src/tool_combinations)  
[How to create a tool configuration for CoOpeRace CLI](/conf)  

## SV-COMP Smoketests in Docker

1. Check the archive in the two environments in which fm-tools' CI checks it before it can be
   registered (`ci/.gitlab-ci.yml` of fm-tools):
   ```bash
   make svcomp FMTOOLS_ENTRY=<path or URL of fm-tools' data/cooperace.yml>
   ```
   `FMTOOLS_ENTRY` defaults to the file on fm-tools' branch `cooperace-2027`.
   [`scripts/sv-comp/fmtools-entry.py`](scripts/sv-comp/fmtools-entry.py) reads from it the version
   that the SV-COMP 2027 participation names as `tool_version`, as the CI does, and that version's
   `required_ubuntu_packages`; it fails if the version has `full_container_images`, for which the CI
   checks no package. The archive is built with that version's name in its `VERSION`. Then two images
   are built from [`scripts/sv-comp/Dockerfile`](scripts/sv-comp/Dockerfile) and run as root with
   `docker run --privileged`, which the CI's cgroup setup needs:
   - **Check 1, `cooperace-smoketest`**, the CI job `smoke-test`: `ubuntu:26.04` by digest, the
     packages of the job's `before_script`, the version's `required_ubuntu_packages` installed with
     `apt install -y` as `fm-weck smoke-test --gitlab-ci-mode` installs them, and `./smoketest.sh` in the
     unpacked archive. `./smoketest.sh` runs `./cooperace` directly: as root, BenchExec makes each
     component's container without `runexec`.
   - **Check 2, `cooperace-smoketest-competition`**, the competition image `user:2027` by digest, in which
     nothing is installed: `dpkg --get-selections <package> | grep install` must succeed for each required
     package, as the CI job `check-installed-required-packages-2027` requires; then `./smoketest.sh`
     runs directly, and its task runs with `./cooperace` under `runexec` with the competition's options
     (`--read-only-dir / --hidden-dir /home --overlay-dir <archive> --set-cgroup-value pids.max=5000`,
     900 s, 15 GB), which must print the same verdict and a witness. That `runexec` is the bundled
     wheel's, at `/usr/bin/runexec`, the path to which BenchExec's AppArmor profile grants user
     namespaces on a host that restricts them.

   The CI's smoke test runs the archive of the version's DOI. Until that archive is uploaded, and
   whenever the DOI's archive was built from another commit, the local `dist/cooperace.zip` stands in
   for it; both checks print a line saying which archive they ran, with its `VERSION` and MD5 sum.
   The output is streamed to your terminal, and `make` returns non-zero if a check fails.
   `DOCKER_RUN_OPTIONS` passes further options to `docker run`, such as `--cpuset-cpus 8-11`.

   The archive is built from the commit `HEAD` (`scripts/svcomp-dist.sh`), which must have no uncommitted
   changes to tracked files; `ALLOW_DIRTY=1` builds a development archive from the working tree instead,
   and such an archive must never be submitted. The archive's file `VERSION` holds the version name and
   `git describe --always --dirty`, and `./cooperace --version` prints them.

2. To work in the CI image, start a shell in it and run the CI's steps by hand:
   ```bash
   docker run --rm -it --privileged --platform linux/amd64 cooperace-smoketest /bin/bash
   ci-smoke-test
   ```
   `ci-smoke-test` sets up the cgroups and runs `./smoketest.sh` in `/opt/cooperace`; afterwards
   `./cooperace` can be run there by hand. In the competition image, start the shell with
   `--tmpfs /opt/cooperace:exec` and run `competition-check`.

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
   The draft's metadata is first replaced by `scripts/sv-comp/zenodo-metadata.json`
   (`--metadata FILE` to use another, `--no-metadata` to leave it, `--metadata-only` to set
   it and upload nothing). Pass `--sandbox` if you want to test against `https://sandbox.zenodo.org` or
   override the archive path with `--file`. The helper sends the archive with one `PUT` to the
   draft's `links.bucket` URL, streamed from disk, and prints the bytes sent so you can keep an eye
   on Zenodo's slow ingestion. Afterwards it compares the MD5 of the local file with the checksum
   Zenodo reports and exits 1 on a mismatch, in which case the file in the draft is not the one
   you built and must be uploaded again before the record is published.
