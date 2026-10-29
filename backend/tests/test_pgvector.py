"""Question-bank near-duplicate check on pgvector (migration 0004, QST-01)."""
import math

from sqlalchemy import text

from app.ai.fake import _bigram_vector
from app.core.db import session_scope
from app.models import QuestionBank
from app.questions.compose import _near_duplicate, norm_hash
from tests.helpers import new_company


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


async def test_cosine_near_duplicate_runs_in_postgres():
    company_id = await new_company()
    q = "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요."
    async with session_scope() as db:
        assert (await db.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))).first()
        row = QuestionBank(kind="COMPANY", company_id=company_id, text=q, text_norm_hash=norm_hash(q), source="AI",
                           embedding=_unit(_bigram_vector(q)), embedding_model="bge-m3")
        db.add(row)
        await db.flush()
        ids = [row.id]
        same = _unit(_bigram_vector("최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해주세요"))
        other = _unit(_bigram_vector("가장 좋아하는 음식과 그 이유를 알려 주세요."))
        assert await _near_duplicate(db, ids, same, "bge-m3")
        assert not await _near_duplicate(db, ids, other, "bge-m3")
        assert not await _near_duplicate(db, ids, same, "other-model")  # vectors of another model never compare
        assert not await _near_duplicate(db, [], same, "bge-m3")
        idx = (await db.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_qbank_embedding'"))).scalar()
        assert "hnsw" in idx and "vector_cosine_ops" in idx
