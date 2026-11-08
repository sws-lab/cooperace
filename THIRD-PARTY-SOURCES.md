# Third-party components under the GPL or LGPL: licences and source

CoOpeRace's own files are under the MIT license (`LICENSE`). The SV-COMP archive also conveys,
under `tools/<name>/`, the components that `tools.txt` names, each exactly as its Zenodo archive
holds it. This file lists every part of them that is under the GNU GPL or LGPL, says where its
licence text is in the archive, and where its Corresponding Source is (GPLv3 §6(d), GPLv2 §3,
LGPL). The texts of the GPL version 2 and version 3 are also in `LICENSES/GPL-2.0-only.txt` and
`LICENSES/GPL-3.0-or-later.txt`. The source locations are offered for as long as this version of
the archive is distributed; if one becomes unavailable, write to the maintainers of CoOpeRace
(https://github.com/sws-lab/cooperace) for a copy.

## IKOS-ConC (`tools/ikos-conc/`, Zenodo 10.5281/zenodo.23736684)

IKOS-ConC itself is under the NASA Open Source Agreement 1.3 (`tools/ikos-conc/LICENSE.txt`):
Copyright © 2011 United States Government as represented by the Administrator of the National
Aeronautics and Space Administration. All Rights Reserved. Its source:
https://github.com/2393591260/ikos-conc, commit 3d3b027d5a8669c45dc7b998f971aa5d57df141d
(`tools/ikos-conc/BUILD-INFO.json`), and the Zenodo record above.

- **Parma Polyhedra Library (PPL) 1.2**, Ubuntu package `libppl14` 1:1.2-8.1build5, GPL-3.0-or-later.
  Conveyed as the shared object `tools/ikos-conc/lib/libppl.so.14`, which `lib/libap_ppl.so`
  loads. Licence: `tools/ikos-conc/licenses/GPL-3` and `licenses/libppl14-copyright`. Corresponding
  Source, in the archive: `tools/ikos-conc/licenses/ppl-source/` (`ppl_1.2.orig.tar.xz`,
  `ppl_1.2-8.1build5.debian.tar.xz`, `.dsc`); the same files at
  https://launchpad.net/ubuntu/+source/ppl/1:1.2-8.1build5.
- **APRON**, LGPL-2.1 with a linking exception, except its PPL domain (`lib/libap_ppl.so`), which
  is GPL-2.0. Conveyed as the shared objects `tools/ikos-conc/lib/libapron.so`, `libap_ppl.so`,
  `libap_pkgrid.so`, `libboxMPQ.so`, `liboctMPQ.so`, `libpolkaMPQ.so`. Licences:
  `tools/ikos-conc/licenses/APRON-COPYING`, `APRON-PPL-COPYING`, `APRON-products-COPYING`.
  Source: https://github.com/antoinemine/apron. The archive does not record which APRON revision
  was built; ask IKOS-ConC's author for it.

## Goblint (`tools/goblint/`, Zenodo 10.5281/zenodo.24040934)

Goblint is under the MIT license. Source: https://github.com/goblint/analyzer, commit f793261a3.

- **APRON**, LGPL-2.1 with a linking exception: the shared objects `tools/goblint/lib/libapron.so`,
  `libboxD.so`, `liboctD.so`, `libpolkaMPQ.so`. Licence: `tools/goblint/lib/LICENSE.APRON`.
  Source: https://github.com/antoinemine/apron.
- The OCaml libraries linked statically into `tools/goblint/goblint` that are under the LGPL carry
  the OCaml static-linking exception.

## ULTIMATE GemCutter (`tools/ugemcutter/`, Zenodo 10.5281/zenodo.24060016)

- **Ultimate**, LGPL-3.0 (`tools/ugemcutter/LICENSE`, `LICENSE.GPL.LESSER`, `LICENSE.GPL`).
  Source: https://github.com/ultimate-pa/ultimate, commit 991986cc09 (version
  0.3.2-dev-991986cc09).
- **LTL2BA** 1.x, GPL-2.0-or-later: `tools/ugemcutter/ltl2ba`. Licence: `ltl2ba-LICENSE` and
  `LICENSE.GPL` (version 3, which "or later" permits). Source: http://www.lsv.fr/~gastin/ltl2ba/.
- Not under the GPL: cvc5 1.4.2 (BSD-3-Clause; built without its optional GPL libraries, per
  `cvc5 --show-config`), Z3 4.15.4 (MIT), Bitwuzla 0.9.1, Golem 0.9.1 (MIT), and MathSAT5, whose
  licence (`mathsat-LICENSE`) allows research and evaluation use only.

## Dartagnan (`tools/dartagnan/`, Zenodo 10.5281/zenodo.23897433)

Dartagnan is under the MIT license. Source: https://github.com/hernanponcedeleon/Dat3M.

- Its executable `dartagnan/target/dartagnan` is a native image built with Oracle GraalVM
  25.0.4+7.1. It contains the Java class library of JDK 25.0.4, GPL-2.0 with the Classpath
  Exception (`tools/dartagnan/LICENSES/GPL-2.0-only.txt`). Source:
  https://github.com/openjdk/jdk25u/releases/tag/jdk-25.0.4%2B7. The licences of the other
  dependencies in the image are in `tools/dartagnan/LICENSES/`.

## Deagle (`tools/deagle/`, Zenodo 10.5281/zenodo.23488039)

Deagle's `LICENSE` is BSD-4-Clause, with the terms of CBMC and MiniSat. Its archive also holds the
GPL-3.0 text (`tools/deagle/COPYING`). Source: https://github.com/thufv/deagle.

## sv-sanitizers (`tools/sv-sanitizers/`)

MIT; no bundled third-party code.
