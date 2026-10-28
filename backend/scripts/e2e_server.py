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
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PG = "postgresql://aihire:aihire_dev_only@127.0.0.1:55432"
DB = "aihire_e2e"
os.environ["AIHIRE_DATABASE_URL"] = f"postgresql+asyncpg://aihire:aihire_dev_only@127.0.0.1:55432/{DB}"
os.environ.setdefault("AIHIRE_ENV", "dev")
os.environ.setdefault("AIHIRE_DATA_DIR", str(BACKEND / ".e2e-data"))
sys.path.insert(0, str(BACKEND))

import asyncpg
import httpx
import uvicorn
from sqlalchemy import text

RECRUITER_EMAIL = "rec@e2e.example"
PASSWORD = "Str0ng-pass!"
PRECHECK_TEXT = "안녕하세요"
ANSWER_TEXT = "주문 API의 응답 지연을 복합 인덱스와 배치 조회로 줄였고 평균 응답 시간이 절반으로 줄었습니다."


async def _recreate_db() -> None:
    conn = await asyncpg.connect(f"{PG}/postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {DB}")
    finally:
        await conn.close()


def recreate_db() -> None:
    asyncio.run(_recreate_db())
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})


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


async def seed(app) -> None:
    from app.cli import create_company, create_recruiter
    from app.core.db import session_scope

    async with session_scope() as db:
        cid = await create_company(db, name="이투이테크", intro="사내 결제 플랫폼을 만드는 회사입니다.")
        await create_recruiter(db, company_id=cid, email=RECRUITER_EMAIL, name="담당자", password=PASSWORD)
        pid = (await db.execute(text("SELECT id FROM coding_problems ORDER BY id LIMIT 1"))).scalar_one()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://e2e",
                                 headers={"Origin": "http://localhost:5173"}) as rc:
        r = await rc.post("/api/auth/login", json={"role": "RECRUITER", "email": RECRUITER_EMAIL, "password": PASSWORD})
        r.raise_for_status()
        rc.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        body = {"title": "E2E 백엔드 개발자", "job_family": "BACKEND", "requirements": "Python 웹 서비스 개발 경험",
                "preferred": "대용량 트래픽 경험", "duties": "주문·결제 API 개발", "headcount": 1,
                "deadline_date": (date.today() + timedelta(days=14)).isoformat(), "coding_enabled": True,
                "coding_problem_id": str(pid), "coding_time_limit_min": 30}
        r = await rc.post("/api/recruiter/postings", json=body)
        r.raise_for_status()
        r = await rc.post(f"/api/recruiter/postings/{r.json()['id']}/publish")
        r.raise_for_status()


async def serve(port: int) -> None:
    install_fakes()
    from app.jobs import runtime
    from app.main import create_app

    app = create_app()
    await seed(app)

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
    recreate_db()
    asyncio.run(serve(port))
