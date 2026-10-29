"""Voice runtime (TRD 06, ADR-0009) with a real aiortc client peer in the same process: the client sends a tone as
"speech" over WebRTC and reads data-channel events; the server side is the real VoiceSession (gate, VAD, captions,
finalize, follow-up, completion) over PostgreSQL with FakeAIClient for STT/TTS/LLM.
"""
import asyncio
import fractions
import json
import time
import uuid

import av
import numpy as np
import pytest
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from pydantic import ValidationError

from app.core.db import session_scope
from app.voice import dc_protocol as dcp
from app.voice import registry
from app.voice.router import check_sdp
from app.voice.vad import FRAME, EnergyVAD
from tests.test_handlers import answer_all, drain, prepare_and_compose, q, setup_app


# ── pure units ─────────────────────────────────────────────────────────────────
def tone(seconds: float, amp: float = 0.3, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)


def silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


def test_vad_segments_on_pause_and_never_on_silence_alone():
    vad = EnergyVAD()
    assert vad.feed(silence(3.0)) == []  # silence alone produces nothing (and never ends an answer)
    segs = vad.feed(np.concatenate([tone(1.0), silence(0.6), tone(0.8), silence(0.6)]))
    assert len(segs) == 2 and 1.0 <= segs[0].seconds <= 1.6
    assert vad.feed(tone(0.1)) == [] and vad.flush() is None  # 0.1 s of speech is noise (< 0.25 s)


def test_vad_splits_long_speech_at_the_quietest_frame():
    vad = EnergyVAD()
    speech = np.concatenate([tone(14.0), tone(0.02, amp=0.012), tone(2.0)])
    segs = vad.feed(speech)
    assert len(segs) == 1 and 13.5 <= segs[0].seconds <= 15.0
    rest = vad.flush()
    assert rest is not None and rest.seconds < 3.0
    assert len(speech) - len(segs[0].pcm) - len(rest.pcm) < FRAME  # nothing lost but the trailing partial frame


def test_protocol_rejects_undefined_events_and_fields():
    ev = dcp.envelope("caption_segment", 3, uuid.uuid4(), segment_no=1, text="안녕하세요")
    assert ev["v"] == 1 and ev["seq"] == 3 and ev["server_now"].endswith("Z")
    with pytest.raises(ValidationError):  # D-09: no follow-up reasons / sources / debug on candidate events
        dcp.envelope("question", 1, None, main_no=1, is_followup=False, text="q", audio="none", limit_sec=60,
                     presentation_no=1, reason="VAGUE")
    with pytest.raises(KeyError):
        dcp.envelope("debug", 1, None)
    assert set(dcp.json_schemas()) == set(dcp.SERVER_EVENTS)


def test_sdp_check_allows_audio_and_datachannel_only():
    check_sdp("v=0\nm=audio 9 UDP/TLS/RTP/SAVPF 111\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\n")
    for bad in ("m=audio 9 x\nm=video 9 x\nm=application 9 x\n", "m=application 9 x\n"):
        with pytest.raises(Exception, match="SDP_INVALID|오디오"):
            check_sdp(bad)


# ── client peer ────────────────────────────────────────────────────────────────
class Mic(MediaStreamTrack):
    """48 kHz mono tone while `speaking`, silence otherwise, paced in real time."""
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self.speaking = False
        self._pts = 0
        self._t0: float | None = None

    async def recv(self) -> av.AudioFrame:
        if self._t0 is None:
            self._t0 = time.perf_counter()
        wait = self._t0 + self._pts / 48000 - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)
        n = 960
        if self.speaking:
            t = (self._pts + np.arange(n)) / 48000
            pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
        else:
            pcm = np.zeros(n, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = 48000, self._pts, fractions.Fraction(1, 48000)
        self._pts += n
        return frame


class Client:
    def __init__(self, app_id: str) -> None:
        self.app_id = app_id
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.mic = Mic()
        self.pc.addTrack(self.mic)
        self.dc = self.pc.createDataChannel("control")
        self.events: asyncio.Queue = asyncio.Queue()
        self.log: list[dict] = []

        @self.dc.on("open")
        def on_open() -> None:
            self.dc.send(json.dumps({"type": "client_ready", "audio": "playing"}))

        @self.dc.on("message")
        def on_message(raw) -> None:
            ev = json.loads(raw)
            self.log.append(ev)
            self.events.put_nowait(ev)

        @self.pc.on("track")
        def on_track(track) -> None:
            async def drain_audio() -> None:
                while True:
                    try:
                        await track.recv()
                    except Exception:  # noqa: BLE001
                        return
            asyncio.ensure_future(drain_audio())

    async def connect(self, purpose: str) -> None:
        await self.pc.setLocalDescription(await self.pc.createOffer())
        answer, _ = await registry.open_session(uuid.UUID(self.app_id), purpose, self.pc.localDescription.sdp)
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

    async def expect(self, type_: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            ev = await asyncio.wait_for(self.events.get(), max(0.01, deadline - time.monotonic()))
            if ev["type"] == type_:
                return ev


async def start_interviewing(app_id: str) -> None:
    """What POST …/interview/start does once prechecks pass (the interview router is mounted in step 4)."""
    async with session_scope() as db:
        from sqlalchemy import text
        await db.execute(text("UPDATE applications a SET attempt_status='INTERVIEWING', eval_settings_id=p.current_settings_id, "
                              "interview_started_at=now(), answered_main_count=(SELECT count(*) FROM interview_answers x "
                              "JOIN interview_questions iq ON iq.id=x.question_id WHERE x.application_id=a.id "
                              "AND iq.role='MAIN') FROM job_postings p WHERE a.id=:a AND p.id=a.posting_id"),
                         {"a": app_id})
        await db.execute(text("UPDATE interviews SET question_set_id=(SELECT id FROM question_sets WHERE application_id=:a "
                              "AND status='READY'), started_at=now() WHERE application_id=:a"), {"a": app_id})
        await db.execute(text("INSERT INTO interview_slots(application_id, lease_expires_at) "
                              "VALUES (:a, now() + interval '120 seconds')"), {"a": app_id})


async def answer_turn(c: Client, *, speak: float = 1.0, before_open: float = 0.0) -> dict:
    """One turn from the client side: optional speech during the question audio (must be dropped), speech after
    the mic opens, a pause for the caption, then [답변 완료]."""
    q_ev = await c.expect("question")
    if before_open:
        c.mic.speaking = True
        await asyncio.sleep(before_open)
        c.mic.speaking = False
    opened = await c.expect("answer_opened")
    assert opened["turn_id"] == q_ev["turn_id"]
    if speak:
        c.mic.speaking = True
        await asyncio.sleep(speak)
        c.mic.speaking = False
        await c.expect("caption_segment")
    assert await registry.request_finish(uuid.UUID(c.app_id), uuid.UUID(q_ev["turn_id"]))
    fin = await c.expect("answer_finalized")
    assert fin["turn_id"] == q_ev["turn_id"]
    return {"question": q_ev, "finalized": fin}


async def test_interview_turns_gate_captions_followup_and_completion(client, make_client, voice_ai):
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs[:8], no_answer_seq=99)  # questions 1~8 already answered
    await start_interviewing(app_id)
    voice_ai.stt_texts.extend(["바나나 캐시를 도입해 응답 시간을 줄였습니다", "마지막 답변입니다 결제 장애를 줄였습니다"])
    asked = {"n": 0}

    def ask_once(inp, msgs):  # follow-up only for the first decision (Q9)
        asked["n"] += 1
        if asked["n"] > 1:
            return {"decision": "SKIP", "reason": "NONE", "anchor": None, "question": None}
        quote = inp["answer"].strip()[:12]
        return {"decision": "ASK", "reason": "VAGUE", "anchor": {"source": "answer", "quote": quote},
                "question": f"말씀하신 '{quote}' 부분을 구체적인 사례로 설명해 주시겠어요?"}

    voice_ai.responses["followup_decide"] = ask_once

    c = Client(app_id)
    await c.connect("interview")
    hello = await c.expect("hello")
    assert hello["purpose"] == "interview" and hello["resume"] == {"main_no": 9, "is_followup": False}

    # Q9: speech while the question plays is dropped by the server gate (AC-09), speech after opening is captioned.
    t9 = await answer_turn(c, speak=1.0, before_open=0.15)
    assert t9["question"]["main_no"] == 9 and t9["question"]["audio"] == "track"
    assert t9["finalized"]["text"] == "바나나 캐시를 도입해 응답 시간을 줄였습니다" and not t9["finalized"]["no_answer"]
    assert voice_ai.calls_count["stt"] == 1  # only the post-opening segment reached STT

    # Follow-up on Q9 (same number, 45 s), answered with silence -> 답변 없음; silence alone did not end it (AC-10).
    fu = await answer_turn(c, speak=0)
    assert fu["question"]["main_no"] == 9 and fu["question"]["is_followup"] and fu["question"]["limit_sec"] == 45
    assert fu["finalized"]["no_answer"] is True

    t10 = await answer_turn(c, speak=0.8)
    assert t10["question"]["main_no"] == 10 and t10["finalized"]["reason"] == "button"
    done = await c.expect("interview_completed")
    assert done["next"] == "SUBMITTED" and done["turn_id"] is None

    rows = await q("SELECT attempt_status, eval_status, answered_main_count FROM applications WHERE id=:a", a=app_id)
    assert rows == [("SUBMITTED", "PENDING", 10)]
    answers = await q("SELECT q.seq, q.role, a.end_reason, a.is_no_answer, a.audio_key IS NOT NULL, a.audio_duration_ms "
                      "FROM interview_answers a JOIN interview_questions q ON q.id=a.question_id "
                      "WHERE a.application_id=:a AND q.seq >= 9 ORDER BY q.seq, q.role DESC", a=app_id)
    assert [(s, r, e, n) for s, r, e, n, _, _ in answers] == [(9, "MAIN", "BUTTON", False), (9, "FOLLOWUP", "BUTTON", True),
                                                            (10, "MAIN", "BUTTON", False)]
    q9_audio_ms = answers[0][5]
    assert answers[0][4] and q9_audio_ms < 4000  # stored answer audio excludes the pre-opening speech
    # Event order inside a turn (TRD 06 §8) and every event carries turn_id + seq.
    seqs = [e["seq"] for e in c.log]
    assert seqs == sorted(seqs) and all("turn_id" in e for e in c.log)
    order = [e["type"] for e in c.log if e.get("turn_id") == t9["question"]["turn_id"]]
    assert order[:3] == ["question", "tts_end", "answer_opened"] and order[-2:] == ["answer_finalized", "processing"]
    await c.pc.close()
    await drain()  # the pre_evaluate / evaluate jobs registered by the runtime run fine with the fake AI


async def test_disconnect_while_answering_interrupts_without_saving(client, make_client, voice_ai):
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs[:9], no_answer_seq=99)
    await start_interviewing(app_id)
    c = Client(app_id)
    await c.connect("interview")
    q_ev = await c.expect("question")
    await c.expect("answer_opened")
    c.mic.speaking = True
    await asyncio.sleep(0.5)
    await c.pc.close()  # the browser goes away mid-answer
    for _ in range(100):
        if (await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id))[0][0] == "INTERRUPTED":
            break
        await asyncio.sleep(0.1)
    assert await q("SELECT attempt_status, interrupted_from FROM applications WHERE id=:a", a=app_id) == \
        [("INTERRUPTED", "INTERVIEWING")]
    assert await q("SELECT state, end_reason FROM interview_turns WHERE id=:t", t=q_ev["turn_id"]) == \
        [("ABANDONED", "DISCONNECT")]
    assert await q("SELECT 1 FROM interview_answers a JOIN interview_questions q ON q.id=a.question_id "
                   "WHERE a.application_id=:a AND q.seq=10", a=app_id) == []  # partial answer discarded (OQ-21)
    assert registry.get(uuid.UUID(app_id)) is None
    assert not await registry.request_finish(uuid.UUID(app_id), uuid.UUID(q_ev["turn_id"]))  # stale turn (C3)


async def test_hanging_up_on_completion_does_not_interrupt_coding(client, make_client, voice_ai):
    """The browser closes the connection as soon as `interview_completed` arrives; that is not a disconnect."""
    (pid,), = await q("SELECT id FROM coding_problems LIMIT 1")
    _, app_id, cand = await setup_app(client, make_client, coding_enabled=True, coding_problem_id=str(pid),
                                     coding_time_limit_min=30)
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs[:9], no_answer_seq=99)
    await start_interviewing(app_id)
    voice_ai.stt_texts.append("마지막 답변입니다")
    voice_ai.responses["followup_decide"] = lambda inp, msgs: {"decision": "SKIP", "reason": "NONE", "anchor": None,
                                                               "question": None}
    c = Client(app_id)
    await c.connect("interview")
    await answer_turn(c, speak=0.8)
    done = await c.expect("interview_completed")
    await c.pc.close()  # at once, inside the server's send-then-close window
    assert done["next"] == "CODING"
    await asyncio.sleep(1.0)
    assert await q("SELECT attempt_status, interrupted_from FROM applications WHERE id=:a", a=app_id) == [("CODING", None)]


async def test_precheck_utterance_marks_webrtc_ok(client, make_client, voice_ai):
    _, app_id, cand = await setup_app(client, make_client)
    await prepare_and_compose(cand, app_id)
    voice_ai.stt_texts.append("안녕하세요")
    c = Client(app_id)
    await c.connect("precheck")
    assert (await c.expect("hello"))["purpose"] == "precheck"
    s = registry.get(uuid.UUID(app_id))
    listen = asyncio.ensure_future(s.precheck_listen())
    await c.expect("precheck_listen")
    c.mic.speaking = True
    await asyncio.sleep(1.0)
    c.mic.speaking = False
    res = await c.expect("precheck_result", timeout=10)
    await listen
    assert res["ok"] is True and res["reason"] == "OK" and res["text"] == "안녕하세요"
    assert (await q("SELECT precheck_webrtc_ok_at IS NOT NULL FROM interviews WHERE application_id=:a", a=app_id)) == [(True,)]

    listen = asyncio.ensure_future(s.precheck_listen())  # silence -> NO_SPEECH
    res = await c.expect("precheck_result", timeout=10)
    await listen
    assert res["ok"] is False and res["reason"] == "NO_SPEECH"
    await c.pc.close()


def test_announce_adds_host_candidates_with_valid_foundations():
    from app.voice.netcfg import announce, validate_announce_ips

    f = "0123456789abcdef0123456789abcdef"  # aioice uses all 32 characters
    sdp = "\r\n".join(["v=0", f"a=candidate:{f} 1 udp 2130706431 172.18.0.5 41003 typ host",
                       "a=candidate:x 1 udp 2130706431 fe80::1 41004 typ host", "a=end-of-candidates", ""])
    out = announce(sdp, ["127.0.0.1", "192.168.0.20"]).split("\r\n")
    added = [x for x in out if " 41003 " in x and "172.18.0.5" not in x]
    assert added == [f"a=candidate:{f[:29]}a00 1 udp 2130706430 127.0.0.1 41003 typ host",
                     f"a=candidate:{f[:29]}a01 1 udp 2130706429 192.168.0.20 41003 typ host"]
    assert all(len(x.split()[0].split(":", 1)[1]) <= 32 for x in out if x.startswith("a=candidate:"))
    assert not [x for x in out if "41004" in x and "fe80" not in x]  # IPv6 candidates are not duplicated
    assert announce(sdp, []) == sdp
    for bad in (["8.8.8.8"], ["host.docker.internal"], ["::1"]):
        with pytest.raises(ValueError):
            validate_announce_ips(bad)


async def test_container_rtc_settings_pin_ports_and_still_connect(client, make_client, voice_ai):
    """deploy/docker-compose.yml: ports come from the published range and the announced address is offered too."""
    import re

    import aioice.ice

    from app.core.config import get_settings
    from app.voice import netcfg

    s = get_settings()
    saved = (s.rtc_udp_port_min, s.rtc_udp_port_max, s.rtc_announce_ips)
    s.rtc_udp_port_min, s.rtc_udp_port_max, s.rtc_announce_ips = 41000, 41009, ["10.255.255.1"]
    try:
        _, app_id, cand = await setup_app(client, make_client)
        await prepare_and_compose(cand, app_id)
        c = Client(app_id)
        await c.pc.setLocalDescription(await c.pc.createOffer())
        answer, _ = await registry.open_session(uuid.UUID(app_id), "precheck", c.pc.localDescription.sdp)
        cands = re.findall(r"a=candidate:\S+ 1 udp \d+ (\S+) (\d+) typ host", answer)
        assert cands and all(41000 <= int(port) <= 41009 for _, port in cands)
        assert {ip for ip, _ in cands} >= {"10.255.255.1"}
        await c.pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
        assert (await c.expect("hello"))["purpose"] == "precheck"
        await c.pc.close()
    finally:
        s.rtc_udp_port_min, s.rtc_udp_port_max, s.rtc_announce_ips = saved
        aioice.ice.asyncio = asyncio
        netcfg._installed = None
