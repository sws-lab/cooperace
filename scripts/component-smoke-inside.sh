#!/bin/bash

# SPDX-FileCopyrightText: 2026 University of Tartu
#
# SPDX-License-Identifier: MIT

# Runs inside the competition image (docker --privileged), started by
# scripts/component-smoke.py. Lays out /home/cwd as on the SV-COMP machines
# (bin/<tool dir>, sv-benchmarks, benchmark-defs; the tool directory is copied
# from /in/root, with the component's archive from /in/tool as
# tools/$COMPONENT, onto the tmpfs, since BenchExec cannot overlay a bind
# mount of the host here; sv-benchmarks is a read-only bind mount) and runs BenchExec's `benchexec`
# from the tool directory with the competition's options, one call and one
# task per line of /smoke/queue.tsv ("<id> <property> <task .yml under c/>"),
# one task at a time. The tool directory is a CoOpeRace whose default conf runs
# the one component under test. While a task runs, a sampler records every
# second the pids.peak, memory.peak and processes of each cgroup below
# BenchExec's into /out/<id>.cgroups.tsv.
# Env: COMPONENT (its directory name), DOI (its DOI), ATTRS (the <benchmark> limits), CORES (BenchExec --allowedCores),
# RUN_TIMEOUT (seconds per BenchExec call).
set -uo pipefail
umask 000
mkdir -p /sys/fs/cgroup/init /sys/fs/cgroup/benchexec
while read -r pid; do echo "$pid" > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null; done < /sys/fs/cgroup/cgroup.procs
for c in $(cat /sys/fs/cgroup/cgroup.controllers); do
  echo "+$c" > /sys/fs/cgroup/cgroup.subtree_control
  echo "+$c" > /sys/fs/cgroup/benchexec/cgroup.subtree_control
done
cp -a /in/benchexec-src /home/cwd/
mkdir -p /home/cwd/benchmark-defs /home/cwd/bin
cp -a /in/root /home/cwd/bin/cooperace && rmdir "/home/cwd/bin/cooperace/tools/$COMPONENT" \
  && cp -a /in/tool "/home/cwd/bin/cooperace/tools/$COMPONENT" || { echo "cannot copy the tool directory" > /out/environment.txt; exit 3; }
# An unpacked archive without the record of download-tools.py gets it in the copy
[ -e "/home/cwd/bin/cooperace/tools/$COMPONENT/.doi" ] || echo "$DOI" > "/home/cwd/bin/cooperace/tools/$COMPONENT/.doi"
# As the lane's inside.sh: the files belong to the user who runs BenchExec, as where SV-COMP unpacks an
# archive (Deagle's archive has a top directory of mode 0700, which BenchExec's user namespace could not read)
chown -R "$(id -u):$(id -g)" /home/cwd
cd /home/cwd/bin/cooperace || exit 3
{ grep PRETTY /etc/os-release; python3 --version; cat /home/cwd/benchexec-src/REVISION 2>/dev/null
  echo "attrs $ATTRS cores $CORES"; echo "cpuset $(cat /sys/fs/cgroup/cpuset.cpus.effective) mem.max $(cat /sys/fs/cgroup/memory.max)"; } > /out/environment.txt

sampler() {
  local id=$1
  while [ -e "/tmp/running.$id" ]; do
    local ts; ts=$(date +%s.%N | cut -c1-14)
    find /sys/fs/cgroup/benchexec -mindepth 1 -type d 2>/dev/null | while read -r d; do
      pk=$(cat "$d/pids.peak" 2>/dev/null) || continue
      mp=$(cat "$d/memory.peak" 2>/dev/null); sub=$(find "$d" -mindepth 1 -maxdepth 1 -type d | wc -l)
      comms=$(for q in $(cat "$d/cgroup.procs" 2>/dev/null); do cat /proc/$q/comm 2>/dev/null; done | sort -u | tr '\n' ',')
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "${d#/sys/fs/cgroup/benchexec}" "$pk" "$mp" "$sub" "$comms"
    done >> "/out/$id.cgroups.tsv"
    sleep 1
  done
}

while IFS=$'\t' read -r id prop task; do
  [ -n "$id" ] || continue
  cat > "/home/cwd/benchmark-defs/$id.xml" <<XML
<?xml version="1.0"?>
<!DOCTYPE benchmark PUBLIC "+//IDN sosy-lab.org//DTD BenchExec benchmark 2.3//EN" "https://www.sosy-lab.org/benchexec/benchmark-2.3.dtd">
<benchmark tool="cooperace" displayName="CoOpeRace component smoke" $ATTRS>
  <resultfiles>**</resultfiles>
  <rundefinition name="SV-COMP27_$prop">
    <tasks name="C.$prop.Concurrency">
      <include>../sv-benchmarks/c/$task</include>
      <propertyfile>../sv-benchmarks/c/properties/$prop.prp</propertyfile>
    </tasks>
  </rundefinition>
</benchmark>
XML
  touch "/tmp/running.$id"; sampler "$id" & SP=$!
  start=$(date -u +%FT%TZ)
  timeout "${RUN_TIMEOUT:-1150}" /usr/bin/benchexec "../../benchmark-defs/$id.xml" \
    --results-per-taskset --maxLogfileSize 2MB --read-only-dir / --read-only-dir /home/cwd --overlay-dir ./ \
    --hidden-dir /home/ -N 1 --allowedCores "$CORES" -n "$id" -o "/out/$id/" --no-compress-results \
    > "/out/$id.benchexec.txt" 2>&1
  rc=$?
  rm -f "/tmp/running.$id"; wait "$SP"
  printf '%s\t%s\t%s\t%s\n' "$id" "$start" "$(date -u +%FT%TZ)" "$rc" >> /out/done.tsv
done < /smoke/queue.tsv
chmod -R a+rwX /out 2>/dev/null
