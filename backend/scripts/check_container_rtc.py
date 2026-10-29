"""Checks the WebRTC path into a containerized `app` (deploy/docker-compose.yml) without a browser.

    python scripts/check_container_rtc.py [--base http://localhost:8080] [--ip 127.0.0.1]

Signs up a throwaway candidate, applies to the first open posting, opens a precheck WebRTC session through the web
proxy, then keeps ONLY the answer's candidates on `--ip` (the announced address, AIHIRE_RTC_ANNOUNCE_IPS) before
connecting. Success means the browser-facing route works: published UDP range -> container, announced address in
the SDP. Exit code 0 on success. Uses the stack's own API; creates one candidate account and one application.
"""
import argparse
import asyncio
import json
import re
import sys
import time

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

PASSWORD = "Str0ng-pass!"


def keep_only(sdp: str, ip: str) -> tuple[str, list[str]]:
    lines, kept = [], []
    for line in sdp.split("\r\n"):
        if line.startswith("a=candidate:"):
            if f" {ip} " not in line:
                continue
            kept.append(line)
        lines.append(line)
    return "\r\n".join(lines), kept


async def main(base: str, ip: str) -> int:
    async with httpx.AsyncClient(base_url=base, headers={"Origin": base}, timeout=30) as c:
        email = f"rtc-check-{int(time.time())}@check.example"
        r = await c.post("/api/auth/signup", json={"name": "점검", "email": email, "password": PASSWORD,
                                                   "password_confirm": PASSWORD, "phone": "010-0000-0000"})
        r.raise_for_status()
        r = await c.post("/api/auth/login", json={"role": "CANDIDATE", "email": email, "password": PASSWORD})
        r.raise_for_status()
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        postings = (await c.get("/api/postings")).json()
        posting = next((p for p in postings if p.get("is_open")), None)
        if posting is None:
            print("FAIL: no open posting (run scripts/seed_demo.py in the app container first)")
            return 1
        doc = (await c.get("/api/consent/current")).json()
        r = await c.post(f"/api/candidate/postings/{posting['id']}/consents",
                         json={"version": doc["version"], "answers": {i["code"]: True for i in doc["items"]}})
        r.raise_for_status()
        resume = {"name": "점검", "education": [{"school": "한국대학교", "major": "컴퓨터공학", "degree": "학사",
                                               "period": "2016-2020"}],
                  "career": [], "projects": [], "skills": [], "certificates": []}
        r = await c.post(f"/api/candidate/postings/{posting['id']}/applications",
                         json={"consent_id": r.json()["consent_id"], "resume": resume})
        r.raise_for_status()
        aid = r.json()["application_id"]
        (await c.post(f"/api/candidate/applications/{aid}/interview/prepare")).raise_for_status()

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        pc.addTransceiver("audio", direction="sendrecv")
        dc = pc.createDataChannel("control")
        hello: asyncio.Future = asyncio.get_running_loop().create_future()

        @dc.on("message")
        def on_message(raw) -> None:
            ev = json.loads(raw)
            if ev.get("type") == "hello" and not hello.done():
                hello.set_result(ev)

        await pc.setLocalDescription(await pc.createOffer())
        r = await c.post(f"/api/candidate/applications/{aid}/rtc/offer",
                         json={"purpose": "PRECHECK", "sdp": pc.localDescription.sdp, "type": "offer"})
        r.raise_for_status()
        answer, kept = keep_only(r.json()["sdp"], ip)
        all_cands = re.findall(r"a=candidate:\S+ \d+ udp \d+ (\S+) (\d+)", r.json()["sdp"])
        print(f"answer candidates: {[f'{h}:{p}' for h, p in all_cands]}")
        if not kept:
            print(f"FAIL: no candidate on {ip} — is AIHIRE_RTC_ANNOUNCE_IPS set on the app container?")
            return 1
        print(f"connecting with only: {[k.split()[4] + ':' + k.split()[5] for k in kept]}")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
        t0 = time.perf_counter()
        try:
            ev = await asyncio.wait_for(hello, 15)
        except TimeoutError:
            print(f"FAIL: no data-channel message within 15 s (ICE state {pc.iceConnectionState}) — "
                  "check the published UDP range and the announced address")
            return 1
        finally:
            await pc.close()
            await c.post(f"/api/candidate/applications/{aid}/interview/leave-prepare")
        print(f"PASS: WebRTC through {ip} in {time.perf_counter() - t0:.2f}s (server hello purpose={ev['purpose']})")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--ip", default="127.0.0.1")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.base.rstrip("/"), a.ip)))
