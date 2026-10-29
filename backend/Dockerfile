# Backend images (deploy/docker-compose.yml). One Dockerfile, two targets:
#   app  - API (uvicorn), worker (`python -m app.jobs.runtime`) and the one-shot migration
#   ai   - the ai process: LLM gateway to Ollama, STT (faster-whisper, CUDA 12 / cuDNN 9 from pip wheels), TTS
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

FROM base AS ai
RUN python -c "import tomllib; print('\n'.join(x.split(';')[0] for x in tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies']['ai'] if 'nvidia' not in x))" \
      > /tmp/req-ai.txt
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem; export PIP_CERT=/tmp/ca.pem; fi; \
    pip install -r /tmp/req-ai.txt "nvidia-cublas-cu12==12.*" "nvidia-cudnn-cu12==9.*"
# ctranslate2 finds cuBLAS/cuDNN through the loader path; the GPU driver itself comes from the NVIDIA runtime.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib
COPY . .
# Model caches (Hugging Face: faster-whisper; Supertonic) live under /root/.cache, a volume in compose.
EXPOSE 8200
CMD ["python", "-m", "app.aisvc"]
