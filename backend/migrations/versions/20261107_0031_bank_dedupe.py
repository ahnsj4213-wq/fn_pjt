"""Seed question bank: questions that asked the same thing under two kinds rewritten (2026-11-07).

One interview takes two personality and one collaboration question from the bank, and both kinds held the same question in other
words ("다른 사람의 피드백을 받아 일하는 방식을 바꾼 경험" / "피드백을 받고 일하는 방식을 바꿨던 경험"; three questions about a
disagreement with a teammate). The rows are rewritten in place, so the bank ids that past question sets point at stay valid; the
embedding of a rewritten row is cleared (it described the old text).

Revision ID: 0031
"""
import hashlib
import re
import unicodedata

from alembic import op
from sqlalchemy import text

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

# (kind, job_family, old text, new text)
REWRITES = [
    ("COLLABORATION", None, "피드백을 받고 일하는 방식을 바꿨던 경험을 말씀해 주세요.",
     "동료에게 듣기 불편한 피드백을 전해야 했을 때 어떻게 전했고, 그 뒤 관계는 어땠는지 말씀해 주세요."),
    ("COLLABORATION", None, "갈등을 피하지 않고 직접 이야기해서 문제를 풀었던 경험이 있습니까?",
     "팀에서 아무도 맡으려 하지 않는 일을 맡게 됐을 때 어떻게 하셨는지 말씀해 주세요."),
    ("PERSONALITY", None, "팀원과 의견이 달랐던 경험과 그 상황을 어떻게 해결했는지 말씀해 주세요.",
     "일이 계획대로 되지 않아 크게 실망했을 때 어떻게 다시 추스르셨는지 말씀해 주세요."),
    ("PERSONALITY", None, "동료를 도와 팀의 결과를 높인 경험을 말씀해 주세요.",
     "본인의 강점과 약점이 실제 일에서 드러났던 사례를 하나씩 말씀해 주세요."),
    ("JOB", "DEV", "장애 대응 절차를 만들거나 개선한 경험을 말씀해 주세요.",
     "코드 리뷰에서 가장 중요하게 보는 기준과 그 이유를 설명해 주세요."),
]


def norm_hash(s: str) -> bytes:
    """Same rule as the seed migration (0007) and app.questions.compose.norm_hash."""
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


def _apply(pairs) -> None:
    conn = op.get_bind()
    for kind, family, old, new in pairs:
        conn.execute(text(
            "UPDATE question_bank SET text = :new, text_norm_hash = :h, embedding = NULL, embedding_model = NULL "
            "WHERE source = 'SEED' AND kind = :k AND job_family IS NOT DISTINCT FROM CAST(:f AS text) AND text = :old "
            "AND NOT EXISTS (SELECT 1 FROM question_bank b WHERE b.kind = :k AND b.job_family IS NOT DISTINCT FROM CAST(:f AS text) "
            "AND b.company_id IS NULL AND b.text_norm_hash = :h)"),
            {"new": new, "h": norm_hash(new), "k": kind, "f": family, "old": old})


def upgrade() -> None:
    _apply(REWRITES)


def downgrade() -> None:
    _apply([(k, f, new, old) for k, f, old, new in REWRITES])
