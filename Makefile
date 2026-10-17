# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

SVCOMP_DOCKERFILE := scripts/sv-comp/Dockerfile
DOCKER := DOCKER_BUILDKIT=1 DOCKER_DEFAULT_PLATFORM=linux/amd64 docker
SVCOMP_IMAGE := cooperace-smoketest
# Further options of `docker run`, for example --cpuset-cpus 0-3.
DOCKER_RUN_OPTIONS ?=

.PHONY: svcomp integration

# The archive's smoke test as fm-tools' CI runs it (scripts/sv-comp/Dockerfile).
svcomp:
	./scripts/svcomp-dist.sh
	$(DOCKER) build --progress=plain -t $(SVCOMP_IMAGE) -f $(SVCOMP_DOCKERFILE) dist
	$(DOCKER) run --rm --privileged $(DOCKER_RUN_OPTIONS) $(SVCOMP_IMAGE)

# Regression suite for the components inside CoOpeRace; see tests/integration/README.md.
integration:
	python3 tests/integration/run.py
