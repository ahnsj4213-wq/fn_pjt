#!/usr/bin/env bash
# Entry point of the `ai` container (deploy/docker-compose.yml, ADR-0015): four servers in one container.
#   :8201  STT  faster-whisper (GPU, CPU fallback)     python -m app.aisvc.speech_server stt
#   :8202  TTS  Supertonic (CPU)                       python -m app.aisvc.speech_server tts
#   :8082  evaluation LLM, llama.cpp on the CPU        (BATCH lane)
#   :8083  embeddings, llama.cpp --embeddings, BGE-M3
# When any of them exits, the others are stopped and the container exits, so Docker restarts all four together.
set -u

LLM_FILE="${AI_LLM_FILE:-/models/llm/qwen3.5-4b.gguf}"
EMBED_FILE="${AI_EMBED_FILE:-/models/embed/bge-m3.gguf}"
LLM_ALIAS="${AIHIRE_LLM_MODEL:-qwen3.5:4b}"

pids=()
python -m app.aisvc.speech_server stt & pids+=($!)
python -m app.aisvc.speech_server tts & pids+=($!)
/opt/llama/llama-server -m "$LLM_FILE" --host 0.0.0.0 --port 8082 --jinja -c 8192 --parallel 1 \
  -t "${LLM_EVAL_THREADS:-6}" --alias "$LLM_ALIAS" & pids+=($!)
/opt/llama/llama-server -m "$EMBED_FILE" --host 0.0.0.0 --port 8083 --embeddings --pooling cls -c 8192 -t 2 \
  --alias bge-m3 & pids+=($!)

stop() { kill -TERM "${pids[@]}" 2>/dev/null; }
trap 'stop; wait; exit 0' TERM INT

wait -n
code=$?
echo "ai: a server exited (code $code); stopping the others" >&2
stop
sleep 10
kill -KILL "${pids[@]}" 2>/dev/null
exit 1
