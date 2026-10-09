# syntax=docker/dockerfile:1.7

# The competition image, by tag and by digest (read 2026-10-09 with `docker
# buildx imagetools inspect`). Docker pulls by the digest, so a later push to
# the tag does not change the image; to move to a new image, change both.
ARG COMPETITION_IMAGE=registry.gitlab.com/sosy-lab/benchmarking/competition-scripts/user:2026@sha256:cf838d8305e42cecdbecd6158e90d1e9a58239c162f7c96ed3f80574676bc6da
FROM scratch AS archive
COPY cooperace.zip /tmp/cooperace.zip

FROM ${COMPETITION_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=archive /tmp/cooperace.zip /tmp/cooperace.zip
RUN unzip /tmp/cooperace.zip -d /opt \
    && rm /tmp/cooperace.zip
WORKDIR /opt/cooperace
RUN ./smoketest.sh
