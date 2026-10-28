"""Deleting interview content by application, for the retention schedule and for withdrawal (TRD 03 §8).

Start of the clock (OQ-11, provisional): the first decision, or the posting's close when nothing was decided.
  stage 1  answer audio     start + retention_audio_days    key NULL, file queued, audio_purged_at
  stage 2  all content      start + retention_content_days  rows deleted / snapshot emptied, content_purged_at
Kept: the application row (posting, dates, the three states), decision_history, consent_records, audit_logs,
evaluation_runs (version metadata only). Files are never deleted inline: their keys go to `file_deletions`
(paths relative to data_dir) and `file_gc` removes them, so a rolled-back purge never loses a file.
Immutable tables accept these deletes only under `SET LOCAL app.purge = 'on'` (T14).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.timeutil import utcnow

BATCH = 200


@dataclass
class PurgeCounts:
    audio_apps: int = 0
    content_apps: int = 0
    tts_assets: int = 0
    files: int = 0

    def as_detail(self) -> dict:
        return {"audio_apps": self.audio_apps, "content_apps": self.content_apps, "tts_assets": self.tts_assets,
                "files": self.files}


async def _allow_purge(db: AsyncSession) -> None:
    await db.execute(text("SET LOCAL app.purge = 'on'"))


async def _queue_files(db: AsyncSession, sql: str, params: dict, reason: str) -> int:
    """Copies the storage keys selected by `sql` (one column, relative to data_dir) into the deletion outbox."""
    res = await db.execute(text(f"INSERT INTO file_deletions (storage_key, reason) SELECT k, :reason FROM ({sql}) AS s(k) "
                                "WHERE k IS NOT NULL"), {**params, "reason": reason})
    return res.rowcount or 0


async def purge_audio(db: AsyncSession, app_ids: list[UUID], reason: str) -> int:
    """Stage 1 for these applications. The caller holds the application rows."""
    if not app_ids:
        return 0
    await _allow_purge(db)
    n = await _queue_files(db, "SELECT audio_key FROM interview_answers WHERE application_id = ANY(:ids)",
                           {"ids": app_ids}, reason)
    await db.execute(text("UPDATE interview_answers SET audio_key = NULL WHERE application_id = ANY(:ids) "
                          "AND audio_key IS NOT NULL"), {"ids": app_ids})
    await db.execute(text("UPDATE applications SET audio_purged_at = now() WHERE id = ANY(:ids) "
                          "AND audio_purged_at IS NULL"), {"ids": app_ids})
    return n


async def purge_content(db: AsyncSession, app_ids: list[UUID], reason: str) -> int:
    """Stage 2 (includes stage 1). Children first; one statement per table keeps it set-based."""
    if not app_ids:
        return 0
    files = await purge_audio(db, app_ids, reason)
    p = {"ids": app_ids}
    q_of_apps = "SELECT id FROM interview_questions WHERE application_id = ANY(:ids)"
    files += await _queue_files(db, f"SELECT audio_key FROM question_tts_assets WHERE question_id IN ({q_of_apps})", p, reason)
    files += await _queue_files(db, "SELECT 'resumes/' || f.storage_key FROM resume_files f JOIN applications a "
                                    "ON a.resume_file_id = f.id WHERE a.id = ANY(:ids) AND f.purpose = 'APPLICATION'", p, reason)
    # Running jobs lose their lease: their late writes fail the fencing check (AC-18).
    await db.execute(text("UPDATE jobs SET state = 'CANCELLED', lease_token = NULL, lease_owner = NULL, "
                          "lease_expires_at = NULL, finished_at = now(), last_error_code = 'PURGED' "
                          "WHERE application_id = ANY(:ids) AND state IN ('QUEUED', 'RUNNING')"), p)
    answers = "SELECT id FROM interview_answers WHERE application_id = ANY(:ids)"
    for stmt in (
        f"DELETE FROM answer_evaluations WHERE answer_id IN ({answers})",
        f"DELETE FROM answer_pre_evaluations WHERE answer_id IN ({answers})",
        "DELETE FROM candidate_feedback WHERE application_id = ANY(:ids)",
        "DELETE FROM recruiter_reports WHERE application_id = ANY(:ids)",
        "DELETE FROM recruiter_notes WHERE application_id = ANY(:ids)",
        ("DELETE FROM coding_test_results WHERE submission_id IN "
         "(SELECT id FROM coding_submissions WHERE application_id = ANY(:ids))"),
        "DELETE FROM coding_submissions WHERE application_id = ANY(:ids)",
        "DELETE FROM coding_runs WHERE application_id = ANY(:ids)",
        "DELETE FROM coding_attempts WHERE application_id = ANY(:ids)",
        "DELETE FROM followup_decisions WHERE application_id = ANY(:ids)",
        "DELETE FROM proctor_events WHERE application_id = ANY(:ids)",
        "DELETE FROM interview_answers WHERE application_id = ANY(:ids)",
        "DELETE FROM interview_turns WHERE application_id = ANY(:ids)",
        f"DELETE FROM question_tts_assets WHERE question_id IN ({q_of_apps})",
        "UPDATE interviews SET current_question_id = NULL WHERE application_id = ANY(:ids)",
        "DELETE FROM interview_questions WHERE application_id = ANY(:ids)",
        "DELETE FROM interview_slots WHERE application_id = ANY(:ids)",
    ):
        await db.execute(text(stmt), p)
    old_files = (await db.execute(text("SELECT resume_file_id FROM applications WHERE id = ANY(:ids) "
                                       "AND resume_file_id IS NOT NULL"), p)).scalars().all()
    await db.execute(text("UPDATE applications SET resume_snapshot = '{}'::jsonb, resume_snapshot_text = '', "
                          "resume_file_id = NULL, content_purged_at = now() WHERE id = ANY(:ids)"), p)
    if old_files:
        await db.execute(text("DELETE FROM resume_files WHERE id = ANY(:f) AND purpose = 'APPLICATION'"), {"f": old_files})
    return files


def _due_sql(stamp_col: str) -> str:
    # Applications in progress are never touched; their clock has not started anyway.
    return (f"SELECT a.id FROM applications a JOIN job_postings p ON p.id = a.posting_id "
            f"WHERE a.{stamp_col} IS NULL AND COALESCE(a.first_decided_at, p.closed_at) < :cutoff "
            f"AND a.attempt_status NOT IN ('PREPARING', 'INTERVIEWING', 'CODING') "
            f"ORDER BY a.id LIMIT {BATCH} FOR UPDATE OF a SKIP LOCKED")


async def run_retention(db: AsyncSession, as_of: datetime | None = None, *, dry_run: bool = False) -> PurgeCounts:
    """One pass of `retention_purge`. Commits per batch; `as_of` fixes the clock for tests and `purge --dry-run`."""
    s = get_settings()
    now = as_of or utcnow()
    counts = PurgeCounts()
    stages = (("content_purged_at", now - timedelta(days=s.retention_content_days), purge_content, "RETENTION_CONTENT"),
              ("audio_purged_at", now - timedelta(days=s.retention_audio_days), purge_audio, "RETENTION_AUDIO"))
    for col, cutoff, fn, reason in stages:
        while True:
            ids = list((await db.execute(text(_due_sql(col)), {"cutoff": cutoff})).scalars())
            if not ids:
                break
            if dry_run:
                n = len(ids)
                await db.rollback()
            else:
                counts.files += await fn(db, ids, reason)
                await db.commit()
                n = len(ids)
            if col == "content_purged_at":
                counts.content_apps += n
            else:
                counts.audio_apps += n
            if dry_run or n < BATCH:
                break
    # TTS cache of finished attempts (questions stay until stage 2; only the synthesized audio goes).
    finished = ("SELECT t.question_id FROM question_tts_assets t JOIN interview_questions q ON q.id = t.question_id "
                "JOIN applications a ON a.id = q.application_id WHERE a.attempt_status IN ('SUBMITTED', 'EXPIRED')")
    if dry_run:
        counts.tts_assets = len((await db.execute(text(finished))).scalars().all())
        await db.rollback()
        return counts
    counts.files += await _queue_files(db, f"SELECT audio_key FROM question_tts_assets WHERE question_id IN ({finished})",
                                       {}, "TTS_CACHE")
    res = await db.execute(text(f"DELETE FROM question_tts_assets WHERE question_id IN ({finished})"))
    counts.tts_assets = res.rowcount or 0
    await db.commit()
    return counts


async def run_file_gc(db: AsyncSession) -> int:
    """Removes queued files (a missing file counts as done) and uploads nobody uses after 7 days (data-retention 2절)."""
    s = get_settings()
    await db.execute(text(
        "INSERT INTO file_deletions (storage_key, reason) SELECT 'resumes/' || f.storage_key, 'UNUSED_UPLOAD' "
        "FROM resume_files f WHERE f.purpose = 'UPLOAD' AND f.created_at < now() - interval '7 days' "
        "AND NOT EXISTS (SELECT 1 FROM resumes r WHERE r.file_id = f.id) "
        "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.resume_file_id = f.id)"))
    await db.execute(text(
        "DELETE FROM resume_files f WHERE f.purpose = 'UPLOAD' AND f.created_at < now() - interval '7 days' "
        "AND NOT EXISTS (SELECT 1 FROM resumes r WHERE r.file_id = f.id) "
        "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.resume_file_id = f.id)"))
    await db.commit()
    rows = (await db.execute(text("SELECT id, storage_key FROM file_deletions WHERE done_at IS NULL ORDER BY id LIMIT 1000"))).all()
    root = s.data_dir.resolve()
    done = []
    for fid, key in rows:
        path = (root / key).resolve()
        if root not in path.parents:  # never follow a key outside data_dir
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue  # retried next run
        done.append(fid)
    if done:
        await db.execute(text("UPDATE file_deletions SET done_at = now() WHERE id = ANY(:ids)"), {"ids": done})
    await db.commit()
    return len(done)
