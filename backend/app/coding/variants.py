"""Per-application coding problem variants (the anti-leak design).

A template (coding/templates.py) is turned into a private problem row for one application: the PUBLIC examples are the
template's, the HIDDEN inputs are generated from a seed, and every expected output comes from the template's reference
`solve`. Nothing here asks an LLM for a test or an answer. The statement may be rewritten in other words
(ai/tasks/problem_rewrite.py, `rewrite_statement`); it is checked against the original before it replaces it.
"""
import random
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coding.templates import BY_TITLE, TEMPLATES, Template
from app.models import CodingProblem, CodingTestcase

_NUM = re.compile(r"\d[\d,]*")
_TICK = re.compile(r"`[^`]+`")
_HEADINGS = ("### 입력", "### 출력")


def seed_for(application_id: UUID) -> int:
    return application_id.int % (2**31)


def statement_preserves(original: str, rewritten: str) -> bool:
    """A rewrite may change the story and the names, never the format or the numbers that decide the answer."""
    if any(h not in rewritten for h in _HEADINGS):
        return False
    nums = lambda s: {n.replace(",", "") for n in _NUM.findall(s)}  # noqa: E731
    if not nums(original) <= nums(rewritten):
        return False
    return {t for t in _TICK.findall(original)} <= {t for t in _TICK.findall(rewritten)}


def template_of(base: CodingProblem) -> Template | None:
    return TEMPLATES.get(base.template_key or "") or BY_TITLE.get(base.title)


async def get_variant(db: AsyncSession, application_id: UUID) -> CodingProblem | None:
    return (await db.execute(select(CodingProblem).where(CodingProblem.variant_for == application_id))).scalar_one_or_none()


async def ensure_variant(db: AsyncSession, application_id: UUID, base: CodingProblem, statement: str | None = None
                         ) -> CodingProblem:
    """The application's own copy of `base`; created once (a second call, or a concurrent one, returns the same row)."""
    existing = await get_variant(db, application_id)
    if existing is not None:
        return existing
    tpl = template_of(base)
    if tpl is None:  # a problem without a template (hand-made row) is used as it is
        return base
    seed = seed_for(application_id)
    text_ = statement if statement and statement_preserves(tpl.statement, statement) else tpl.statement
    try:
        async with db.begin_nested():
            v = CodingProblem(title=tpl.title, statement_md=text_, language=base.language, time_limit_ms=tpl.time_limit_ms,
                              memory_mb=tpl.memory_mb, is_active=False, difficulty=tpl.difficulty, template_key=tpl.key,
                              variant_of=base.id, variant_for=application_id, variant_seed=seed)
            db.add(v)
            await db.flush()
            ordinal = 0
            for inp in tpl.public:
                ordinal += 1
                db.add(CodingTestcase(problem_id=v.id, ordinal=ordinal, visibility="PUBLIC", input=inp, expected_output=tpl.solve(inp)))
            for inp in tpl.hidden(random.Random(seed)):
                ordinal += 1
                db.add(CodingTestcase(problem_id=v.id, ordinal=ordinal, visibility="HIDDEN", input=inp, expected_output=tpl.solve(inp)))
            await db.flush()
    except IntegrityError:  # another transaction created it first
        again = await get_variant(db, application_id)
        if again is None:
            raise
        return again
    return v


async def problem_of(db: AsyncSession, application_id: UUID, posting) -> CodingProblem:
    """The problem this application was given: its attempt's, else its variant (made now if the attempt row is missing)."""
    from app.models import CodingAttempt  # local: models import chain
    attempt = await db.get(CodingAttempt, application_id)
    if attempt is not None:
        return await db.get(CodingProblem, attempt.problem_id)
    return await ensure_variant(db, application_id, await db.get(CodingProblem, posting.coding_problem_id))
