"""Backend for the browser E2E suite (frontend/e2e): the real app and worker with a fake AI service and a fake runner.

Usage (dev PostgreSQL on 127.0.0.1:55432):  python scripts/e2e_server.py [--port 8000]
Recreates the `aihire_e2e` database, seeds one company, one recruiter and one open posting with coding, then
serves the API on 127.0.0.1:<port> with the job worker in the same process. Never point this at real data.

Fakes: STT always "hears" a fixed sentence (Chrome's fake microphone only beeps); TTS clips are 0.4 s of silence;
the runner executes the candidate's Python locally (trusted test code only); the sandbox reports ready.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DB = "aihire_e2e"
# E2E_PG_HOST=/tmp reaches PostgreSQL over its Unix socket (the AC-22 run has no network but loopback of its own).
_PG_HOST = os.environ.get("E2E_PG_HOST", "127.0.0.1")
_PG_QS = f"?host={_PG_HOST}&port=55432" if _PG_HOST.startswith("/") else ""
_PG_NET = "" if _PG_QS else f"{_PG_HOST}:55432"
PG = f"postgresql://aihire:aihire_dev_only@{_PG_NET}"
os.environ["AIHIRE_DATABASE_URL"] = f"postgresql+asyncpg://aihire:aihire_dev_only@{_PG_NET}/{DB}{_PG_QS}"
os.environ.setdefault("AIHIRE_ENV", "dev")
os.environ.setdefault("AIHIRE_DATA_DIR", str(BACKEND / ".e2e-data"))
sys.path.insert(0, str(BACKEND))

import asyncpg
import httpx
import uvicorn

RECRUITER_EMAIL = "rec@e2e.example"
PASSWORD = "Str0ng-pass!"
PRECHECK_TEXT = "안녕하세요"
ANSWER_TEXT = "주문 API의 응답 지연을 복합 인덱스와 배치 조회로 줄였고 평균 응답 시간이 절반으로 줄었습니다."


async def _recreate_db() -> None:
    conn = await asyncpg.connect(f"{PG}/postgres{_PG_QS}")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {DB}")
    finally:
        await conn.close()


def recreate_db() -> None:
    asyncio.run(_recreate_db())
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def install_egress_log(path: str) -> None:
    """AC-22: records every name lookup and connection this process attempts outside loopback / Unix sockets."""
    import ipaddress

    local_names = {None, "", "localhost", "127.0.0.1", "::1"}

    def is_local(host) -> bool:
        if isinstance(host, bytes):
            host = host.decode()
        if host in local_names:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def hook(event: str, args: tuple) -> None:
        target = None
        if event == "socket.getaddrinfo" and not is_local(args[0]):
            host = args[0].decode() if isinstance(args[0], bytes) else args[0]
            target = f"dns {host}"
        elif event == "socket.connect" and isinstance(args[1], tuple) and not is_local(args[1][0]):
            target = f"connect {args[1][0]}:{args[1][1]}"
        if target:
            with open(path, "a", encoding="utf-8") as f:
                f.write(target + "\n")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("", encoding="utf-8")
    sys.addaudithook(hook)


def install_fakes() -> None:
    from app.ai.client import STTResult, TTSResult, set_ai_client
    from app.ai.fake import FakeAIClient
    from app.coding import runner as runner_client
    from app.interview import infra

    fake = FakeAIClient()

    async def stt(pcm16k: bytes, *, purpose: str = "answer") -> STTResult:
        said = PRECHECK_TEXT if purpose == "precheck" else ANSWER_TEXT
        return STTResult(text=said, segments=[{"start": 0.0, "end": len(pcm16k) / 32000, "text": said}],
                         model="fake-stt", device="cpu")

    async def tts(_text: str) -> TTSResult:
        return TTSResult(pcm48k=bytes(19200 * 2), duration_ms=400, voice_model="fake-tts")

    fake.stt, fake.tts = stt, tts
    set_ai_client(fake)

    def run_locally(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        results = []
        for c in body["cases"]:
            p = subprocess.run([sys.executable, "-I", "-c", body["code"]], input=c["stdin"], capture_output=True,
                               text=True, timeout=10, check=False)
            results.append({"case_id": c["case_id"], "verdict": "OK" if p.returncode == 0 else "RUNTIME_ERROR",
                            "stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode, "time_ms": 5})
        return httpx.Response(200, json={"results": results, "sandbox_fingerprint": "e2e"})

    runner_client.set_transport(httpx.MockTransport(run_locally))

    async def ready() -> bool:
        return True

    infra.sandbox_ready = ready


async def serve(port: int) -> None:
    install_fakes()
    from app.jobs import runtime
    from app.main import create_app
    from scripts.seed_demo import seed_demo

    app = create_app()
    await seed_demo(app, title="E2E 백엔드 개발자", recruiter_email=RECRUITER_EMAIL)

    async def up() -> bool:
        return True

    probes = {**runtime.default_probes(), "RUNNER": runtime.CachedProbe(up)}
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run(stop, probes=probes, idle_sec=0.3, periodic_tick=2.0))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    print(f"E2E backend ready on http://127.0.0.1:{port}", flush=True)
    try:
        await server.serve()
    finally:
        stop.set()
        await worker


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    port = ap.parse_args().port
    if os.environ.get("E2E_EGRESS_LOG"):
        install_egress_log(os.environ["E2E_EGRESS_LOG"])
    recreate_db()
    asyncio.run(serve(port))
