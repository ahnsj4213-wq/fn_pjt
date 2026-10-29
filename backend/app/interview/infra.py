"""Infrastructure checks used before starting an interview (INT-14, T13). Kept separate so tests can replace them."""
import httpx

from app.core.config import get_settings


async def ai_status() -> dict:
    """{'llm': bool, 'stt': bool, 'tts': bool}. Unreachable AI service = everything down."""
    from app.ai.client import get_ai_client

    try:
        h = await get_ai_client().health()
    except Exception:  # noqa: BLE001
        return {"llm": False, "stt": False, "tts": False}
    return {k: bool(h.get(k, {}).get("up")) for k in ("llm", "stt", "tts")}


async def sandbox_ready() -> bool:
    """Code execution is offered only when enabled and the runner's self-check passed (ADR-0007)."""
    s = get_settings()
    if not s.code_execution_enabled:
        return False
    if s.runner_mode == "celery":  # API container: the worker publishes its self-test result
        from app.coding.sandbox_local import published_ready

        return await published_ready()
    if s.runner_mode == "local":
        from app.coding.sandbox_local import service

        return await service().ensure_valid()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{s.runner_url}/v1/health", headers={"X-Runner-Token": s.runner_token.get_secret_value()})
        return bool(r.json().get("sandbox_verified"))
    except Exception:  # noqa: BLE001
        return False
