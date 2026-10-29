"""Worker container entry point (worker_mode=celery): one Celery worker per queue group, so each resource keeps
its own concurrency (TRD 01 §1: one INTERACTIVE and one BATCH LLM job at a time, two sandbox runs).

    python -m app.worker_main

The container exits when any worker exits (Docker restarts it). The sandbox self-test runs once at start.
"""
import os
import signal
import subprocess
import sys
import time

GROUPS = [("llm", "llm", 1), ("eval", "llm_eval", 1), ("runner", "runner,sandbox", 2),
          ("misc", "cpu,none,relay,periodic", 2)]


def ensure_sandbox_image() -> None:
    """Builds the sandbox image on the host daemon the first time (the self-test then verifies it)."""
    image = os.environ.get("RUNNER_IMAGE", "aihire-sandbox:py311")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        print(f"worker: building sandbox image {image}", flush=True)
        subprocess.run([sys.executable, "-m", "runner", "--build-image"], cwd="/srv/runner", check=False)


def main() -> int:
    ensure_sandbox_image()
    procs = []
    for name, queues, conc in GROUPS:
        cmd = [sys.executable, "-m", "celery", "-A", "app.celery_app", "worker", "-Q", queues, "-c", str(conc),
               "-n", f"{name}@%h", "--loglevel", os.environ.get("CELERY_LOGLEVEL", "INFO"), "-O", "fair"]
        procs.append(subprocess.Popen(cmd))
    subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.celery_app", "call", "periodic.run",
                      "--args", '["sandbox_verify"]', "--queue", "runner"]).wait()

    def stop(*_):
        for p in procs:
            p.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while all(p.poll() is None for p in procs):
        time.sleep(1)
    stop()
    for p in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
    return 1


if __name__ == "__main__":
    sys.exit(main())
