"""Which problem an application gets (ADR-0021). The candidate never chooses, and neither does the recruiter unless they pin one.

One level for the whole posting, from its job family, experience level, difficulty setting and time limit, so applicants to
one posting are compared at the same level. Within that level each application gets a problem that this candidate has not had
and that this posting used least recently, so a leaked problem helps few people. Reporting the choice never touches tests.
"""
import random
import re
from uuid import UUID

from sqlalchemy import select

from app.coding.levels import LEVELS
from app.models import Application, CodingProblem, JobPosting

RECENT_APPLICATIONS = 30  # how far back a posting's issued problems count as recent
CODING_FAMILIES = {"DEV", "DATA_AI"}  # others get a basic problem, whatever the experience level
_OFFSET = {"EASY": -1, "NORMAL": 0, "HARD": 1}


def experience_level(text: str | None) -> int:
    """신입 2 / 경력 3 / 5년 이상 4 / 10년 이상 5. `experience_level` is free text: an unknown value counts as 신입."""
    t = text or ""
    years = [int(x) for x in re.findall(r"(\d+)\s*년", t)]
    if years and max(years) >= 10:
        return 5
    if years and max(years) >= 5:
        return 4
    if years or "경력" in t:
        return 3
    return 2


def target_level(job_family: str | None, experience: str | None, difficulty: str | None, limit_min: int | None) -> int:
    level = experience_level(experience) if job_family in CODING_FAMILIES or job_family is None else 1
    level = max(0, min(5, level + _OFFSET.get(difficulty or "NORMAL", 0)))
    while level > 0 and limit_min is not None and LEVELS[level].minutes > limit_min:
        level -= 1  # a level never gets less time than it needs: a harder problem needs a longer limit on the posting
    return level


def test_minutes(level: int | None, limit_min: int) -> int:
    """The level's time, never more than the posting allows."""
    return min(LEVELS[level].minutes, limit_min) if level in LEVELS else limit_min


async def base_problem_for(db, app: Application, posting: JobPosting) -> CodingProblem:
    if posting.coding_selection == "FIXED" and posting.coding_problem_id:
        return await db.get(CodingProblem, posting.coding_problem_id)
    level = target_level(posting.job_family, posting.experience_level, posting.coding_difficulty, posting.coding_time_limit_min)
    pool = list((await db.execute(select(CodingProblem).where(CodingProblem.is_active, CodingProblem.variant_of.is_(None),
                                                              CodingProblem.level == level))).scalars())
    if not pool:  # no problem of that level is active: the posting's own problem still works
        return await db.get(CodingProblem, posting.coding_problem_id)
    issued = CodingProblem.variant_of
    mine = set((await db.execute(select(issued).join(Application, Application.id == CodingProblem.variant_for).where(
        Application.candidate_id == app.candidate_id, Application.id != app.id))).scalars())
    recent = list((await db.execute(select(issued).join(Application, Application.id == CodingProblem.variant_for).where(
        Application.posting_id == posting.id, Application.id != app.id).order_by(Application.applied_at.desc())
        .limit(RECENT_APPLICATIONS))).scalars())

    def rank(p: CodingProblem) -> tuple:
        last_seen = recent.index(p.id) if p.id in recent else len(recent) + 1  # larger = longer ago
        family_miss = bool(p.families) and posting.job_family not in p.families
        return (p.id in mine, -last_seen, family_miss)

    best = min(rank(p) for p in pool)
    choices = sorted((p for p in pool if rank(p) == best), key=lambda p: str(p.id))
    return random.Random(_seed(app.id)).choice(choices)


def _seed(application_id: UUID) -> int:
    return application_id.int % (2**31)
