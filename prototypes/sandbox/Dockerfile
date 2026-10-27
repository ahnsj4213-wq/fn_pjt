# Spike image for the Python code sandbox. Minimal: interpreter only, no pip use at runtime.
FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
 && rm -rf /root/.cache /usr/share/doc /usr/share/man
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-B", "-"]
