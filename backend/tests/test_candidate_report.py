"""결과 리포트 (SC-A08): the applicant reads the report as soon as the evaluation is done, whatever the recruiter decides."""
import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.notify import mail  # noqa: F401
from tests.helpers import recruiter
from tests.test_handlers import ANSWERS, answer_all, drain, fake_ai, prepare_and_compose, q, setup_app, submit_and_register  # noqa: F401

pytestmark = pytest.mark.integration
RECRUITER_ONLY = {"ai_reference", "versions", "model", "prompt", "notes", "memo", "decided_by", "decision"}


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj)) if obj else set()
    return set()


async def _evaluated(client, make_client):
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs)
    await submit_and_register(app_id)
    await drain()
    return app_id, cand


async def test_report_is_not_ready_before_the_evaluation_and_open_after_it(client, make_client, fake_ai):  # noqa: F811
    _, app_id, cand = await setup_app(client, make_client)
    r = await cand.get(f"/api/candidate/applications/{app_id}/report")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPORT_NOT_READY"

    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs)
    await submit_and_register(app_id)
    await drain()
    assert (await q("SELECT decision_status FROM applications WHERE id=:a", a=app_id))[0][0] == "UNDECIDED"  # nobody decided

    r = await cand.get(f"/api/candidate/applications/{app_id}/report")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["total"] == 52.0 and rep["grade"] == "노력 필요" and rep["grade_kind"] == "bad"
    assert [(c["label"], c["score"], c["weight"]) for c in rep["criteria"]][0][1] == 52
    assert rep["main_count"] == 5 and rep["followup_count"] == 0 and rep["summary"]
    assert rep["strengths"][0]["rank"] == 1 and rep["improvements"]
    assert [c["label"] for c in rep["chips"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"][:len(rep["chips"])]
    assert len(rep["questions"]) == 5 and rep["questions"][0]["answer"]["text"] == ANSWERS[0]
    assert rep["questions"][4]["answer"]["no_answer"] is True  # the silent one: shown as 답변 없음, not dropped
    assert [a["key"] for a in rep["areas"]] == ["LOGIC", "DELIVERY", "ATTITUDE", "EXPERTISE"]
    assert rep["media"]["audio_deleted"] is False and rep["notice"].startswith("AI 분석 결과는")
    assert not (_keys(rep) & RECRUITER_ONLY), _keys(rep) & RECRUITER_ONLY  # what is for the company stays there
    # The decision is a different screen: still nothing published.
    res = (await cand.get(f"/api/candidate/applications/{app_id}/result")).json()
    assert res["decision"] == "UNDECIDED" and res["feedback"] is None


async def test_report_highlights_are_pieces_of_the_answer(client, make_client, fake_ai):  # noqa: F811
    def judge(inp, messages):  # strong logic, weak delivery on every answer
        reply = fake_ai._default("evaluate_answer", inp)
        reply["logic"]["score"], reply["delivery"]["score"] = 5, 1
        return reply

    fake_ai.responses["evaluate_answer"] = judge
    app_id, cand = await _evaluated(client, make_client)
    a = (await cand.get(f"/api/candidate/applications/{app_id}/report")).json()["questions"][0]["answer"]
    assert {h["kind"] for h in a["highlights"]} == {"good", "bad"}
    for h in a["highlights"]:
        assert a["text"][h["start"]:h["end"]]
    assert {e["kind"] for e in a["evidence"]} == {"good", "bad"} and a["good"] and a["fix"]
    assert a["score"] == round((3 + 5 + 3 + 1) / 4 * 20)


async def test_only_the_owner_reads_it_and_the_company_has_its_own_screen(client, make_client, fake_ai):  # noqa: F811
    app_id, cand = await _evaluated(client, make_client)
    other = make_client()
    from tests.helpers import candidate
    await candidate(other)
    assert (await other.get(f"/api/candidate/applications/{app_id}/report")).status_code == 404
    assert (await client.get(f"/api/candidate/applications/{app_id}/report")).status_code == 403  # a recruiter session
    anon = make_client()
    assert (await anon.get(f"/api/candidate/applications/{app_id}/report")).status_code == 401
    rec2 = make_client()
    await recruiter(rec2)
    assert (await rec2.get(f"/api/candidate/applications/{app_id}/report")).status_code == 403

    # SC-R10: the company of the application reads the same report plus the AI reference; another company and applicants cannot
    mine = (await client.get(f"/api/recruiter/applications/{app_id}/report-view")).json()
    theirs = (await cand.get(f"/api/candidate/applications/{app_id}/report")).json()
    assert mine["ai_reference"] in ("PASS", "HOLD", "FAIL") and mine["total"] == theirs["total"] and mine["questions"] == theirs["questions"]
    assert "ai_reference" not in theirs  # never in the applicant's copy
    assert (await rec2.get(f"/api/recruiter/applications/{app_id}/report-view")).status_code == 404
    assert (await cand.get(f"/api/recruiter/applications/{app_id}/report-view")).status_code == 403


async def test_applicant_can_delete_the_audio_now_and_the_scores_stay(client, make_client, fake_ai):  # noqa: F811
    app_id, cand = await _evaluated(client, make_client)
    await _with_audio(app_id)
    assert (await cand.get(f"/api/candidate/applications/{app_id}/report")).json()["media"]["audio_deleted"] is False
    assert (await cand.post(f"/api/candidate/applications/{app_id}/media/delete")).status_code == 204
    rep = (await cand.get(f"/api/candidate/applications/{app_id}/report")).json()
    assert rep["media"]["audio_deleted"] is True and rep["total"] == 52.0 and rep["questions"][0]["answer"]["text"]
    assert (await q("SELECT count(*) FROM interview_answers WHERE application_id=:a AND audio_key IS NOT NULL", a=app_id))[0][0] == 0
    assert (await q("SELECT count(*) FROM file_deletions WHERE reason='CANDIDATE_REQUEST'"))[0][0] >= 1
    other = make_client()
    from tests.helpers import candidate
    await candidate(other)
    assert (await other.post(f"/api/candidate/applications/{app_id}/media/delete")).status_code == 404


async def _with_audio(app_id: str) -> None:
    async with session_scope() as db:
        await db.execute(text("SET LOCAL app.purge = 'on'"))  # answers are immutable; the retention job is allowed to touch audio
        await db.execute(text("UPDATE interview_answers SET input_mode = 'VOICE', audio_duration_ms = 4000, "
                              "audio_key = 'answers/' || id::text || '.wav' "
                              "WHERE application_id = :a AND NOT is_no_answer"), {"a": app_id})


async def test_reports_of_evaluations_from_before_the_rename_still_open(client, make_client, fake_ai):  # noqa: F811
    """Rows written before migration 0019 say SPECIFICITY; both report screens read them as the fourth area."""
    app_id, cand = await _evaluated(client, make_client)
    async with session_scope() as db:
        await db.execute(text("SET LOCAL app.purge = 'on'"))
        await db.execute(text("UPDATE answer_evaluations SET criterion = 'SPECIFICITY' WHERE criterion = 'ATTITUDE' "
                              "AND run_id = (SELECT run_id FROM recruiter_reports WHERE application_id = :a)"), {"a": app_id})
        await db.execute(text("UPDATE recruiter_reports SET weights = (weights - 'attitude') || jsonb_build_object('specificity', weights->'attitude'), "
                              "insufficient_by_criterion = '{}'::jsonb WHERE application_id = :a"), {"a": app_id})
    assert (await cand.get(f"/api/candidate/applications/{app_id}/report")).status_code == 200
    assert (await client.get(f"/api/recruiter/applications/{app_id}/report-view")).status_code == 200
    r = await client.get(f"/api/recruiter/applications/{app_id}/report")
    assert r.status_code == 200 and "SPECIFICITY" not in r.text
