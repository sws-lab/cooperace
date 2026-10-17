# syntax=docker/dockerfile:1.7

# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# The container of fm-tools' CI job smoke-test (ci/.gitlab-ci.yml of
# sosy-lab/benchmarking/fm-tools, read at commit 91acb17e), which runs
# smoketest.sh of the archive before the archive can be registered:
# docker.io/ubuntu:24.04 as root, the packages its before_script installs,
# and the required_ubuntu_packages of CoOpeRace's fm-tools entry, which
# `fm-weck smoke-test --gitlab-ci-mode` installs with `apt install -y`.
# fuse-overlayfs is not: smoketest.sh runs ./cooperace directly, so a
# component's overlay is the second level on the image's overlay file system,
# which the kernel's overlayfs allows. unzip unpacks the archive, which fm-weck does in Python. Run it with `docker run --privileged`
# (make svcomp): the CI's cgroup setup writes to /sys/fs/cgroup.
ARG BASE_IMAGE=docker.io/ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
FROM scratch AS archive
COPY cooperace.zip /tmp/cooperace.zip

FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Berlin
RUN apt-get update \
    && apt-get install --no-install-recommends -y git python3 python3-pip python3-venv python-is-python3 pipx yq \
    && apt-get install -y clang cpp lib32gcc-13-dev libc6-dev libc6-dev-i386 libgcc-13-dev libgmp10 libmpfr6 \
       openjdk-21-jre-headless \
    && apt-get install --no-install-recommends -y unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=archive /tmp/cooperace.zip /tmp/cooperace.zip
RUN unzip /tmp/cooperace.zip -d /opt \
    && rm /tmp/cooperace.zip
# What the CI job does in the container: ci/setup_cgroupsv2_in_container.sh
# of fm-tools (BenchExec's documented setup of cgroups v2 in a container),
# then smoketest.sh from the root of the archive.
COPY --chmod=755 <<'EOF' /usr/local/bin/ci-smoke-test
#!/bin/sh
set -eu
mkdir -p /sys/fs/cgroup/init /sys/fs/cgroup/benchexec
while read -r pid; do echo "$pid" > /sys/fs/cgroup/init/cgroup.procs; done < /sys/fs/cgroup/cgroup.procs
for controller in $(cat /sys/fs/cgroup/cgroup.controllers); do
  echo "+$controller" > /sys/fs/cgroup/cgroup.subtree_control
  echo "+$controller" > /sys/fs/cgroup/benchexec/cgroup.subtree_control
done
cd /opt/cooperace
exec ./smoketest.sh
EOF
CMD ["ci-smoke-test"]
