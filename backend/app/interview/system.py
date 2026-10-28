"""GET /api/system/ai-health (TRD 06 §7, INT-14): whether an interview can start right now. LLM or STT down blocks
the start; TTS down only means text-only questions (OQ-08)."""
from fastapi import APIRouter, Depends

from app.auth.deps import get_principal
from app.auth.sessions import Principal
from app.interview import infra

router = APIRouter(prefix="/api/system", tags=["common"])


@router.get("/ai-health")
async def ai_health(_: Principal = Depends(get_principal)) -> dict:
    s = await infra.ai_status()
    return {**s, "can_start": s["llm"] and s["stt"]}
