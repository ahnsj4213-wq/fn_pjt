# Code-test sandbox image (ADR-0007, TRD 07 13.2). Python only (X-10).
#
# Pinning: build with the base image *digest*, never a floating tag, and record the resulting image ID.
#   docker image inspect python:3.11-slim --format "{{index .RepoDigests 0}}"
#   docker build -t aihire-sandbox:py311 --build-arg BASE_IMAGE=python@sha256:<digest> runner/sandbox
# Python 3.11 per the target container diagram (2026-10-29); the default below is the python:3.11-slim index digest
# pulled that day. (Earlier: 3.12-slim, verified on the dev box 2026-10-27.)
# The runner puts the built image ID into its fingerprint, so any rebuild/retag disables execution until the
# self-test (SBX-01~14) passes again. For offline production, move the image with `docker save` / `docker load`.
ARG BASE_IMAGE=python@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
FROM ${BASE_IMAGE}

# Non-root account with a fixed uid/gid and no shell; pip and docs removed; every setuid/setgid bit stripped.
RUN groupadd --gid 10001 sandbox \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sandbox \
 && rm -rf /usr/local/lib/python3.11/site-packages/pip /usr/local/lib/python3.11/site-packages/pip-* \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 /root/.cache /usr/share/doc /usr/share/man \
 && (find / -xdev -type f -perm /6000 -exec chmod a-s {} + || true)

# Launcher is root-owned and read-only; the directory stays traversable (COPY --chmod would also strip x on it).
COPY launcher.py /opt/sandbox/launcher.py
RUN chown -R root:root /opt/sandbox && chmod 0755 /opt/sandbox && chmod 0444 /opt/sandbox/launcher.py

LABEL org.opencontainers.image.title="aihire-sandbox" aihire.sandbox.abi="frame-v1"
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-S", "-B", "/opt/sandbox/launcher.py"]
