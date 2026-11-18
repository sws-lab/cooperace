#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""Static checks that a component archive still meets what CoOpeRace assumes
about it: the files, options and output strings its registry entry
(src/cooperace/components.py), its tool-info module and tools-options.json rely
on. Reads the zip (or an unpacked directory); runs nothing from it.
Usage: archive-static-check.py <component> <zip-or-dir> with <component> one of
goblint, dartagnan, deagle, sv-sanitizers, ugemcutter, ikos-conc, saf. Prints one
line per check, "ok", "FAIL" or "warn", and exits 1 on a FAIL (a "warn" does
not fail). scripts/component-smoke.py runs it on every archive it smokes."""
import re
import sys
import zipfile
from pathlib import Path


def reader(path):
    path = Path(path)
    if path.is_dir():
        files = {str(p.relative_to(path)): p for p in path.rglob("*") if p.is_file()}
        return set(files), lambda name: files[name].read_text(errors="replace")
    archive = zipfile.ZipFile(path)
    names = [n for n in archive.namelist() if not n.endswith("/")]
    top = {n.split("/", 1)[0] for n in names}
    prefix = top.pop() + "/" if len(top) == 1 else ""
    files = {n[len(prefix):]: n for n in names}
    return set(files), lambda name: archive.read(files[name]).decode(errors="replace")


def main(component, path):
    names, read = reader(path)
    checks = []

    def has(name):
        checks.append((f"file {name}", name in names, "FAIL"))
        return name in names

    def grep(name, pattern, what):
        found = name in names and re.search(pattern, read(name), re.M) is not None
        checks.append((f"{name}: {what}", found, "FAIL"))

    if component == "goblint":
        has("goblint_runner.py")
        has("conf/svcomp27/seq.txt")  # tools-options.json: --portfolio-conf conf/svcomp27/seq.txt
    elif component == "dartagnan":
        if has("Dartagnan-SVCOMP.sh"):
            grep("Dartagnan-SVCOMP.sh", r"--data-model=", "accepts --data-model= (toolinfo/dartagnan.py passes it)")
            grep("Dartagnan-SVCOMP.sh", r"DAT3M_OUTPUT", "sets DAT3M_OUTPUT (witness at output/witness.yml)")
    elif component == "deagle":
        if has("deagle"):
            grep("deagle", r'print\("\d+(\.\d+)+"\)', "--version prints dotted integers (deagle.py int-parses it)")
            for prop in ("unreach-call", "no-data-race", "no-overflow", "valid-memsafety"):
                grep("deagle", rf'"{prop}\.prp" in property_path:\s*\n\s*flags \+= "--yaml-witness witness\.yml',
                     f"{prop}: --yaml-witness witness.yml (registry WitnessSpec)")
            grep("deagle", r'print\("SUCCESSFUL"\)', "prints SUCCESSFUL (deagle.py: true)")
            grep("deagle", r'print\("FAILED"\)', "prints FAILED (deagle.py: false)")
            grep("deagle", r"--pointer-check", "valid-memsafety: --pointer-check (pointer_dereference. ids)")
            grep("deagle", r"--memory-leak-check", "valid-memsafety: --memory-leak-check (memory-leak. ids)")
        # 24278298 ships the witness.yml of its smoke test. CoOpeRace collects only the files a
        # run writes into the overlay of Deagle's directory, so a shipped one never counts
        # (tests/unit/test_witness_check.py); still worth a line, since a wrapper that stopped
        # writing witness.yml would leave the shipped file the only one there
        checks.append(("no witness.yml shipped in the archive", "witness.yml" not in names, "warn"))
    elif component == "sv-sanitizers":
        if has("sv-sanitizers.py"):
            grep("sv-sanitizers.py", r"SV-COMP result: ", 'prints "SV-COMP result: "')
            grep("sv-sanitizers.py", r"witness\.yml", "writes witness.yml")
            grep("sv-sanitizers.py", r'"--property"', "takes --property")
            grep("sv-sanitizers.py", r'"--data-model"', "takes --data-model")
    elif component == "ugemcutter":
        if has("Ultimate.py"):
            grep("Ultimate.py", r'^witnessname = "witness"', 'default witness name "witness" (witness.yml)')
            grep("Ultimate.py", r'"--witness-dir"', "takes --witness-dir")
            grep("Ultimate.py", r'"--full-output"', "takes --full-output (tools-options.json)")
            grep("Ultimate.py", r'result_msg = "valid-deref"', "maps valid-deref")
            grep("Ultimate.py", r'result_msg = "valid-memtrack"', "maps valid-memtrack")
            grep("Ultimate.py", r'"FALSE\(\{\}\)"\.format', "prints FALSE(<sub-property>)")
        for bits in ("32bit", "64bit"):
            checks.append((f"config svcomp-DerefFreeMemtrack-{bits}-GemCutter_*.epf",
                           any(re.fullmatch(rf"config/svcomp-DerefFreeMemtrack-{bits}-GemCutter_\w+\.epf", n)
                               for n in names), "FAIL"))
        has("config/GemCutterMemDerefMemtrack.xml")
    elif component == "ikos-conc":
        has("bin/svcomp_witness.py")
        if "bin/svcomp_witness.py" in names:
            # 24225248: bin/svcomp_witness.py is a shell-and-Python shim that runs the module
            # ikos.svcomp_witness of its bundled Python; the checks then read that module
            script = "bin/svcomp_witness.py"
            if re.search(r"^from ikos\.svcomp_witness import main", read(script), re.M):
                modules = sorted(n for n in names if n.endswith("site-packages/ikos/svcomp_witness.py"))
                checks.append(("bin/svcomp_witness.py runs ikos.svcomp_witness, which the archive holds",
                               bool(modules), "FAIL"))
                script = modules[0] if modules else script
            grep(script, r"--out", "takes --out (registry WitnessSpec)")
            text = read(script)
            for line in ("The program is definitely UNSAFE", "The program is SAFE"):
                checks.append((f'{script}: prints "{line}" (toolinfo/ikos_conc.py)', line in text, "FAIL"))
    elif component == "saf":
        # toolinfo/saf.py runs bin/saf verify and needs bin/ and share/ side by side; SAF finds its
        # stub header from its own path, and without it answers unknown on every task
        has("share/saf/stubs/sv-comp-stubs.h")
        if has("bin/saf"):
            binary = read("bin/saf")
            for text, what in (("witness.yml", "default --witness witness.yml (registry WitnessSpec)"),
                               ("data-model", "takes --data-model (toolinfo/saf.py passes it)"),
                               ("clang-21", "runs clang-21 (required_ubuntu_packages)")):
                checks.append((f"bin/saf: {what}", text in binary, "FAIL"))
        checks.append(("no witness.yml shipped in the archive", "witness.yml" not in names, "warn"))
    else:
        sys.exit(f"unknown component {component}")

    failed = 0
    for what, good, severity in checks:
        print(f"{'ok  ' if good else severity} {component}: {what}")
        failed += not good and severity == "FAIL"
    return 1 if failed else 0


COMPONENTS = ("goblint", "dartagnan", "deagle", "sv-sanitizers", "ugemcutter", "ikos-conc", "saf")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
