# Code runner (ADR-0007). Talks to the host's Docker daemon through the mounted socket and starts one sandbox
# container per test case. Build context: runner/.
FROM docker:29-cli AS dockercli

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
# Behind a TLS-inspecting proxy: the optional `build_ca` secret (see backend/Dockerfile).
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; export PIP_CERT=/tmp/ca.pem; fi; \
    pip install "fastapi==0.162.0" "uvicorn==0.54.0" "pydantic==2.14.1"
WORKDIR /srv/runner
COPY runner ./runner
COPY sandbox ./sandbox
COPY docker-entrypoint.sh /usr/local/bin/runner-entrypoint
RUN chmod 0755 /usr/local/bin/runner-entrypoint
ENV RUNNER_HOST=0.0.0.0 RUNNER_PORT=8400
EXPOSE 8400
ENTRYPOINT ["runner-entrypoint"]
