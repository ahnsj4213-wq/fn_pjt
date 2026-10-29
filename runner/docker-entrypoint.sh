#!/bin/sh
# Builds the sandbox image on the host daemon the first time (the runner fingerprints its image ID, so a rebuild
# simply makes the self-test run again), then serves. The self-test (SBX-01~14) runs inside the server at start.
set -eu
IMAGE="${RUNNER_IMAGE:-aihire-sandbox:py311}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "runner: building sandbox image $IMAGE"
  python -m runner --build-image
fi
exec python -m runner "$@"
