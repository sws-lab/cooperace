# syntax=docker/dockerfile:1.7

ARG COMPETITION_IMAGE=registry.gitlab.com/sosy-lab/benchmarking/competition-scripts/user:2026
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
