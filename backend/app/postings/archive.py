"""Archive the old test postings (ADR-0029): back them up, then take them off every applicant screen. Nothing is deleted.

    python -m app.postings.archive                                  # report only: every posting, its class and the evidence
    python -m app.postings.archive --apply --backup-dir /data/backups  # back up, then archive the test postings
    python -m app.postings.archive --restore /data/backups/postings-archive-<time>.json

A posting is archived only with evidence that it is test data (`classify`); one without evidence is reported as UNCLEAR and left
alone unless named with --include. Archiving keeps the row and everything that refers to it (applications, answers, evaluations,
coding results): it sets hidden_by=ARCHIVE (off the list, detail and apply screens; applicants who applied keep their history) and
closes an OPEN posting with close_reason=ARCHIVED, without expiring anyone's application. Tech blog postings (is_virtual) are never
archived here. The backup file holds each posting row as it was, so --restore puts back exactly those columns.
"""
import argparse
import asyncio
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.audit import audit
from app.core.db import session_scope
from app.core.timeutil import utcnow

TEST_TITLE = re.compile(r"테스트|임시|확인용|\btest\b|\d{3,}\s*$", re.I)
TEST_DOMAIN = re.compile(r"\.(example|invalid|test|localhost)$", re.I)  # RFC 2606 / 6761 reserved names: never a real mailbox
ARCHIVED_COLUMNS = ("status", "closed_at", "close_reason", "hidden_at", "hidden_by", "updated_at", "row_version")


def classify(row: dict) -> tuple[str, list[str]]:
    """(class, evidence). Classes: VIRTUAL_TECH_BLOG (kept), ARCHIVED (done already), SEED_SYNTHETIC, TEST, UNCLEAR."""
    evidence: list[str] = []
    if row["is_virtual"]:
        return "VIRTUAL_TECH_BLOG", ["기술 블로그 기반 테스트 공고(is_virtual)"]
    if row["hidden_by"] == "ARCHIVE":
        return "ARCHIVED", ["이미 보관 처리됨"]
    if row["company_name"].endswith("(가상)"):
        evidence.append("회사명이 '(가상)'으로 끝남: seed_knowledge가 만든 가상 회사")
    if row["recruiter_domains"] and all(TEST_DOMAIN.search(d) for d in row["recruiter_domains"]):
        evidence.append(f"담당자 계정이 테스트 전용 도메인만 씀({', '.join(sorted(set(row['recruiter_domains'])))})")
    if TEST_TITLE.search(row["title"] or ""):
        evidence.append(f"제목이 테스트용 표기('{row['title']}')")
    if row["deleted_at"] is not None:
        evidence.append("담당자가 이미 삭제한 공고")
    if row["company_test_titles"]:
        evidence.append(f"같은 회사에 테스트용 제목 공고가 있음({row['company_test_titles']}건)")
    if not evidence:
        return "UNCLEAR", ["테스트 데이터라는 근거 없음: 보관하지 않음"]
    return ("SEED_SYNTHETIC" if row["company_name"].endswith("(가상)") else "TEST"), evidence


async def survey(db) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT p.id, p.company_id, c.name AS company_name, p.title, p.status, p.is_virtual, p.deleted_at, p.hidden_at, p.hidden_by,
               p.created_at,
               ARRAY(SELECT lower(split_part(u.email, '@', 2)) FROM users u WHERE u.company_id = p.company_id AND u.role = 'RECRUITER')
                 AS recruiter_domains,
               (SELECT count(*) FROM applications a WHERE a.posting_id = p.id) AS applications,
               (SELECT count(*) FROM interview_answers x JOIN applications a ON a.id = x.application_id WHERE a.posting_id = p.id) AS answers,
               (SELECT count(*) FROM evaluation_runs e JOIN applications a ON a.id = e.application_id WHERE a.posting_id = p.id) AS evaluations,
               (SELECT count(*) FROM coding_submissions s JOIN applications a ON a.id = s.application_id WHERE a.posting_id = p.id) AS coding,
               (SELECT count(*) FROM company_knowledge k WHERE k.posting_id = p.id) AS posting_chunks
        FROM job_postings p JOIN companies c ON c.id = p.company_id
        ORDER BY c.name, p.created_at"""))).mappings().all()
    out = [dict(r) for r in rows]
    for r in out:
        r["company_test_titles"] = sum(1 for o in out if o["company_id"] == r["company_id"] and o["id"] != r["id"]
                                       and not o["is_virtual"] and TEST_TITLE.search(o["title"] or ""))
        r["class"], r["evidence"] = classify(r)
    return out


def _json(v):
    if isinstance(v, (datetime, uuid.UUID)):
        return str(v)
    raise TypeError(type(v))


async def apply(db, rows: list[dict], include: set[str], backup_dir: Path) -> tuple[Path, list[dict]]:
    """Backs up first (the whole posting row + what refers to it), then archives. Returns the backup path and what was archived."""
    targets = [r for r in rows if r["class"] in ("SEED_SYNTHETIC", "TEST") or (r["class"] == "UNCLEAR" and str(r["id"]) in include)]
    ids = [r["id"] for r in targets]
    full = (await db.execute(text("SELECT row_to_json(p) FROM job_postings p WHERE id = ANY(:i)"), {"i": ids})).scalars().all()
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"postings-archive-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    summary = [{c: r[c] for c in ("id", "company_name", "title", "status", "class", "evidence", "applications", "answers", "evaluations", "coding")}
               for r in targets]
    path.write_text(json.dumps({"made_at": utcnow(), "rows": full, "postings": summary}, ensure_ascii=False, indent=1, default=_json),
                    encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))  # the backup must read back before anything changes
    now = utcnow()
    for r in targets:
        await db.execute(text("""
            UPDATE job_postings SET
              hidden_at = COALESCE(hidden_at, :now), hidden_by = 'ARCHIVE',
              closed_at = CASE WHEN status = 'OPEN' THEN :now ELSE closed_at END,
              close_reason = CASE WHEN status = 'OPEN' THEN 'ARCHIVED' ELSE close_reason END,
              status = CASE WHEN status = 'OPEN' THEN 'CLOSED' ELSE status END,
              updated_at = :now, row_version = row_version + 1
            WHERE id = :i AND NOT is_virtual"""), {"i": r["id"], "now": now})
        await audit(db, action="POSTING_ARCHIVE", target_type="posting", target_id=r["id"], actor_id=None, actor_role="OPERATOR_CLI",
                    company_id=r["company_id"], reason=r["class"], backup=path.name)
    return path, targets


async def restore(db, backup: Path) -> int:
    """Puts back the archived columns of every posting in the backup that is still archived. Other columns are not touched."""
    data = json.loads(backup.read_text(encoding="utf-8"))
    n = 0
    for row in data["rows"]:
        values = {c: row[c] for c in ARCHIVED_COLUMNS}
        for c in ("closed_at", "hidden_at", "updated_at"):
            values[c] = datetime.fromisoformat(values[c]) if values[c] else None
        res = await db.execute(text(
            "UPDATE job_postings SET status = :status, closed_at = :closed_at, close_reason = :close_reason, hidden_at = :hidden_at, "
            "hidden_by = :hidden_by, updated_at = :updated_at, row_version = :row_version WHERE id = :id AND hidden_by = 'ARCHIVE'"),
            {"id": uuid.UUID(row["id"]), **values})
        if res.rowcount:
            n += 1
            await audit(db, action="POSTING_ARCHIVE_RESTORE", target_type="posting", target_id=row["id"], actor_id=None,
                        actor_role="OPERATOR_CLI", company_id=row["company_id"], backup=backup.name)
    return n


def _report(rows: list[dict]) -> dict:
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    return {"total": len(rows), "by_class": by_class,
            "postings": [{"id": str(r["id"]), "company": r["company_name"], "title": r["title"], "status": r["status"],
                          "deleted": r["deleted_at"] is not None, "class": r["class"], "evidence": r["evidence"],
                          "linked": {k: r[k] for k in ("applications", "answers", "evaluations", "coding", "posting_chunks")}} for r in rows]}


async def _main(args) -> int:
    async with session_scope() as db:
        if args.restore:
            print(json.dumps({"restored": await restore(db, Path(args.restore))}))
            return 0
        rows = await survey(db)
        report = _report(rows)
        if not args.apply:
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 0
        path, done = await apply(db, rows, set(args.include or []), Path(args.backup_dir))
        print(json.dumps({"backup": str(path), "archived": len(done), "before": report["by_class"]}, ensure_ascii=False))
    return 0


async def archived_exists(db) -> bool:
    """True once this database moved to the tech blog test postings: old postings were archived here, or the tech blog
    postings are loaded (the archived ones may since have been deleted). The old seed scripts then refuse to run."""
    return await db.scalar(text("SELECT EXISTS (SELECT 1 FROM job_postings WHERE hidden_by = 'ARCHIVE' OR is_virtual)")) is True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m app.postings.archive")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", default="/data/backups")
    ap.add_argument("--include", action="append", help="posting id of an UNCLEAR posting to archive as well")
    ap.add_argument("--restore", metavar="BACKUP_JSON")
    sys.exit(asyncio.run(_main(ap.parse_args())))

