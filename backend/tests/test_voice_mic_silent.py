"""2026-11-09: five seconds without audio packets ended a live interview at question 5 while ICE was still connected.
With the connection up a gap only warns the candidate (MIC_SILENT); a connection that is down, or audio gone for
NO_AUDIO_HARD_SEC, still ends it (ADR-0024)."""
import time
from types import SimpleNamespace

import pytest

from app.voice import session as vs


def _session(silent_for: float, state: str):
    s = SimpleNamespace(closed=False, purpose="interview", last_rx=time.monotonic() - silent_for, last_activity=time.monotonic(),
                        pc=SimpleNamespace(connectionState=state), app_id="a", _mic_warned=False, sent=[], lost_with=None)
    s.send = lambda type_, **f: s.sent.append((type_, f))

    async def lost(cause):
        s.lost_with = cause
        s.closed = True
    s.lost = lost
    return s


async def _one_tick(s, monkeypatch):
    calls = {"n": 0}

    async def sleep(_):
        calls["n"] += 1
        if calls["n"] > 1:
            s.closed = True
    monkeypatch.setattr(vs.asyncio, "sleep", sleep)
    await vs.VoiceSession._watchdog(s)


@pytest.mark.parametrize("silent_for,state,ends", [(6, "connected", False), (6, "disconnected", True),
                                                   (vs.NO_AUDIO_HARD_SEC + 1, "connected", True), (1, "connected", False)])
async def test_audio_gap_warns_while_connected(monkeypatch, silent_for, state, ends):
    s = _session(silent_for, state)
    await _one_tick(s, monkeypatch)
    assert (s.lost_with == "NO_AUDIO") is ends
    warned = ("error", {"code": "MIC_SILENT"}) in s.sent
    assert warned is (not ends and silent_for > vs.NO_AUDIO_SEC)
