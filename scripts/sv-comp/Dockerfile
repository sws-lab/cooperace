# syntax=docker/dockerfile:1.7

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# The two environments in which fm-tools' CI checks the archive for SV-COMP
# 2027 (ci/.gitlab-ci.yml of sosy-lab/benchmarking/fm-tools, last changed at
# commit 16769ef4 of 2026-10-18), one target each; `make svcomp` builds and
# runs both. The build context is dist/: the archive dist/cooperace.zip, and
# in dist/fmtools/ what scripts/sv-comp/fmtools-entry.py read from
# CoOpeRace's fm-tools entry (required-packages.txt) and the note that says
# which archive is checked (archive-note.txt).
#
# Target `ci`, job `smoke-test`: docker.io/ubuntu:26.04 as root, the packages
# its before_script installs, then the version's required_ubuntu_packages,
# which `fm-weck smoke-test --gitlab-ci-mode` installs with `apt install -y`
# (fm-weck 1.8.1, fm_weck/smoke_test_mode.py), then ./smoketest.sh in the
# unpacked archive. The before_script also runs `pipx install fm-weck`, which
# is left out: fm-weck only downloads and unpacks the archive and runs the two
# commands above. Run it with `docker run --privileged`: the CI's cgroup setup
# writes to /sys/fs/cgroup.
#
# Target `competition`, the competition image user:2027 of
# sosy-lab/benchmarking/competition-scripts, which fm-tools' CI job
# `check-installed-required-packages-2027` runs in. Nothing is installed. The
# check fails unless `dpkg --get-selections <package> | grep install`
# succeeds for every required package, as that job requires, and then runs
# ./smoketest.sh directly, as in the CI. Last it runs smoketest.sh's task with
# ./cooperace under runexec with the competition's directory modes and limits,
# as BenchExec runs a tool there, and checks the verdict and the witness as
# smoketest.sh does. runexec does not run smoketest.sh itself: inside a run,
# BenchExec makes the components' cgroups only if CoOpeRace's processes are
# the only ones in the run's cgroup, and smoketest.sh's shell would be another.
# The archive is copied to /opt/cooperace, which must be a tmpfs (`docker run
# --privileged --tmpfs /opt/cooperace:exec`): runexec's overlay of it is then
# the first level, and a component's overlay inside it the second, which the
# kernel's overlayfs allows; on the image's own overlay file system the
# component's would be a third, which it refuses.
#
# fuse-overlayfs is in neither image. The archive is unpacked in a stage of
# its own, so that neither image gets `unzip`.
ARG CI_IMAGE=docker.io/ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
ARG COMPETITION_IMAGE=registry.gitlab.com/sosy-lab/benchmarking/competition-scripts/user:2027@sha256:206003f8899b72a2459f5a1cb78064996b38de04eee184b4f4f3feadc2453724

FROM ${CI_IMAGE} AS unpacked
RUN apt-get update \
    && apt-get install --no-install-recommends -y unzip \
    && rm -rf /var/lib/apt/lists/*
COPY cooperace.zip /tmp/cooperace.zip
RUN unzip -q /tmp/cooperace.zip -d /opt \
    && rm /tmp/cooperace.zip

FROM scratch AS scripts
# ci/setup_cgroupsv2_in_container.sh of fm-tools, BenchExec's documented setup
# of cgroups v2 in a container, which the CI job runs first.
COPY --chmod=755 <<'EOF' /setup-cgroups
#!/bin/sh
set -eu
mkdir -p /sys/fs/cgroup/init /sys/fs/cgroup/benchexec
while read -r pid; do echo "$pid" > /sys/fs/cgroup/init/cgroup.procs; done < /sys/fs/cgroup/cgroup.procs
for controller in $(cat /sys/fs/cgroup/cgroup.controllers); do
  echo "+$controller" > /sys/fs/cgroup/cgroup.subtree_control
  echo "+$controller" > /sys/fs/cgroup/benchexec/cgroup.subtree_control
done
EOF
COPY --chmod=755 <<'EOF' /ci-smoke-test
#!/bin/sh
set -eu
cat /etc/cooperace/archive-note.txt
setup-cgroups
cd /opt/cooperace
exec ./smoketest.sh
EOF
COPY --chmod=755 <<'EOF' /competition-check
#!/bin/bash
set -euo pipefail
cat /etc/cooperace/archive-note.txt
echo "== Required packages, as job check-installed-required-packages-2027 checks them =="
while read -r PKG; do
  if ! dpkg --get-selections "${PKG}" | grep install; then
    echo "Check failed: required package ${PKG} is not installed in the competition image." >&2
    exit 1
  fi
done < /etc/cooperace/required-packages.txt
if ! mountpoint -q /opt/cooperace; then
  echo "Check failed: /opt/cooperace is not a mount point; run with --tmpfs /opt/cooperace:exec." >&2
  exit 1
fi
setup-cgroups
fresh_copy() {
  find /opt/cooperace -mindepth 1 -delete
  cp -a /archive/cooperace/. /opt/cooperace/
}
cd /opt/cooperace

echo
echo "== ./smoketest.sh, run directly =="
fresh_copy
./smoketest.sh

echo
echo "== ./cooperace under runexec with the competition's options, as BenchExec runs it =="
fresh_copy
# The property, the task and the expected last line of smoketest.sh.
eval "$(grep -E '^(PROPERTY|TASK|EXPECTED)=' smoketest.sh)"
rm -rf /tmp/runexec-results
STATUS=0
runexec --read-only-dir / --hidden-dir /home --overlay-dir /opt/cooperace \
  --set-cgroup-value pids.max=5000 --timelimit 900s --memlimit 15GB \
  --dir /opt/cooperace --output /tmp/runexec-output.log \
  --result-files 'witness*' --output-directory /tmp/runexec-results \
  -- ./cooperace --prop "${PROPERTY}" "${TASK}" > /tmp/runexec-report.txt || STATUS=$?
cat /tmp/runexec-output.log || true
echo "-- runexec's report --"
cat /tmp/runexec-report.txt
if [ "${STATUS}" -ne 0 ] || ! grep -qx 'returnvalue=0' /tmp/runexec-report.txt \
    || grep -q '^terminationreason=' /tmp/runexec-report.txt; then
  echo "Check failed: under runexec, ./cooperace did not end with status 0 (runexec status ${STATUS})." >&2
  exit 1
fi
LAST_LINE="$(tail -n 1 /tmp/runexec-output.log)"
if [ "${LAST_LINE}" != "${EXPECTED}" ]; then
  echo "Check failed: under runexec, the last line of the output is '${LAST_LINE}', expected '${EXPECTED}'." >&2
  exit 1
fi
if [ -s /tmp/runexec-results/witness.yml ]; then
  python3 -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' /tmp/runexec-results/witness.yml
elif [ -s /tmp/runexec-results/witness.graphml ]; then
  python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' /tmp/runexec-results/witness.graphml
else
  echo "Check failed: under runexec, neither witness.yml nor witness.graphml was written." >&2
  exit 1
fi
echo "Witness files: $(ls /tmp/runexec-results)"
echo "== COMPETITION IMAGE CHECK SUCCESSFUL! =="
EOF
# runexec of the archive's bundled BenchExec wheel, at the path of the runexec
# of BenchExec's Debian package, with which the competition's machines run a
# tool. Where AppArmor restricts unprivileged user namespaces
# (kernel.apparmor_restrict_unprivileged_userns=1, a host setting that a
# container shares), that package's profile /etc/apparmor.d/benchexec allows
# them to /usr/bin/runexec and the processes it starts; if the host has the
# profile, it attaches to this file too. Without it, CoOpeRace inside the run
# cannot make the components' containers.
COPY --chmod=755 <<'EOF' /runexec
#!/usr/bin/python3
import glob
import sys

sys.path.insert(0, glob.glob("/opt/cooperace/lib/benchexec-*.whl")[0])
from benchexec.runexecutor import main

sys.exit(main())
EOF

FROM ${CI_IMAGE} AS ci
ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Berlin
COPY fmtools/required-packages.txt fmtools/archive-note.txt /etc/cooperace/
RUN apt-get update \
    && apt-get install --no-install-recommends -y git python3 python3-pip python3-venv python-is-python3 pipx yq \
    && xargs --no-run-if-empty --delimiter='\n' apt install -y < /etc/cooperace/required-packages.txt \
    && rm -rf /var/lib/apt/lists/*
COPY --from=unpacked /opt/cooperace /opt/cooperace
COPY --from=scripts /setup-cgroups /ci-smoke-test /usr/local/bin/
CMD ["ci-smoke-test"]

FROM ${COMPETITION_IMAGE} AS competition
ENV TZ=Europe/Berlin TMPDIR=/tmp
COPY fmtools/required-packages.txt fmtools/archive-note.txt /etc/cooperace/
COPY --from=unpacked /opt/cooperace /archive/cooperace
COPY --from=scripts /setup-cgroups /competition-check /usr/local/bin/
COPY --from=scripts /runexec /usr/bin/runexec
CMD ["competition-check"]
