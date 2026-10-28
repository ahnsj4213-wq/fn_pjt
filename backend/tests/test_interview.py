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
    assert await start_code() == "PRECHECK_REQUIRED"  # media alone is not enough: WebRTC must be server-verified

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
    assert (await c.expect("precheck_result"))["reason"] == "NO_SPEECH"  # silence: still blocked
    assert await start_code() == "PRECHECK_REQUIRED"
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
    assert followups == [1, 2, 3, 4]  # 6.2: one per main, four in total
    assert [m for m, fu, _ in seen if not fu] == list(range(1, 11))
    assert all(limit == (45 if fu else 60) for _, fu, limit in seen)
    st = (await cand.get(url(app_id, "/interview/state"))).json()
    assert st["attempt_status"] == "SUBMITTED" and st["answered_main"] == 10
    assert await q("SELECT DISTINCT input_mode FROM interview_answers WHERE application_id=:a", a=app_id) == [("TEXT",)]
    assert await q("SELECT followups_used FROM interviews WHERE application_id=:a", a=app_id) == [(4,)]


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

