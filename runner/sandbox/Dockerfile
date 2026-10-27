# Code-test sandbox image (ADR-0007, TRD 07 13.2). Python only (X-10).
#
# Pinning: build with the base image *digest*, never a floating tag, and record the resulting image ID.
#   docker image inspect python:3.12-slim --format "{{index .RepoDigests 0}}"
#   docker build -t aihire-sandbox:py312 --build-arg BASE_IMAGE=python@sha256:<digest> runner/sandbox
# The digest verified on the dev box (2026-10-27, Python 3.12-slim, linux/amd64) is the default below.
# The runner puts the built image ID into its fingerprint, so any rebuild/retag disables execution until the
# self-test (SBX-01~14) passes again. For offline production, move the image with `docker save` / `docker load`.
ARG BASE_IMAGE=python@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
FROM ${BASE_IMAGE}

# Non-root account with a fixed uid/gid and no shell; pip and docs removed; every setuid/setgid bit stripped.
RUN groupadd --gid 10001 sandbox \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sandbox \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip /usr/local/lib/python3.12/site-packages/pip-* \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 /root/.cache /usr/share/doc /usr/share/man \
 && (find / -xdev -type f -perm /6000 -exec chmod a-s {} + || true)

# Launcher is root-owned and read-only; the directory stays traversable (COPY --chmod would also strip x on it).
COPY launcher.py /opt/sandbox/launcher.py
RUN chown -R root:root /opt/sandbox && chmod 0755 /opt/sandbox && chmod 0444 /opt/sandbox/launcher.py

LABEL org.opencontainers.image.title="aihire-sandbox" aihire.sandbox.abi="frame-v1"
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-S", "-B", "/opt/sandbox/launcher.py"]
