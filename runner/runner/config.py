"""Runner settings from environment variables (no extra dependencies)."""
import os
from dataclasses import dataclass, field

DEFAULT_TOKEN = "dev-runner-token-change-me"  # same dev default as backend AIHIRE_RUNNER_TOKEN

# Fixed sandbox limits (TRD 07 13.2). Per-request wall time / memory are clamped to the ranges below.
PIDS_LIMIT = 64
CPUS = "1"
NOFILE = 64
FSIZE_BYTES = 8 * 1024 * 1024
TMPFS = "/tmp:rw,noexec,nosuid,nodev,size=16m"
SANDBOX_UID = "10001:10001"
STDOUT_CAP = 64 * 1024
STDERR_CAP = 16 * 1024
MAX_CODE_BYTES = 64 * 1024
MAX_STDIN_BYTES = 1024 * 1024
WALL_MS_RANGE = (100, 20_000)       # coding_problems.time_limit_ms CHECK
MEM_MB_RANGE = (32, 512)            # coding_problems.memory_mb CHECK
DEFAULT_WALL_MS = 5_000
DEFAULT_MEM_MB = 256
VERIFICATION_TTL_SEC = 24 * 3600
FINGERPRINT_CACHE_SEC = 60
SANDBOX_LABEL = "app=sandbox"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class RunnerConfig:
    token: str = field(default_factory=lambda: _env("RUNNER_TOKEN", DEFAULT_TOKEN))
    env: str = field(default_factory=lambda: _env("RUNNER_ENV", "dev"))            # dev | test | prod
    host: str = field(default_factory=lambda: _env("RUNNER_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("RUNNER_PORT", "8400")))
    image: str = field(default_factory=lambda: _env("RUNNER_IMAGE", "aihire-sandbox:py311"))
    docker: str = field(default_factory=lambda: _env("RUNNER_DOCKER", "docker"))
    # Hardened runtime for sandbox containers only (C1): e.g. "runsc". Empty = daemon default (dev: runc).
    runtime: str = field(default_factory=lambda: _env("RUNNER_RUNTIME", ""))
    approved_runtimes: tuple[str, ...] = field(
        default_factory=lambda: tuple(x for x in _env("RUNNER_APPROVED_RUNTIMES", "runsc").split(",") if x))
    cpuset: str = field(default_factory=lambda: _env("RUNNER_CPUSET", ""))
    concurrency: int = field(default_factory=lambda: int(_env("RUNNER_CONCURRENCY", "2")))

    def check(self) -> None:
        """Fail closed on unsafe production settings."""
        if self.env == "prod":
            if self.token == DEFAULT_TOKEN or len(self.token) < 24:
                raise ValueError("RUNNER_TOKEN must be set to a strong secret in prod")
            if self.host not in ("127.0.0.1", "::1") and not self.host.startswith(("10.", "172.", "192.168.")):
                raise ValueError("RUNNER_HOST must be loopback or a private address in prod")
