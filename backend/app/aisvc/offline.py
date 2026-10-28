"""Offline settings for the model libraries (SEC-06, AC-22)."""


def offline_env(auto_download: bool) -> dict[str, str]:
    """TRD 07 §12-4: no telemetry ever; no Hugging Face hub calls unless model download was explicitly allowed (dev
    first run). Set before any model library is imported: huggingface_hub reads these at import time."""
    env = {"HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}
    if not auto_download:
        env |= {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    return env
