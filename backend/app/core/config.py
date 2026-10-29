"""Runtime settings (environment / .env). PRD-fixed numbers live in constants.py instead."""
import ipaddress
import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HOSTNAMES = {"localhost"}


def is_private_endpoint(url: str) -> bool:
    """True when the URL host is loopback, a private range, or a bare compose service name (SEC-06, SC-80)."""
    host = urlparse(url).hostname
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." in host:  # FQDNs must resolve to private addresses only
            try:
                infos = socket.getaddrinfo(host, None)
            except OSError:
                return False
            return all(ipaddress.ip_address(i[4][0]).is_private for i in infos)
        return True  # single-label name: docker compose service
    return ip.is_loopback or ip.is_private


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIHIRE_", env_file=".env", extra="ignore")

    env: str = Field("dev", pattern="^(dev|test|prod)$")
    database_url: str = "postgresql+asyncpg://aihire:aihire_dev_only@127.0.0.1:55432/aihire"
    data_dir: Path = Path("var")

    # Sessions (USR-06 idle timeout is a constant). Secure cookies are required in prod.
    cookie_secure: bool = False
    cookie_name: str = "aihire_sid"  # prod: "__Host-aihire_sid" (requires Secure, Path=/)
    throttle_key: SecretStr = SecretStr("dev-throttle-key-change-me")
    trusted_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"]

    # Interview capacity (INT-13, 8.3: start with 1-2 on the 6GB box)
    max_concurrent_interviews: int = 2
    slot_lease_seconds: int = 120  # T10: INTERRUPTED grace
    answer_grace_seconds: float = 1.0  # T9
    tts_playout_guard_seconds: float = 0.3

    # Retention (8.5, legal review pending X-13)
    retention_audio_days: int = 30
    retention_content_days: int = 365

    # EVL-02 / OQ-09
    short_answer_min_chars: int = 15
    quote_min_chars: int = 8

    # AI service (all inference on-prem, SEC-06)
    ai_service_url: str = "http://127.0.0.1:8200"
    ai_token: SecretStr = SecretStr("dev-ai-token-change-me")  # X-AI-Token between app/worker and ai (TRD 05 §4)
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:4b"
    llm_num_ctx: int = 8192
    embed_model: str = "qwen3-embedding:0.6b"
    stt_model: str = "large-v3-turbo"
    stt_device: str = "cuda"  # "cpu" falls back to faster-whisper small
    stt_compute_type: str = "int8_float16"
    tts_engine: str = "supertonic"  # "supertonic" | "none" (text-only interviews, INT-04)
    tts_voice: str = ""  # Supertonic voice style name; empty = first available (fixed per manifest)
    tts_steps: int = 8
    tts_speed: float = 1.05
    model_auto_download: bool = False  # prod is offline (AC-22): models are imported beforehand
    gpu_policy: str = Field("shared", pattern="^(shared|stt_preempts_batch)$")  # TRD 05 §3, chosen by S-10

    # WebRTC when `app` runs in a container (app/voice/netcfg.py). Off = aioice defaults (host networking).
    rtc_udp_port_min: int = Field(0, ge=0, le=65535)
    rtc_udp_port_max: int = Field(0, ge=0, le=65535)
    rtc_announce_ips: list[str] = []

    # Redis (ADR-0013): job dispatch (sorted set per resource) and real-time state events (pub/sub -> SSE).
    # Empty = off: workers claim from PostgreSQL directly and the event stream answers 503.
    redis_url: str = "redis://127.0.0.1:6379/0"

    # File storage (app/core/storage.py): "local" = files under data_dir; "s3" = MinIO bucket, encrypted at rest.
    storage_backend: str = Field("local", pattern="^(local|s3)$")
    s3_endpoint: str = "127.0.0.1:9000"  # host:port, private only
    s3_access_key: SecretStr = SecretStr("aihire")
    s3_secret_key: SecretStr = SecretStr("dev-minio-secret-change-me")
    s3_bucket: str = "aihire"
    s3_secure: bool = False
    s3_encrypt: bool = True

    # Code runner (COD-06). Execution stays off until the sandbox self-test passes (ADR-0007).
    runner_url: str = "http://127.0.0.1:8400"
    runner_token: SecretStr = SecretStr("dev-runner-token-change-me")
    code_execution_enabled: bool = False

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        for name in ("ai_service_url", "ollama_url", "runner_url", "redis_url"):
            url = getattr(self, name)
            if url and not is_private_endpoint(url):
                raise ValueError(f"{name} must point to a local/private host (SEC-06): {url}")
        if self.storage_backend == "s3" and not is_private_endpoint(f"http://{self.s3_endpoint}"):
            raise ValueError(f"s3_endpoint must point to a local/private host (SEC-06): {self.s3_endpoint}")
        if (self.rtc_udp_port_min or self.rtc_udp_port_max) and not 1024 <= self.rtc_udp_port_min <= self.rtc_udp_port_max:
            raise ValueError("rtc_udp_port_min..max must be a range of unprivileged ports")
        from app.voice.netcfg import validate_announce_ips

        validate_announce_ips(self.rtc_announce_ips)
        if self.env == "prod":
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true in prod")
            for secret in ("runner_token", "throttle_key", "ai_token", "s3_secret_key"):
                if "change-me" in getattr(self, secret).get_secret_value():
                    raise ValueError(f"{secret} must be set in prod")
            if not self.cookie_name.startswith("__Host-"):
                raise ValueError("prod cookie_name must use the __Host- prefix")
            if any(not o.startswith("https://") for o in self.trusted_origins):
                raise ValueError("prod trusted_origins must be https")
            if "aihire_dev_only" in self.database_url:
                raise ValueError("prod database_url must not use the dev password")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
