"""Interview acceptance tests through the HTTP API (handoff step 4; TRD 06 §15): AC-08 prechecks, AC-11 timeout /
warning / no answer, AC-12 follow-up limits, AC-13 reconnect + stale turn, AC-14 capacity. WebRTC parts use a
real aiortc client peer that connects through POST …/rtc/offer; AI is FakeAIClient.
"""
import asyncio
from datetime import timedelta

import pytest
from aiortc import RTCSessionDescription

from app.core.constants import CAPACITY_FULL_MESSAGE
from app.voice import registry
from tests.helpers import candidate
from tests.test_handlers import drain, q, setup_app
from tests.test_voice import Client

BASE = "/api/candidate/applications/{aid}"


def url(app_id: str, path: str) -> str:
    return BASE.format(aid=app_id) + path


async def connect_http(cand, c: Client, purpose: str) -> dict:
    await c.pc.setLocalDescription(await c.pc.createOffer())
    r = await cand.post(url(c.app_id, "/rtc/offer"), json={"purpose": purpose, "sdp": c.pc.localDescription.sdp,
                                                            "type": "offer"})
    assert r.status_code == 200, r.text
    await c.pc.setRemoteDescription(RTCSessionDescription(sdp=r.json()["sdp"], type="answer"))
    return r.json()


async def prepared(client, make_client, **posting) -> tuple[str, object]:
    _, app_id, cand = await setup_app(client, make_client, **posting)
    r = await cand.post(url(app_id, "/interview/prepare"))
    assert r.status_code == 200 and r.json()["attempt_status"] == "PREPARING", r.text
    await drain()  # compose_questions + tts_pregen
    return app_id, cand


async def mark_prechecked(app_id: str) -> None:
    await q("UPDATE interviews SET precheck_media_ok_at=now(), precheck_webrtc_ok_at=now() WHERE application_id=:a "
            "RETURNING 1", a=app_id)


async def started(client, make_client) -> tuple[str, object]:
    app_id, cand = await prepared(client, make_client)
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200 and r.json()["attempt_status"] == "INTERVIEWING", r.text
    return app_id, cand


async def speak(c: Client, seconds: float) -> None:
    c.mic.speaking = True
    await asyncio.sleep(seconds)
    c.mic.speaking = False


# ── AC-08 ──────────────────────────────────────────────────────────────────────
async def test_ac08_start_needs_media_and_server_verified_webrtc(client, make_client, voice_ai, monkeypatch):
    _, app_id, cand = await setup_app(client, make_client)
    assert (await cand.post(url(app_id, "/interview/prepare"))).json()["questions_ready"] is False
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUESTIONS_NOT_READY"
    await drain()
    await q("UPDATE interviews SET precheck_media_ok_at=NULL, precheck_webrtc_ok_at=NULL WHERE application_id=:a "
            "RETURNING 1", a=app_id)

    async def start_code() -> str:
        r = await cand.post(url(app_id, "/interview/start"))
        return r.json()["error"]["code"] if r.status_code != 200 else "OK"

    assert await start_code() == "PRECHECK_REQUIRED"
    assert (await cand.post(url(app_id, "/precheck/media"), json={"mic_ok": True, "camera_ok": True})).status_code == 204
    # Screen spec SC-A04 (supersedes the PRD AC-08 WebRTC gate): the camera/mic/speaker check is enough to start;
    # the voice link is verified when the interview session connects. The server utterance check stays optional.

    # Interview connection is refused while PREPARING; video m-lines are refused; utterance needs a connection.
    c = Client(app_id)
    await c.pc.setLocalDescription(await c.pc.createOffer())
    sdp = c.pc.localDescription.sdp
    r = await cand.post(url(app_id, "/rtc/offer"), json={"purpose": "INTERVIEW", "sdp": sdp, "type": "offer"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "STATE_CONFLICT"
    r = await cand.post(url(app_id, "/rtc/offer"), json={"purpose": "PRECHECK", "type": "offer",
                                                         "sdp": sdp + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "SDP_INVALID"
    r = await cand.post(url(app_id, "/precheck/utterance"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "PRECHECK_NOT_CONNECTED"
    monkeypatch.setattr(registry, "MAX_PRECHECK_SESSIONS", 0)
    r = await cand.post(url(app_id, "/rtc/offer"), json={"purpose": "PRECHECK", "sdp": sdp, "type": "offer"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "PRECHECK_BUSY"  # C4 server-wide cap
    monkeypatch.undo()
    await c.pc.close()

    c = Client(app_id)
    await connect_http(cand, c, "PRECHECK")
    await c.expect("hello")
    assert (await cand.post(url(app_id, "/precheck/utterance"))).status_code == 202
    assert (await c.expect("precheck_result"))["reason"] == "NO_SPEECH"  # silence is reported as such
    voice_ai.stt_texts.append("안녕하세요 들리시나요")
    assert (await cand.post(url(app_id, "/precheck/utterance"))).status_code == 202
    await c.expect("precheck_listen")
    await speak(c, 1.0)
    res = await c.expect("precheck_result")
    assert res["ok"] and res["text"] == "안녕하세요 들리시나요"
    await c.pc.close()

    voice_ai.down.add("llm")
    assert await start_code() == "AI_UNAVAILABLE"  # INT-14: LLM/STT down blocks the start
    voice_ai.down.clear()
    h = (await cand.get("/api/system/ai-health")).json()
    assert h == {"llm": True, "stt": True, "tts": True, "can_start": True}
    assert await start_code() == "OK"


# ── AC-14 ──────────────────────────────────────────────────────────────────────
async def test_ac14_capacity_cap_with_fixed_message(client, make_client, voice_ai):
    ready = []
    for _ in range(3):
        rc = make_client()
        app_id, cand = await prepared(rc, make_client)
        await mark_prechecked(app_id)
        ready.append((app_id, cand))
    results = await asyncio.gather(*(cand.post(url(a, "/interview/start")) for a, cand in ready))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 200, 409]  # max_concurrent_interviews = 2 (8.3)
    refused = next(r for r in results if r.status_code == 409)
    assert refused.json()["error"] == {**refused.json()["error"], "code": "CAPACITY_FULL", "message": CAPACITY_FULL_MESSAGE}
    loser = ready[[r.status_code for r in results].index(409)][0]
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=loser) == [("READY",)]  # start later


# ── AC-12 (text mode, so no audio timing is involved) ──────────────────────────
async def test_ac12_followups_at_most_one_per_main_and_four_total(client, make_client, voice_ai):
    app_id, cand = await started(client, make_client)
    voice_ai.followup = "ASK"  # the model would ask after every answer
    r = await cand.post(url(app_id, "/interview/text-mode"))
    assert r.status_code == 200 and r.json()["mode"] == "TEXT"  # no live voice link for 20 s (T12)
    seen = []
    for _ in range(20):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        if st["attempt_status"] != "INTERVIEWING":
            break
        t = st["turn"]
        seen.append((t["main_no"], t["is_followup"], t["limit_sec"]))
        r = await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"),
                            json={"text": f"{t['main_no']}번 질문에는 결제 API의 지연을 캐시로 줄인 경험으로 답하겠습니다."})
        assert r.status_code == 202, r.text
    followups = [m for m, fu, _ in seen if fu]
    assert followups == [1, 2]  # one per main, at most the posting's follow-up count (default 2, SC-R07)
    assert [m for m, fu, _ in seen if not fu] == list(range(1, 6))
    # Every question is spoken and uses the posting's 60 s.
    assert all(limit == (45 if fu else 60) for _m, fu, limit in seen)
    st = (await cand.get(url(app_id, "/interview/state"))).json()
    assert st["attempt_status"] == "SUBMITTED" and st["answered_main"] == 5
    assert await q("SELECT DISTINCT input_mode FROM interview_answers WHERE application_id=:a", a=app_id) == [("TEXT",)]
    assert await q("SELECT followups_used FROM interviews WHERE application_id=:a", a=app_id) == [(2,)]
    # SC-A07 면접 요약: counts only, owner only.
    sm = (await cand.get(url(app_id, "/interview/summary"))).json()
    assert {k: sm[k] for k in ("main_count", "answered_main", "followups_used", "interjections", "gaze_warnings",
                               "share_result")} == {"main_count": 5, "answered_main": 5, "followups_used": 2,
                                                    "interjections": 0, "gaze_warnings": 0, "share_result": True}
    assert sm["duration_sec"] is not None and "score" not in str(sm)
    stranger = make_client()
    await candidate(stranger)
    assert (await stranger.get(url(app_id, "/interview/summary"))).status_code == 404


# ── AC-11 (fake clock) ─────────────────────────────────────────────────────────
async def test_ac11_timeout_finalizes_with_warning_and_silence_is_no_answer(client, make_client, voice_ai, monkeypatch):
    app_id, cand = await started(client, make_client)
    import app.voice.session as vs

    real = vs.utcnow
    monkeypatch.setattr(vs, "utcnow", lambda: real() + timedelta(seconds=58))  # 60 s window ends ~3 s from now
    voice_ai.stt_texts.append("시간이 다 되도록 설명한 답변입니다")
    c = Client(app_id)
    await connect_http(cand, c, "INTERVIEW")
    q1 = await c.expect("question")
    await c.expect("answer_opened")
    await speak(c, 0.8)
    assert (await c.expect("answer_warn"))["remaining_sec"] == 10
    fin = await c.expect("answer_finalized", timeout=10)
    assert fin["turn_id"] == q1["turn_id"] and fin["reason"] == "timeout" and fin["text"] == "시간이 다 되도록 설명한 답변입니다"
    q2 = await c.expect("question")
    await c.expect("answer_opened")
    fin = await c.expect("answer_finalized", timeout=10)  # silence until the deadline -> 답변 없음
    assert fin["turn_id"] == q2["turn_id"] and fin["reason"] == "timeout" and fin["no_answer"] is True
    rows = await q("SELECT q.seq, a.end_reason, a.is_no_answer FROM interview_answers a JOIN interview_questions q "
                   "ON q.id=a.question_id WHERE a.application_id=:a ORDER BY q.seq", a=app_id)
    assert rows == [(1, "TIMEOUT", False), (2, "TIMEOUT", True)]
    await c.pc.close()


# ── AC-13 / C3 ─────────────────────────────────────────────────────────────────
async def test_ac13_reconnect_resumes_at_the_cut_question_and_rejects_stale_turns(client, make_client, voice_ai):
    app_id, cand = await started(client, make_client)
    voice_ai.stt_texts.extend(["첫 번째 질문에 대한 확정된 답변입니다", "다시 연결한 뒤의 두 번째 답변입니다"])
    c = Client(app_id)
    await connect_http(cand, c, "INTERVIEW")
    q1 = await c.expect("question")
    await c.expect("answer_opened")
    await speak(c, 0.8)
    await c.expect("caption_segment")
    r = await cand.post(url(app_id, f"/interview/turns/{q1['turn_id']}/done"))
    assert r.status_code == 202 and r.json()["state"] == "FINALIZING"
    assert (await c.expect("answer_finalized"))["text"] == "첫 번째 질문에 대한 확정된 답변입니다"
    r = await cand.post(url(app_id, f"/interview/turns/{q1['turn_id']}/done"))
    assert r.status_code == 202 and r.json()["state"] == "CLOSED"  # idempotent for a finished turn

    q2 = await c.expect("question")
    assert q2["main_no"] == 2
    await c.expect("answer_opened")
    await c.pc.close()  # connection lost while answering Q2
    for _ in range(100):
        if (await cand.get(url(app_id, "/interview/state"))).json()["attempt_status"] == "INTERRUPTED":
            break
        await asyncio.sleep(0.1)
    st = (await cand.get(url(app_id, "/interview/state"))).json()
    assert st["attempt_status"] == "INTERRUPTED" and st["slot_grace_until"] is not None

    r = await cand.post(url(app_id, "/interview/resume"))
    assert r.status_code == 200 and r.json()["attempt_status"] == "INTERVIEWING"
    c2 = Client(app_id)
    await connect_http(cand, c2, "INTERVIEW")
    assert (await c2.expect("hello"))["resume"] == {"main_no": 2, "is_followup": False}
    q2b = await c2.expect("question")
    assert q2b["main_no"] == 2 and q2b["presentation_no"] == 2 and q2b["turn_id"] != q2["turn_id"]
    await c2.expect("answer_opened")
    # The [답변 완료] queued while offline arrives now with the old turn id: it must not close the new turn (C3).
    r = await cand.post(url(app_id, f"/interview/turns/{q2['turn_id']}/done"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_TURN"
    await speak(c2, 0.8)
    await c2.expect("caption_segment")
    assert (await cand.post(url(app_id, f"/interview/turns/{q2b['turn_id']}/done"))).status_code == 202
    assert (await c2.expect("answer_finalized"))["text"] == "다시 연결한 뒤의 두 번째 답변입니다"
    rows = await q("SELECT q.seq, a.transcript, a.reask_count FROM interview_answers a JOIN interview_questions q "
                   "ON q.id=a.question_id WHERE a.application_id=:a ORDER BY q.seq", a=app_id)
    assert rows == [(1, "첫 번째 질문에 대한 확정된 답변입니다", 0), (2, "다시 연결한 뒤의 두 번째 답변입니다", 1)]
    await c2.pc.close()


@pytest.fixture(autouse=True)
async def _close_sessions():
    yield
    await registry.close_all()
    await asyncio.sleep(0)



# ── a voice session that keeps failing must leave a usable text interview (INT-18) ─────────────
async def test_text_mode_asks_a_question_that_used_up_its_voice_attempts(client, make_client, voice_ai):
    app_id, cand = await started(client, make_client)
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    first = (await cand.post(url(app_id, "/interview/next"))).json()["turn"]
    await q("UPDATE interview_turns SET state='ABANDONED', end_reason='DISCONNECT', closed_at=now() WHERE id=:t RETURNING 1",
            t=first["turn_id"])
    qid = (await q("SELECT question_id FROM interview_turns WHERE id=:t", t=first["turn_id"]))[0][0]
    for no in (2, 3):
        await q("INSERT INTO interview_turns (application_id, question_id, presentation_no, input_mode, state, end_reason, closed_at) "
                "VALUES (:a, :q, :n, 'VOICE', 'ABANDONED', 'DISCONNECT', now()) RETURNING 1", a=app_id, q=qid, n=no)

    st = (await cand.post(url(app_id, "/interview/next"))).json()
    t = st["turn"]
    assert t is not None and t["phase"] == "ANSWERING" and t["main_no"] == first["main_no"]
    r = await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"),
                        json={"text": "결제 API의 지연을 캐시로 줄인 경험으로 답하겠습니다. 응답 시간을 절반으로 줄였습니다."})
    assert r.status_code == 202, r.text
    rows = await q("SELECT count(*), max(presentation_no) FROM interview_turns WHERE question_id=:q", q=qid)
    assert rows == [(3, 3)]
    following = (await cand.post(url(app_id, "/interview/next"))).json()["turn"]
    assert following is not None and following["turn_id"] != t["turn_id"]


async def test_failing_to_cache_the_interjection_keeps_the_voice_session(monkeypatch):
    from types import SimpleNamespace

    from app.voice import session as vs

    class Full:
        async def get(self, key):
            return None

        async def put(self, *a):
            raise PermissionError("tts/interject-v1.pcm")

    class Ai:
        async def tts(self, text):
            return SimpleNamespace(pcm48k=b"audio")

    monkeypatch.setattr(vs, "get_storage", lambda: Full())
    monkeypatch.setattr(vs, "get_ai_client", lambda: Ai())
    assert await vs.VoiceSession._interject_audio(SimpleNamespace(app_id="a")) == b"audio"


async def test_stopping_a_test_posting_interview_starts_over_with_new_questions(client, make_client, voice_ai, monkeypatch):
    from app.core.config import get_settings

    app_id, cand = await started(client, make_client)
    pid = (await q("SELECT posting_id FROM applications WHERE id=:a", a=app_id))[0][0]
    old_set = (await q("SELECT question_set_id FROM interviews WHERE application_id=:a", a=app_id))[0][0]

    # Every other posting keeps the one-attempt rule.
    r = await cand.post(url(app_id, "/interview/retake"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "RETAKE_NOT_ALLOWED"
    assert (await cand.get(url(app_id, "/interview/state"))).json()["retake_allowed"] is False

    monkeypatch.setattr(get_settings(), "retake_posting_ids", str(pid))
    assert (await cand.get(url(app_id, "/interview/state"))).json()["retake_allowed"] is True
    stranger = make_client()
    await candidate(stranger)
    assert (await stranger.post(url(app_id, "/interview/retake"))).status_code == 404

    r = await cand.post(url(app_id, "/interview/retake"))
    assert r.status_code == 200 and r.json()["attempt_status"] == "READY", r.text
    row = (await q("SELECT attempt_status, answered_main_count, resume_snapshot IS NOT NULL FROM applications WHERE id=:a", a=app_id))[0]
    assert tuple(row) == ("READY", 0, True)
    assert (await q("SELECT count(*) FROM interview_questions WHERE application_id=:a", a=app_id))[0][0] == 0

    # The next attempt runs the normal path and composes a new set.
    r = await cand.post(url(app_id, "/interview/prepare"))
    assert r.status_code == 200, r.text
    await drain()
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200 and r.json()["answered_main"] == 0, r.text
    assert (await q("SELECT question_set_id FROM interviews WHERE application_id=:a", a=app_id))[0][0] != old_set
    # A submitted-style state cannot be reset: READY has nothing to retake.
    await cand.post(url(app_id, "/interview/retake"))
    assert (await cand.post(url(app_id, "/interview/retake"))).status_code == 409


async def test_live_coding_turn_is_not_listened_to_and_gets_no_followup(client, make_client, voice_ai):
    from uuid import UUID

    from app.interview import turns
    from app.voice.session import Gate
    from app.core.timeutil import utcnow

    assert Gate(None, utcnow(), utcnow(), listening=False).listening is False
    app_id, _ = await started(client, make_client)
    row = (await q("SELECT id, question_set_id, company_id FROM interview_questions WHERE application_id=:a AND seq=1", a=app_id))[0]
    code_q = (await q("INSERT INTO interview_questions (question_set_id, application_id, company_id, seq, role, slot_type, text, "
                      "answer_mode, answer_limit_sec, think_sec, source) SELECT question_set_id, application_id, company_id, 6, role, "
                      "slot_type, 'code', 'CODE', answer_limit_sec, think_sec, source FROM interview_questions WHERE id=:i RETURNING id",
                      i=row[0]))[0][0]
    turn = (await q("INSERT INTO interview_turns (question_id, application_id, input_mode, state, presentation_no) "
                    "VALUES (:q, :a, 'TEXT', 'CLOSED', 1) RETURNING id", q=code_q, a=app_id))[0][0]
    ans = (await q("INSERT INTO interview_answers (turn_id, question_id, application_id, company_id, input_mode, end_reason, "
                   "is_no_answer, stt_failed, interjected, reask_count, eval_text, code_text, opened_at, confirmed_at) VALUES (:t, :q, :a, :c, 'TEXT', 'BUTTON', false, false, false, 0, 'x = 1  # 설명', 'x = 1  # 설명', "
                   "now(), now()) RETURNING id", t=turn, q=code_q, a=app_id, c=row[2]))[0][0]
    assert await turns.decide_followup(UUID(app_id), ans) is None
    assert (await q("SELECT count(*) FROM interview_questions WHERE parent_id=:q", q=code_q))[0][0] == 0


# ── live composition (ADR-0022) ────────────────────────────────────────────────
async def test_live_composition_writes_one_question_first_and_each_next_from_the_answers(client, make_client, voice_ai, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "question_mode", "live")
    seen_queries: list = []
    real = voice_ai.embed

    async def embed(texts):
        seen_queries.extend(texts)
        return await real(texts)
    monkeypatch.setattr(voice_ai, "embed", embed)
    app_id, cand = await started(client, make_client)
    # Before the interview: the plan and the first question only.
    assert await q("SELECT count(*) FROM interview_questions WHERE application_id=:a", a=app_id) == [(1,)]
    (plan,) = (await q("SELECT plan FROM question_sets WHERE application_id=:a AND status='READY'", a=app_id))[0]
    assert len(plan) == 5 and plan[0]["seq"] == 1
    voice_ai.followup = "SKIP"
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    mains = []
    for n in range(12):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        if st["attempt_status"] != "INTERVIEWING":
            break
        t = st["turn"]
        mains.append(t["main_no"])
        # the rows grow with the interview: never a question ahead of the one being asked
        assert await q("SELECT max(seq) FROM interview_questions WHERE application_id=:a AND role='MAIN'", a=app_id) == [(t["main_no"],)]
        answer = f"정산 배치를 파티션 단위로 나눠 병렬화한 경험이 {n}번째로 떠오릅니다. 처리 시간을 크게 줄였습니다."
        r = await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"), json={"text": answer})
        assert r.status_code == 202, r.text
    assert mains == [1, 2, 3, 4, 5]
    st = (await cand.get(url(app_id, "/interview/state"))).json()
    assert st["attempt_status"] == "SUBMITTED" and st["answered_main"] == 5
    rows = await q("SELECT seq, slot_type FROM interview_questions WHERE application_id=:a AND role='MAIN' ORDER BY seq", a=app_id)
    assert [s for s, _ in rows] == [1, 2, 3, 4, 5] and [a for _, a in rows] == [p["area"] for p in plan]  # the plan's areas, in order
    assert any("정산 배치를 파티션 단위로" in x for x in seen_queries)  # the answers steered the document search


async def test_live_composition_falls_back_to_the_bank_when_generation_fails(client, make_client, voice_ai, monkeypatch):
    from app.core.config import get_settings
    from app.questions import compose as C
    monkeypatch.setattr(get_settings(), "question_mode", "live")
    app_id, cand = await started(client, make_client)

    async def broken(*a, **k):
        raise RuntimeError("model gave up")
    for name in ("_fill_resume", "_fill_generated_kind", "_fill_bank_only"):
        monkeypatch.setattr(C, name, broken)
    voice_ai.followup = "SKIP"
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    for _ in range(12):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        if st["attempt_status"] != "INTERVIEWING":
            break
        t = st["turn"]
        await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"), json={"text": "결제 API 응답 지연을 캐시로 줄였습니다. 원인은 조회 쿼리였습니다."})
    st = (await cand.get(url(app_id, "/interview/state"))).json()
    assert st["attempt_status"] == "SUBMITTED" and st["answered_main"] == 5
    sources = await q("SELECT source FROM interview_questions WHERE application_id=:a AND role='MAIN' AND seq > 1", a=app_id)
    assert {s for (s,) in sources} == {"BANK_FALLBACK"}
