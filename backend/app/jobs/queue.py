"""Enqueue side of the durable job queue (ADR-0006). Enqueue runs inside the caller's transaction so that
state changes and their follow-up work commit together (7.2: submission and evaluation job are atomic)."""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# kind -> (resource, priority, max_attempts). Lower priority value runs first.
JOB_KINDS: dict[str, tuple[str, int, int]] = {
    "compose_questions": ("LLM", 10, 3),
    "tts_pregen": ("CPU", 15, 3),
    "extract_resume": ("LLM", 20, 2),
    "grade_coding": ("RUNNER", 20, 3),
    "pre_evaluate": ("LLM", 40, 2),
    "evaluate_application": ("LLM", 50, 2),   # EVL-06: first attempt + one automatic regeneration
    "purge_files": ("NONE", 90, 10),
    "send_email": ("NONE", 30, 5),            # app/notify/mail.py
}


async def enqueue(db: AsyncSession, kind: str, payload: dict[str, Any], *, application_id: UUID | None = None,
                  dedupe_key: str | None = None, run_after: datetime | None = None,
                  depends_on: int | None = None) -> int | None:
    """Insert a QUEUED job. A live job with the same dedupe_key makes this a no-op (returns None)."""
    resource, priority, max_attempts = JOB_KINDS[kind]
    row = (await db.execute(text("""
        INSERT INTO jobs (kind, payload, application_id, dedupe_key, priority, resource, max_attempts, run_after, depends_on)
        VALUES (:kind, CAST(:payload AS jsonb), :app, :dedupe, :prio, :res, :max, COALESCE(:run_after, now()), :dep)
        ON CONFLICT (dedupe_key) WHERE state IN ('QUEUED','RUNNING') DO NOTHING
        RETURNING id"""), {
        "kind": kind, "payload": _json(payload), "app": application_id, "dedupe": dedupe_key, "prio": priority,
        "res": resource, "max": max_attempts, "run_after": run_after, "dep": depends_on,
    })).scalar_one_or_none()
    await db.execute(text("SELECT pg_notify('jobs', :kind)"), {"kind": kind})
    return row


def _json(payload: dict[str, Any]) -> str:
    import json
    return json.dumps({k: str(v) if isinstance(v, UUID) else v for k, v in payload.items()})
