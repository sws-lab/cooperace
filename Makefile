SVCOMP_DOCKERFILE := scripts/sv-comp/Dockerfile
DOCKER := DOCKER_BUILDKIT=1 DOCKER_DEFAULT_PLATFORM=linux/amd64 docker
SVCOMP_IMAGE := cooperace-smoketest

.PHONY: svcomp

svcomp:
	./scripts/svcomp-dist.sh
	$(DOCKER) build --progress=plain -t $(SVCOMP_IMAGE) -f $(SVCOMP_DOCKERFILE) dist
