"""One WebRTC session per application (TRD 06, ADR-0009). The server owns the clock and the gate.

Interview turn loop (TRD 06 §5):
  S1 present_next -> `question` + TTS on the downlink track
  S2 last TTS frame sent -> `tts_end`, 0.3 s playout guard          (no TTS -> tts_end{played:false}, open at once)
  S3 open_answer -> `answer_opened`; frames received before opened_at or after deadline + grace are dropped
  S4 deadline - 10 s -> `answer_warn`
  S5 [답변 완료] (request_finish) -> 0.3 s drain, gate closes     S6 deadline + grace -> gate closes
  S7 last VAD segment -> STT -> finalize (one transaction) -> `answer_finalized`, `processing`
  S10 follow-up decision (RT lane, answer end + 5 s) -> next turn, or finish -> `interview_completed`
Silence never ends an answer: the VAD only cuts caption segments (X-02).

Disconnect (TRD 06 §11): ICE failed/closed at once, disconnected for 5 s, no uplink audio for 5 s, or the data
channel closing. A turn being finalised still completes (S9); then the live turn is abandoned and the attempt
becomes INTERRUPTED. The slot lease is renewed every 15 s while connected; afterwards it lapses after 120 s (T10).
"""
import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import av
import numpy as np
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from sqlalchemy import text

from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import log_event
from app.core.storage import get_storage
from app.core.timeutil import utcnow
from app.interview import slots, turns
from app.models import Application, InterviewAnswer, QuestionTtsAsset
from app.voice import dc_protocol as dcp
from app.voice import netcfg
from app.voice.tts_track import TtsTrack
from app.voice.vad import RATE, EnergyVAD, Segment

log = logging.getLogger("app.voice")

PLAYOUT_GUARD_SEC = 0.3
BUTTON_DRAIN_SEC = 0.3
WARN_BEFORE_SEC = 10
CLIENT_READY_WAIT_SEC = 3.0
DC_OPEN_WAIT_SEC = 10.0
DISCONNECTED_GRACE_SEC = 5.0
NO_AUDIO_SEC = 5.0
SLOT_RENEW_SEC = 15.0
TTS_TIMEOUT_SEC = 10.0
PRECHECK_WINDOW_SEC = 6.0
PRECHECK_MIN_SPEECH_FRAMES = 15  # 0.3 s
PRECHECK_IDLE_SEC = 600.0
_WORDISH = re.compile(r"[0-9A-Za-z가-힣]")


@dataclass
class Gate:
    """The listening window of one turn (or one precheck utterance)."""
    turn_id: UUID | None
    opened_at: datetime
    close_at: datetime  # deadline + grace; moved earlier by [답변 완료] + drain
    vad: EnergyVAD = field(default_factory=EnergyVAD)
    audio: list = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    captions: list[str] = field(default_factory=list)
    attempted: int = 0
    failed: int = 0
    stt_model: str | None = None
    finalized: bool = False
    dropped_samples: int = 0

    def accepts(self, now: datetime) -> bool:
        return self.opened_at <= now <= self.close_at


class VoiceSession:
    def __init__(self, application_id: UUID, purpose: str, on_closed) -> None:
        self.app_id, self.purpose = application_id, purpose
        self.id = uuid.uuid4().hex
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # never the default public STUN (AC-22)
        self.tts = TtsTrack()
        self.pc.addTrack(self.tts)
        self.dc = None
        self.seq = 0
        self.gate: Gate | None = None
        self.dc_open = asyncio.Event()
        self.client_ready = asyncio.Event()
        self.finish = asyncio.Event()
        self.finish_reason: str | None = None
        self.finish_at: datetime | None = None
        self.finalizing = False
        self._finalize_done = asyncio.Event()
        self._finalize_done.set()
        self.closed = False
        self.superseded = False
        self.last_rx: float | None = None
        self.last_activity = time.monotonic()
        self._tasks: set[asyncio.Task] = set()
        self._runner: asyncio.Task | None = None
        self._on_closed = on_closed
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        self._wire()

    # ── setup ──
    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._task_done)
        return t

    def _task_done(self, t: asyncio.Task) -> None:
        self._tasks.discard(t)
        if t.cancelled() or t.exception() is None:
            return
        exc = t.exception()
        frames = __import__("traceback").extract_tb(exc.__traceback__)
        where = f"{frames[-1].filename.rsplit('/', 1)[-1]}:{frames[-1].lineno}" if frames else "?"
        log_event(log, "voice_task_failed", logging.ERROR, application_id=str(self.app_id),
                  error_code=type(exc).__name__, reason=where)
        if t is self._runner and not self.closed:
            self.send("error", code="SERVER_ERROR")
            self._spawn(self.lost("RUNNER_FAILED"))

    def _wire(self) -> None:
        pc = self.pc

        @pc.on("datachannel")
        def on_dc(channel) -> None:
            if channel.label != "control":
                return
            self.dc = channel
            self.dc_open.set()

            @channel.on("message")
            def on_message(raw) -> None:
                self._on_client_message(raw)

            @channel.on("close")
            def on_close() -> None:
                self._spawn(self.lost("DC_CLOSED"))

        @pc.on("track")
        def on_track(track) -> None:
            if track.kind == "audio":
                self._spawn(self._consume(track))

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            st = pc.connectionState
            if st in ("failed", "closed"):
                await self.lost(f"ICE_{st.upper()}")
            elif st == "disconnected":
                self._spawn(self._disconnected_watch())

    async def accept(self, offer_sdp: str) -> str:
        s = get_settings()
        if s.rtc_udp_port_min:
            netcfg.install_port_range(s.rtc_udp_port_min, s.rtc_udp_port_max)  # before ICE gathering binds ports
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        self._runner = self._spawn(self._run())
        self._spawn(self._watchdog())
        return netcfg.announce(self.pc.localDescription.sdp, s.rtc_announce_ips)

    # ── data channel ──
    def send(self, type_: str, turn_id=None, **fields) -> None:
        if self.dc is None or self.dc.readyState != "open":
            return
        self.seq += 1
        self.dc.send(json.dumps(dcp.envelope(type_, self.seq, turn_id, **fields), ensure_ascii=False))

    def _on_client_message(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if msg.get("type") == "ping":
            self.send("pong", client_seq=int(msg.get("client_seq") or 0), client_now=msg.get("client_now"))
        elif msg.get("type") == "client_ready":
            self.client_ready.set()

    # ── uplink audio ──
    async def _consume(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            now = utcnow()
            self.last_rx = time.monotonic()
            gate = self.gate
            if gate is None or gate.finalized:
                continue
            for f in self._resampler.resample(frame):
                pcm = f.to_ndarray().reshape(-1).astype(np.int16, copy=False)
                if not gate.accepts(now):  # server-side gate (AC-09): before opening or after the window
                    gate.dropped_samples += len(pcm)
                    continue
                gate.audio.append(pcm)
                for seg in gate.vad.feed(pcm):
                    gate.queue.put_nowait(seg)

    async def _stt_worker(self, gate: Gate, purpose: str) -> None:
        """Segments in order, one at a time; one retry per segment (TRD 06 §6)."""
        ai = get_ai_client()
        while True:
            seg: Segment | None = await gate.queue.get()
            if seg is None:
                return
            gate.attempted += 1
            result = None
            for _ in range(2):
                try:
                    result = await ai.stt(seg.pcm.astype("<i2").tobytes(), purpose=purpose)
                    break
                except Exception:  # noqa: BLE001, S112 - AIUnavailable, timeouts, HTTP errors: retry once, then failed
                    continue
            if result is None:
                gate.failed += 1
                continue
            gate.stt_model = result.model
            text_ = result.text.strip()
            if text_ and not gate.finalized:
                gate.captions.append(text_)
                if self.purpose == "interview":
                    self.send("caption_segment", gate.turn_id, segment_no=len(gate.captions), text=text_)

    # ── interview loop ──
    async def _run(self) -> None:
        try:
            await asyncio.wait_for(self.dc_open.wait(), DC_OPEN_WAIT_SEC)
        except TimeoutError:
            await self.close("NO_DATA_CHANNEL")
            return
        if self.purpose == "precheck":
            self.send("hello", session_id=self.id, purpose="precheck", resume=None)
            return
        resume = None
        async with session_scope() as db:
            app = await db.get(Application, self.app_id)
            q = await turns.next_question(db, self.app_id)
            if app.answered_main_count > 0 and q is not None:
                resume = {"main_no": q.seq, "is_followup": q.role == "FOLLOWUP"}
        self.send("hello", session_id=self.id, purpose="interview", resume=resume)
        try:
            await asyncio.wait_for(self.client_ready.wait(), CLIENT_READY_WAIT_SEC)
        except TimeoutError:
            pass  # the client cannot delay the first question indefinitely (TRD 06 §8)
        self._spawn(self._renew_slot())
        while not self.closed:
            if not await self._one_turn():
                return

    async def _one_turn(self) -> bool:
        s = get_settings()
        async with session_scope() as db:
            try:
                view = await turns.present_next(db, self.app_id)
            except turns.TextModeRequired:
                view = "TEXT"
            status = None
            if view is None:
                status = await turns.finish_interview(db, self.app_id)
        if view == "TEXT":
            self.send("error", code="TEXT_MODE_REQUIRED")
            await self.close("TEXT_MODE_REQUIRED")
            return False
        if view is None:
            # Closed before the event leaves: the client hangs up on `interview_completed`, and that hang-up must not
            # count as a disconnect (it would interrupt the coding phase that has just begun).
            self.closed = True
            self.send("interview_completed", next="CODING" if status == "CODING" else "SUBMITTED")
            await asyncio.sleep(0.2)  # let the event leave before the connection closes
            await self.close("COMPLETED")
            return False

        audio = await self._question_audio(view)
        self.send("question", view.turn_id, main_no=view.main_no, is_followup=view.is_followup, text=view.text,
                  audio="track" if audio else "none", limit_sec=view.limit_sec, presentation_no=view.presentation_no)
        if audio:
            ended = asyncio.Event()
            self.tts.play(audio, ended.set)
            await ended.wait()
        self.send("tts_end", view.turn_id, played=bool(audio))
        if audio:
            await asyncio.sleep(PLAYOUT_GUARD_SEC)

        async with session_scope() as db:
            opened_at, deadline_at = await turns.open_answer(db, view.turn_id)
        grace = timedelta(seconds=s.answer_grace_seconds)
        self.finish.clear()
        self.finish_reason = self.finish_at = None
        gate = Gate(view.turn_id, opened_at, deadline_at + grace)
        self.gate = gate
        worker = self._spawn(self._stt_worker(gate, "answer"))
        warn_at = deadline_at - timedelta(seconds=WARN_BEFORE_SEC)
        self.send("answer_opened", view.turn_id, opened_at=dcp.iso(opened_at), deadline_at=dcp.iso(deadline_at),
                  warn_at=dcp.iso(warn_at), limit_sec=view.limit_sec)
        warn = self._spawn(self._warn_at(view.turn_id, warn_at))

        try:
            await asyncio.wait_for(self.finish.wait(), max(0.0, (gate.close_at - utcnow()).total_seconds()))
            reason = self.finish_reason or "BUTTON"
        except TimeoutError:
            reason = "TIMEOUT"
        warn.cancel()
        self.finalizing = True
        self._finalize_done.clear()
        try:
            await self._mark_finalizing(view.turn_id, reason)
            if reason == "BUTTON":
                await asyncio.sleep(BUTTON_DRAIN_SEC)  # words already in flight still count
            gate.close_at = min(gate.close_at, utcnow())
            last = gate.vad.flush()
            if last is not None:
                gate.queue.put_nowait(last)
            gate.queue.put_nowait(None)
            await worker
            answer = await self._finalize(gate, reason)
        finally:
            self.finalizing = False
            self._finalize_done.set()
        self.gate = None
        if answer is None:
            return not self.closed
        self.send("processing", view.turn_id)
        if not view.is_followup:
            await turns.decide_followup(self.app_id, answer.id)
        return not self.closed

    async def _finalize(self, gate: Gate, reason: str) -> InterviewAnswer | None:
        transcript = " ".join(gate.captions)
        stt_failed = gate.attempted > 0 and gate.failed == gate.attempted
        audio_key, audio_ms = await self._store_audio(gate)
        async with session_scope() as db:
            answer = await turns.finalize(db, gate.turn_id, end_reason=reason, transcript=transcript,
                                          stt_failed=stt_failed, audio_key=audio_key, audio_ms=audio_ms,
                                          stt_model=gate.stt_model)
        gate.finalized = True
        if answer is not None:
            self.send("answer_finalized", gate.turn_id, reason=reason.lower(), text=answer.transcript or "",
                      no_answer=answer.is_no_answer)
        log_event(log, "answer_finalized", application_id=str(self.app_id), reason=reason,
                  count=len(gate.captions), status="no_answer" if not transcript else "ok")
        return answer

    async def _mark_finalizing(self, turn_id: UUID, reason: str) -> None:
        async with session_scope() as db:
            await db.execute(text("UPDATE interview_turns SET state='FINALIZING', end_reason=:r "
                                  "WHERE id=:t AND state='ANSWERING'"), {"t": turn_id, "r": reason})
            await db.execute(text("UPDATE interviews SET current_phase='FINALIZING' WHERE application_id=:a"),
                             {"a": self.app_id})

    async def _warn_at(self, turn_id: UUID, at: datetime) -> None:
        await asyncio.sleep(max(0.0, (at - utcnow()).total_seconds()))
        if self.gate is not None and self.gate.turn_id == turn_id and not self.finish.is_set():
            self.send("answer_warn", turn_id, remaining_sec=WARN_BEFORE_SEC)

    async def _question_audio(self, view: turns.TurnView) -> bytes | None:
        """Pre-synthesised main-question audio, else synthesise now; None -> text only (INT-04)."""
        if not view.is_followup:
            async with session_scope() as db:
                asset = await db.get(QuestionTtsAsset, view.question_id)
            if asset is not None:
                data = await get_storage().get(asset.audio_key)
                if data:
                    return data
        try:
            out = await asyncio.wait_for(get_ai_client().tts(view.text), TTS_TIMEOUT_SEC)
        except Exception as e:  # noqa: BLE001 - TTS down: the question is shown as text (INT-04)
            log_event(log, "tts_failed", application_id=str(self.app_id), error_code=type(e).__name__)
            return None
        return out.pcm48k or None

    async def _store_audio(self, gate: Gate) -> tuple[str | None, int | None]:
        """Gated 16 kHz audio of the answer (INT-09), kept 30 days. FLAC: lossless and supported by soundfile."""
        if not gate.audio:
            return None, None
        pcm = np.concatenate(gate.audio)
        key = f"answers/{gate.turn_id}.flac"

        def encode() -> bytes:
            import io

            import soundfile as sf

            buf = io.BytesIO()
            sf.write(buf, pcm, RATE, format="FLAC", subtype="PCM_16")
            return buf.getvalue()

        await get_storage().put(key, await asyncio.to_thread(encode), "audio/flac")
        return key, int(len(pcm) * 1000 / RATE)

    async def _renew_slot(self) -> None:
        from app.voice import registry

        while not self.closed:
            if self.pc.connectionState == "connected":
                registry.mark_seen(self.app_id)
                async with session_scope() as db:
                    await slots.renew(db, self.app_id)
                    await db.execute(text("UPDATE interviews SET last_heartbeat_at=now() WHERE application_id=:a"),
                                     {"a": self.app_id})
            await asyncio.sleep(SLOT_RENEW_SEC)

    # ── commands from the HTTPS side ──
    def request_finish(self, turn_id: UUID) -> bool:
        gate = self.gate
        now = utcnow()
        if gate is None or gate.turn_id != turn_id or gate.finalized or self.finish.is_set() or not gate.accepts(now):
            return False
        self.finish_reason, self.finish_at = "BUTTON", now
        self.finish.set()
        return True

    async def precheck_listen(self) -> None:
        """INT-17: a 6 s window through the same gate, VAD and STT path; pass marks precheck_webrtc_ok_at."""
        self.last_activity = time.monotonic()
        now = utcnow()
        gate = Gate(None, now, now + timedelta(seconds=PRECHECK_WINDOW_SEC))
        self.gate = gate
        self.send("precheck_listen", opened_at=dcp.iso(gate.opened_at), deadline_at=dcp.iso(gate.close_at))
        worker = self._spawn(self._stt_worker(gate, "precheck"))
        await asyncio.sleep(PRECHECK_WINDOW_SEC)
        last = gate.vad.flush()
        if last is not None:
            gate.queue.put_nowait(last)
        gate.queue.put_nowait(None)
        await worker
        gate.finalized = True
        self.gate = None
        heard = " ".join(gate.captions)
        if gate.vad.speech_frames_total < PRECHECK_MIN_SPEECH_FRAMES:
            ok, reason = False, "NO_SPEECH"
        elif gate.attempted and gate.failed == gate.attempted:
            ok, reason = False, "STT_UNAVAILABLE"
        elif len(_WORDISH.findall(heard)) < 2:
            ok, reason = False, "NOT_RECOGNIZED"
        else:
            ok, reason = True, "OK"
        if ok:
            async with session_scope() as db:
                await db.execute(text("UPDATE interviews SET precheck_webrtc_ok_at=now() WHERE application_id=:a"),
                                 {"a": self.app_id})
        self.send("precheck_result", ok=ok, text=heard, reason=reason)

    # ── liveness ──
    async def _disconnected_watch(self) -> None:
        await asyncio.sleep(DISCONNECTED_GRACE_SEC)
        if self.pc.connectionState == "disconnected":
            await self.lost("ICE_DISCONNECTED")

    async def _watchdog(self) -> None:
        while not self.closed:
            await asyncio.sleep(1.0)
            if self.purpose == "interview" and self.last_rx is not None and \
                    time.monotonic() - self.last_rx > NO_AUDIO_SEC:
                await self.lost("NO_AUDIO")
            elif self.purpose == "precheck" and time.monotonic() - self.last_activity > PRECHECK_IDLE_SEC:
                await self.close("PRECHECK_IDLE")

    async def lost(self, cause: str) -> None:
        """Connection lost (TRD 06 §11): finish a finalisation in progress (S9), then abandon and interrupt (S8)."""
        if self.closed or self.superseded:
            return
        self.closed = True
        if self.finalizing:
            await self._finalize_done.wait()
        if self._runner is not None and not self._runner.done():
            self._runner.cancel()
        if self.purpose == "interview":
            async with session_scope() as db:
                await turns.interrupt(db, self.app_id)
        log_event(log, "voice_lost", application_id=str(self.app_id), reason=cause)
        await self._shutdown()

    async def supersede(self) -> None:
        """A newer connection for the same application replaces this one (no interruption)."""
        if self.closed:
            return
        self.superseded = True
        self.send("session_superseded")
        await self.close("SUPERSEDED")

    async def close(self, reason: str = "") -> None:
        self.closed = True
        await self._shutdown()

    async def _shutdown(self) -> None:
        self.tts.stop()
        current = asyncio.current_task()
        for t in list(self._tasks):
            if t is not current:
                t.cancel()
        await self.pc.close()
        self._on_closed(self)

