"""Drive an application through real state transitions (DB triggers enforce them) to a finished evaluation,
standing in for the interview + evaluation pipeline in tests that only need the outcome."""
import json
import uuid

from sqlalchemy import text

from app.core.db import session_scope

CANARY = "CANARY-QUOTE-7731"


async def to_submitted(app_id: str) -> None:
    async with session_scope() as db:
        await db.execute(text("UPDATE applications SET attempt_status='PREPARING' WHERE id=:a"), {"a": app_id})
        await db.execute(text("UPDATE applications a SET attempt_status='INTERVIEWING', eval_settings_id=p.current_settings_id, "
                              "interview_started_at=now() FROM job_postings p WHERE a.id=:a AND p.id=a.posting_id"),
                         {"a": app_id})
        await db.execute(text("UPDATE applications SET attempt_status='SUBMITTED', eval_status='PENDING', submitted_at=now(), "
                              "answered_main_count=10 WHERE id=:a"), {"a": app_id})


async def to_eval_done(app_id: str) -> str:
    await to_submitted(app_id)
    run_id = str(uuid.uuid4())
    async with session_scope() as db:
        await db.execute(text("UPDATE applications SET eval_status='RUNNING' WHERE id=:a"), {"a": app_id})
        await db.execute(text(
            "INSERT INTO evaluation_runs(id, application_id, company_id, run_no, job_id, lease_token, trigger, status, "
            "model_name, prompt_version, rubric_version, eval_settings_id, scoring_version) "
            "SELECT :r, id, company_id, 1, 0, 0, 'SUBMIT', 'SUCCEEDED', 'qwen3.5:4b', 'p1', 'rubric-v1', eval_settings_id, "
            "'score-v1' FROM applications WHERE id=:a"), {"r": run_id, "a": app_id})
        await db.execute(text(
            "INSERT INTO recruiter_reports(application_id, company_id, run_id, score_expertise, score_logic, "
            "score_specificity, score_delivery, total_score, insufficient_count, no_answer_count, "
            "insufficient_by_criterion, answered_main_count, question_scores, weights, summary, strengths, improvements) "
            "SELECT id, company_id, :r, 70, 60, 65, 80, 67.25, 1, 0, CAST(:ib AS jsonb), 10, '[]'::jsonb, "
            "CAST(:w AS jsonb), :s, CAST(:st AS jsonb), CAST(:im AS jsonb) FROM applications WHERE id=:a"),
            {"r": run_id, "a": app_id, "ib": json.dumps({"logic": 1}),
             "w": json.dumps({"expertise": 35, "logic": 25, "specificity": 25, "delivery": 15}),
             "s": f"총평 {CANARY}", "st": json.dumps([f"강점 {CANARY}"]), "im": json.dumps(["개선점"])})
        await db.execute(text(
            "INSERT INTO candidate_feedback(application_id, company_id, run_id, score_expertise, score_logic, "
            "score_specificity, score_delivery, strengths, improvements) "
            "SELECT id, company_id, :r, 70, 60, 65, 80, CAST(:st AS jsonb), CAST(:im AS jsonb) FROM applications WHERE id=:a"),
            {"r": run_id, "a": app_id, "st": json.dumps(["구체적인 사례를 들어 설명했습니다."]),
             "im": json.dumps(["결론을 먼저 말하면 더 좋습니다."])})
        await db.execute(text("UPDATE applications SET eval_status='DONE', eval_done_at=now() WHERE id=:a"), {"a": app_id})
    return run_id


async def to_on_hold(app_id: str) -> None:
    await to_submitted(app_id)
    async with session_scope() as db:
        await db.execute(text("UPDATE applications SET eval_status='RUNNING' WHERE id=:a"), {"a": app_id})
        await db.execute(text("UPDATE applications SET eval_status='ON_HOLD' WHERE id=:a"), {"a": app_id})
