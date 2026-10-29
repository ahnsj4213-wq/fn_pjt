"""Demo data: one company, one recruiter account and one open posting with a coding test. Idempotent.

    python scripts/seed_demo.py [--title "백엔드 개발자"]                     (venv, from backend/)
    docker compose exec app python scripts/seed_demo.py                    (container stack, from deploy/)

Recruiter login: rec@demo.example / Str0ng-pass!  (development only; operators create real accounts with app.cli)
"""
import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text

RECRUITER_EMAIL = "rec@demo.example"
PASSWORD = "Str0ng-pass!"


async def seed_demo(app, *, title: str, recruiter_email: str = RECRUITER_EMAIL) -> bool:
    """Returns False when the recruiter already exists (nothing is created twice)."""
    from app.cli import create_company, create_recruiter
    from app.core.config import get_settings
    from app.core.db import session_scope

    async with session_scope() as db:
        if (await db.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": recruiter_email})).first():
            return False
        cid = await create_company(db, name="이투이테크", intro="사내 결제 플랫폼을 만드는 회사입니다.")
        await create_recruiter(db, company_id=cid, email=recruiter_email, name="담당자", password=PASSWORD)
        pid = (await db.execute(text("SELECT id FROM coding_problems ORDER BY id LIMIT 1"))).scalar_one()
    origin = get_settings().trusted_origins[0]  # the API checks Origin like it does for the browser
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://seed",
                                 headers={"Origin": origin}) as rc:
        r = await rc.post("/api/auth/login", json={"role": "RECRUITER", "email": recruiter_email, "password": PASSWORD})
        r.raise_for_status()
        rc.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        body = {"title": title, "job_family": "BACKEND", "requirements": "Python 웹 서비스 개발 경험",
                "preferred": "대용량 트래픽 경험", "duties": "주문·결제 API 개발", "headcount": 1,
                "deadline_date": (date.today() + timedelta(days=14)).isoformat(), "coding_enabled": True,
                "coding_problem_id": str(pid), "coding_time_limit_min": 30}
        r = await rc.post("/api/recruiter/postings", json=body)
        r.raise_for_status()
        r = await rc.post(f"/api/recruiter/postings/{r.json()['id']}/publish")
        r.raise_for_status()
    return True


async def _main(title: str) -> None:
    from app.main import create_app

    created = await seed_demo(create_app(), title=title)
    print(f"seeded: recruiter {RECRUITER_EMAIL} / {PASSWORD}, posting '{title}'" if created else "already seeded")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="백엔드 개발자")
    asyncio.run(_main(ap.parse_args().title))
