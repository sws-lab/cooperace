# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

SVCOMP_DOCKERFILE := scripts/sv-comp/Dockerfile
DOCKER := DOCKER_BUILDKIT=1 DOCKER_DEFAULT_PLATFORM=linux/amd64 docker
SVCOMP_CI_IMAGE := cooperace-smoketest
SVCOMP_COMPETITION_IMAGE := cooperace-smoketest-competition
SVCOMP_YEAR := 2027
# CoOpeRace's fm-tools entry, a path or a URL; the default is the file on
# fm-tools' branch cooperace-2027 (scripts/sv-comp/fmtools-entry.py).
FMTOOLS_ENTRY ?=
# Further options of `docker run`, for example --cpuset-cpus 0-3.
DOCKER_RUN_OPTIONS ?=

.PHONY: svcomp integration

# Builds the archive and checks it in the two environments of fm-tools' CI
# (scripts/sv-comp/Dockerfile): the smoke test in the CI's Ubuntu image with
# the entry's required_ubuntu_packages installed, then those packages and the
# smoke test, directly and under runexec, in the competition image. The
# archive's VERSION names the version the entry's participation names.
svcomp:
	python3 scripts/sv-comp/fmtools-entry.py --year $(SVCOMP_YEAR) $(if $(FMTOOLS_ENTRY),--entry '$(FMTOOLS_ENTRY)') dist/fmtools
	FMTOOLS_VERSION="$$(cat dist/fmtools/version)" ./scripts/svcomp-dist.sh
	{ doi="$$(cat dist/fmtools/doi)"; \
	  echo "== Archive checked: dist/cooperace.zip, VERSION $$(cat dist/cooperace/VERSION), md5 $$(md5sum < dist/cooperace.zip | cut -d' ' -f1) =="; \
	  echo "fm-tools' CI checks the archive of the version's DOI ($${doi:-none registered yet})."; \
	  echo "This check runs the locally built archive in its place, which is that archive only if it was built from the same commit."; \
	} > dist/fmtools/archive-note.txt
	cat dist/fmtools/archive-note.txt
	@echo "== Check 1 of 2: fm-tools CI job smoke-test, in the CI image =="
	$(DOCKER) build --progress=plain --target ci -t $(SVCOMP_CI_IMAGE) -f $(SVCOMP_DOCKERFILE) dist
	$(DOCKER) run --rm --privileged $(DOCKER_RUN_OPTIONS) $(SVCOMP_CI_IMAGE)
	@echo "== Check 2 of 2: the required packages and the smoke test in the competition image =="
	$(DOCKER) build --progress=plain --target competition -t $(SVCOMP_COMPETITION_IMAGE) -f $(SVCOMP_DOCKERFILE) dist
	$(DOCKER) run --rm --privileged --tmpfs /opt/cooperace:exec $(DOCKER_RUN_OPTIONS) $(SVCOMP_COMPETITION_IMAGE)
	@echo "== Both checks passed on dist/cooperace.zip =="

# Regression suite for the components inside CoOpeRace; see tests/integration/README.md.
integration:
	python3 tests/integration/run.py
