# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

SVCOMP_DOCKERFILE := scripts/sv-comp/Dockerfile
DOCKER := DOCKER_BUILDKIT=1 DOCKER_DEFAULT_PLATFORM=linux/amd64 docker
SVCOMP_IMAGE := cooperace-smoketest

.PHONY: svcomp integration

svcomp:
	./scripts/svcomp-dist.sh
	$(DOCKER) build --progress=plain -t $(SVCOMP_IMAGE) -f $(SVCOMP_DOCKERFILE) dist

# Regression suite for the components inside CoOpeRace; see tests/integration/README.md.
integration:
	python3 tests/integration/run.py
