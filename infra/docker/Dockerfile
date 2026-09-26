# Builds MinIO server from source.
#
# Why this exists: MinIO stopped publishing pre-built community images
# in October 2025, archived github.com/minio/minio (Feb 2026, then
# again Apr 2026), and as of ~2026-09-11 deleted the minio/minio and
# minio/mc repositories from Docker Hub outright (not just stopped
# updating them - "pull access denied: repository does not exist").
# The quay.io mirror that many projects fell back to has since started
# requiring authentication as well. `image: minio/minio:latest` in
# docker-compose.yml will not pull anymore.
#
# The GitHub source repo is still clonable, though - "archived" only
# disables issues/PRs, not git clone/checkout - so this builds the
# `minio` binary from source instead of depending on a registry image.
# That also means the base Dockerfile MinIO ships in its own repo
# (`FROM minio/minio:latest`, used to layer pre-built release binaries
# on top of an existing image) doesn't work standalone anymore either;
# this Dockerfile compiles the binary itself instead of assuming that
# base image exists.
#
# Bump MINIO_RELEASE to track new tags: https://github.com/minio/minio/tags
# (git clone still works even though the repo is archived/read-only).

FROM golang:1.24-alpine AS build

ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --depth 1 --branch ${MINIO_RELEASE} \
      https://github.com/minio/minio.git .

# Mirrors `make build` (see the upstream Makefile's `build` target)
# minus the exact version ldflags - `minio --version` will report a
# generic dev version, which doesn't affect server behavior.
RUN CGO_ENABLED=0 go build -tags kqueue -trimpath -o /out/minio .

FROM alpine:3.20

RUN apk add --no-cache ca-certificates && \
    adduser -D -H -u 1000 minio && \
    mkdir -p /data && \
    chown -R minio:minio /data

COPY --from=build /out/minio /usr/bin/minio

USER minio
VOLUME ["/data"]
EXPOSE 9000 9001

ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]