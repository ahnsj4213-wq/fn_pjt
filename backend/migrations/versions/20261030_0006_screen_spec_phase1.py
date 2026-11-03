"""Screen spec phase 1 (docs/design/screen.md, docs/design/screen-implementation-plan.md).

- users: birth date, gender and terms agreement from the candidate signup window (SC-A01). They stay on the account
  only: resume snapshots, AI inputs and recruiter lists never read them (evaluation input rules unchanged).
- job_postings: the card and detail fields of SC-A02/SC-A03 (experience, employment type, location, image, views)
  and the interview question areas chosen in SC-R07.
- job families: eight engineering families -> the five categories of the screen spec.
- password_reset_tokens: 30-minute reset links (SC-A12, SC-A11).
Revision ID: 0006
"""
import hashlib
import re
import unicodedata

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE = [
    """ALTER TABLE users ADD COLUMN birth_date date, ADD COLUMN gender text CHECK (gender IN ('M','F')),
         ADD COLUMN terms_agreed_at timestamptz""",
    """ALTER TABLE job_postings ADD COLUMN experience_level text, ADD COLUMN employment_type text,
         ADD COLUMN location text, ADD COLUMN image_key text, ADD COLUMN view_count integer NOT NULL DEFAULT 0,
         ADD COLUMN question_areas jsonb""",
    """CREATE TABLE password_reset_tokens (
         token_hash bytea PRIMARY KEY,
         user_id uuid NOT NULL REFERENCES users(id),
         expires_at timestamptz NOT NULL,
         used_at timestamptz,
         created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX ix_reset_user ON password_reset_tokens(user_id)",
    # Phone numbers: 010, 011, 016, 017, 018, 019 (screen spec input rules), was 010 only.
    """DO $$ DECLARE c text; BEGIN
         SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'users'::regclass AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%010-%';
         EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', c);
       END $$""",
    """ALTER TABLE users ADD CONSTRAINT users_candidate_phone CHECK (
         role <> 'CANDIDATE' OR status = 'WITHDRAWN' OR phone ~ '^01[016789]-[0-9]{3,4}-[0-9]{4}$')""",
]
DOWNGRADE = [
    "ALTER TABLE users DROP CONSTRAINT users_candidate_phone",
    """ALTER TABLE users ADD CONSTRAINT users_check2 CHECK (
         role <> 'CANDIDATE' OR status = 'WITHDRAWN' OR phone ~ '^010-[0-9]{4}-[0-9]{4}$') NOT VALID""",
    "DROP TABLE password_reset_tokens",
    """ALTER TABLE job_postings DROP COLUMN question_areas, DROP COLUMN view_count, DROP COLUMN image_key,
         DROP COLUMN location, DROP COLUMN employment_type, DROP COLUMN experience_level""",
    "ALTER TABLE users DROP COLUMN terms_agreed_at, DROP COLUMN gender, DROP COLUMN birth_date",
]
FAMILY_MAP = {"BACKEND": "DEV", "FRONTEND": "DEV", "DEVOPS": "DEV", "MOBILE": "DEV", "QA": "DEV",
              "DATA": "DATA_AI", "AI_ML": "DATA_AI", "PM": "MGMT"}


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)
    for old, new in FAMILY_MAP.items():
        op.execute(f"UPDATE job_postings SET job_family = '{new}' WHERE job_family = '{old}'")
        # Two old families can hold the same question: the copy that would collide stays inactive under its old code.
        op.execute(f"""
            UPDATE question_bank q SET is_active = false WHERE q.job_family = '{old}' AND EXISTS (
              SELECT 1 FROM question_bank o WHERE o.kind = q.kind AND o.job_family = '{new}'
                AND COALESCE(o.company_id, '00000000-0000-0000-0000-000000000000')
                    = COALESCE(q.company_id, '00000000-0000-0000-0000-000000000000')
                AND o.text_norm_hash = q.text_norm_hash)""")
        op.execute(f"""
            UPDATE question_bank q SET job_family = '{new}' WHERE q.job_family = '{old}' AND q.is_active""")
    conn = op.get_bind()
    for fam, qs in NEW_SEED.items():
        for q in qs:
            conn.execute(text("INSERT INTO question_bank(kind, job_family, text, text_norm_hash, source) "
                              "VALUES ('JOB', :f, :t, :h, 'SEED') ON CONFLICT DO NOTHING"),
                         {"f": fam, "t": q, "h": norm_hash(q)})


# The new categories had no job questions; same minimum (4) as the old families.
NEW_SEED = {
    "DESIGN": [
        "사용자 조사 결과를 디자인 결정에 반영했던 경험을 말씀해 주세요.",
        "개발자와 디자인 의도가 다르게 구현됐을 때 어떻게 맞춰 나가겠습니까?",
        "디자인 시스템을 만들거나 고칠 때 가장 먼저 정하는 기준은 무엇입니까?",
        "본인이 만든 화면의 사용성을 어떤 방법으로 검증했는지 설명해 주세요.",
    ],
    "MARKETING": [
        "적은 예산으로 신규 고객을 모아야 할 때 어떤 채널부터 시험해 보겠습니까?",
        "캠페인 성과를 판단할 때 보는 지표와 그 이유를 말씀해 주세요.",
        "예상보다 반응이 낮았던 캠페인을 개선했던 경험을 말씀해 주세요.",
        "우리 서비스의 핵심 고객을 정의한다면 어떤 기준으로 나누겠습니까?",
    ],
    "MGMT": [
        "여러 부서의 요청이 동시에 몰릴 때 우선순위를 어떻게 정하겠습니까?",
        "반복되는 업무 절차를 개선해 시간을 줄였던 경험을 말씀해 주세요.",
        "규정과 현장의 요청이 충돌할 때 어떻게 조율하겠습니까?",
        "예산이나 비용 자료를 정리해 의사결정을 도왔던 경험을 말씀해 주세요.",
    ],
}


def norm_hash(s: str) -> bytes:
    """Same rule as the seed migration: NFKC, all whitespace removed, SHA-256."""
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


def downgrade() -> None:
    op.execute("DELETE FROM question_bank WHERE source = 'SEED' AND job_family IN ('DESIGN','MARKETING','MGMT')")
    op.execute("UPDATE question_bank SET job_family = 'BACKEND' WHERE job_family = 'DEV'")
    op.execute("UPDATE question_bank SET job_family = 'DATA' WHERE job_family = 'DATA_AI'")
    op.execute("UPDATE job_postings SET job_family = 'BACKEND' WHERE job_family IN ('DEV','DESIGN','MARKETING')")
    op.execute("UPDATE job_postings SET job_family = 'DATA' WHERE job_family = 'DATA_AI'")
    op.execute("UPDATE job_postings SET job_family = 'PM' WHERE job_family = 'MGMT'")
    for stmt in DOWNGRADE:
        op.execute(stmt)
