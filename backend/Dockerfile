# Backend images (deploy/docker-compose.yml). One Dockerfile, three targets:
#   app     - api (uvicorn) and the one-shot migration
#   worker  - Celery worker and beat (scheduler); adds the docker CLI and the runner package so the worker starts the
#             code sandbox containers itself (build context `runner` = ../runner, a compose additional context)
#   ai      - stt / tts speech servers (faster-whisper with CUDA 12 / cuDNN 9 from pip wheels; Supertonic)
# Build context: backend/.
# Behind a TLS-inspecting proxy, pass its CA as the `build_ca` secret (deploy/.env BUILD_CA_FILE); unset = nothing.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv/backend
WORKDIR /srv/backend
# Dependencies only (cached until pyproject.toml changes); the source runs from /srv/backend, not installed.
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
      > /tmp/req.txt
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; export PIP_CERT=/tmp/ca.pem; fi; \
    pip install -r /tmp/req.txt

FROM base AS app
COPY . .
RUN useradd --uid 10010 --create-home aihire && mkdir -p /data && chown aihire /data
USER aihire
ENV AIHIRE_DATA_DIR=/data
EXPOSE 8000
# --workers 1, no --reload: WebRTC sessions live in this process (TRD 01 §1).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM docker:29-cli AS dockercli

FROM app AS worker
USER root
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=runner runner /srv/runner/runner
COPY --from=runner sandbox /srv/runner/sandbox
ENV PYTHONPATH=/srv/backend:/srv/runner
# Root: the worker drives the Docker daemon through the mounted socket (the sandboxes themselves run as uid 10001
# with no network, ADR-0007).
CMD ["python", "-m", "app.worker_main"]

FROM base AS ai
RUN python -c "import tomllib; print('\n'.join(x.split(';')[0] for x in tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies']['ai'] if 'nvidia' not in x))" \
      > /tmp/req-ai.txt
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; export PIP_CERT=/tmp/ca.pem; fi; \
    pip install -r /tmp/req-ai.txt "nvidia-cublas-cu12==12.*" "nvidia-cudnn-cu12==9.*"
# ctranslate2 finds cuBLAS/cuDNN through the loader path; the GPU driver itself comes from the NVIDIA runtime.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib
COPY . .
# Models come from the read-only /models mount (scripts/prepare_models.py); no downloads at run time.
ENV HF_HOME=/models/hf SUPERTONIC_CACHE_DIR=/models/supertonic3
EXPOSE 8201 8202
CMD ["python", "-m", "app.aisvc.speech_server", "stt"]
