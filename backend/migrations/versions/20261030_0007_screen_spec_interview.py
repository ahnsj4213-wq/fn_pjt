"""Screen spec phase 2: configurable interview (SC-A04~A07, SC-R07; docs/design/screen-implementation-plan.md).

- posting_eval_settings: question plan [{area, text}] (1~10 slots, empty text = AI/bank), think time, answer limit
  and follow-up cap, versioned with the weights.
- question_sets keep the main count and follow-up cap the set was composed with (old sets: 10 and 4).
- interview_questions: slot = question area, answer mode (speech / code / whiteboard), think time, whiteboard
  option. The fixed 10-slot order and 60/45-second checks are replaced by ranges.
- interview turns: think phase, one replay, one interviewer interjection, live-coding code and whiteboard image.
- applications.share_result: unchecked = practice mode, the company never sees the result (SC-A05).
- proctor_events: gaze-away warnings from the browser (SC-A06).
Revision ID: 0007
"""
import hashlib
import re
import unicodedata

from alembic import op
from sqlalchemy import text

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _drop_checks(table: str, *patterns: str) -> str:
    """Drop the unnamed CHECK constraints of `table` whose definition contains any of `patterns`."""
    cond = " OR ".join(f"pg_get_constraintdef(oid) LIKE '%{p}%'" for p in patterns)
    return f"""DO $$ DECLARE c text; BEGIN
         FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = '{table}'::regclass AND contype = 'c' AND ({cond})
         LOOP EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', c); END LOOP;
       END $$"""


UPGRADE = [
    """ALTER TABLE posting_eval_settings
         ADD COLUMN question_plan jsonb CHECK (question_plan IS NULL OR jsonb_array_length(question_plan) BETWEEN 1 AND 10),
         ADD COLUMN think_sec smallint NOT NULL DEFAULT 10 CHECK (think_sec IN (5, 10, 20, 30)),
         ADD COLUMN answer_sec smallint NOT NULL DEFAULT 60 CHECK (answer_sec IN (30, 60, 90, 120)),
         ADD COLUMN followup_max smallint NOT NULL DEFAULT 2 CHECK (followup_max BETWEEN 0 AND 4)""",
    """ALTER TABLE question_sets
         ADD COLUMN main_count smallint NOT NULL DEFAULT 10 CHECK (main_count BETWEEN 1 AND 10),
         ADD COLUMN followup_max smallint NOT NULL DEFAULT 4 CHECK (followup_max BETWEEN 0 AND 4)""",
    _drop_checks("interview_questions", "slot_type", "answer_limit_sec", "CUSTOM"),
    "DROP INDEX ux_iq_custom",
    """ALTER TABLE interview_questions
         ADD COLUMN answer_mode text NOT NULL DEFAULT 'SPEECH' CHECK (answer_mode IN ('SPEECH','CODE','WHITEBOARD')),
         ADD COLUMN think_sec smallint NOT NULL DEFAULT 0 CHECK (think_sec BETWEEN 0 AND 30),
         ADD COLUMN whiteboard_optional boolean NOT NULL DEFAULT false,
         ADD CONSTRAINT iq_slot_type CHECK (slot_type IN ('PERSONALITY','MOTIVATION','JOB_SKILL','PROJECT',
           'COLLABORATION','RESUME','COMPANY','JOB')),
         ADD CONSTRAINT iq_answer_limit CHECK (answer_limit_sec BETWEEN 30 AND 180)""",
    "ALTER TABLE interview_questions DROP CONSTRAINT IF EXISTS interview_questions_source_check",
    """ALTER TABLE interview_questions ADD CONSTRAINT iq_source
         CHECK (source IN ('BANK','AI','CUSTOM','BANK_FALLBACK','PROBLEM'))""",
    _drop_checks("question_bank", "kind = ANY"),
    """ALTER TABLE question_bank ADD CONSTRAINT qbank_kind
         CHECK (kind IN ('PERSONALITY','COMPANY','JOB','COLLABORATION'))""",
    _drop_checks("interviews", "current_phase"),
    """ALTER TABLE interviews
         ADD CONSTRAINT interviews_phase CHECK (current_phase IN ('PRESENTING','THINKING','ANSWERING','FINALIZING',
           'DECIDING','DONE')),
         ADD COLUMN gaze_warning_count smallint NOT NULL DEFAULT 0""",
    """ALTER TABLE interview_turns
         ADD COLUMN think_until timestamptz,
         ADD COLUMN replayed_at timestamptz,
         ADD COLUMN interjected_at timestamptz,
         ADD COLUMN code_text text CHECK (octet_length(code_text) <= 65536),
         ADD COLUMN code_language text CHECK (code_language IN ('python','java','javascript')),
         ADD COLUMN whiteboard_key text""",
    """ALTER TABLE interview_answers
         ADD COLUMN code_text text CHECK (octet_length(code_text) <= 65536),
         ADD COLUMN code_language text CHECK (code_language IN ('python','java','javascript')),
         ADD COLUMN whiteboard_key text,
         ADD COLUMN interjected boolean NOT NULL DEFAULT false""",
    "ALTER TABLE applications ADD COLUMN share_result boolean NOT NULL DEFAULT true",
    _drop_checks("proctor_events", "LEAVE"),
    "ALTER TABLE proctor_events ADD CONSTRAINT proctor_kind CHECK (kind IN ('LEAVE','RETURN','GAZE_AWAY'))",
]
DOWNGRADE = [
    "DELETE FROM proctor_events WHERE kind = 'GAZE_AWAY'",
    "ALTER TABLE proctor_events DROP CONSTRAINT proctor_kind",
    "ALTER TABLE proctor_events ADD CONSTRAINT proctor_events_kind_check CHECK (kind IN ('LEAVE','RETURN'))",
    "ALTER TABLE applications DROP COLUMN share_result",
    """ALTER TABLE interview_answers DROP COLUMN interjected, DROP COLUMN whiteboard_key, DROP COLUMN code_language,
         DROP COLUMN code_text""",
    """ALTER TABLE interview_turns DROP COLUMN whiteboard_key, DROP COLUMN code_language, DROP COLUMN code_text,
         DROP COLUMN interjected_at, DROP COLUMN replayed_at, DROP COLUMN think_until""",
    "UPDATE interviews SET current_phase = 'PRESENTING' WHERE current_phase = 'THINKING'",
    "ALTER TABLE interviews DROP CONSTRAINT interviews_phase, DROP COLUMN gaze_warning_count",
    """ALTER TABLE interviews ADD CONSTRAINT interviews_current_phase_check
         CHECK (current_phase IN ('PRESENTING','ANSWERING','FINALIZING','DECIDING','DONE'))""",
    "DELETE FROM question_bank WHERE kind = 'COLLABORATION'",
    "ALTER TABLE question_bank DROP CONSTRAINT qbank_kind",
    "ALTER TABLE question_bank ADD CONSTRAINT question_bank_kind_check CHECK (kind IN ('PERSONALITY','COMPANY','JOB'))",
    # Rows composed under the new rules cannot satisfy the old checks: those are left unchecked (NOT VALID).
    "ALTER TABLE interview_questions DROP CONSTRAINT iq_source",
    """ALTER TABLE interview_questions ADD CONSTRAINT interview_questions_source_check
         CHECK (source IN ('BANK','AI','CUSTOM','BANK_FALLBACK')) NOT VALID""",
    """ALTER TABLE interview_questions DROP CONSTRAINT iq_answer_limit, DROP CONSTRAINT iq_slot_type,
         DROP COLUMN whiteboard_optional, DROP COLUMN think_sec, DROP COLUMN answer_mode""",
    """ALTER TABLE interview_questions ADD CONSTRAINT interview_questions_slot_type_check
         CHECK (slot_type IN ('PERSONALITY','RESUME','COMPANY','JOB')) NOT VALID""",
    """ALTER TABLE question_sets DROP COLUMN followup_max, DROP COLUMN main_count""",
    """ALTER TABLE posting_eval_settings DROP COLUMN followup_max, DROP COLUMN answer_sec, DROP COLUMN think_sec,
         DROP COLUMN question_plan""",
]

# Collaboration is a question area of its own in the screen spec (SC-A06 question 5, SC-R07).
COLLABORATION_SEED = [
    "팀원과 의견이 크게 달랐던 경험과, 그때 어떻게 합의에 이르렀는지 말씀해 주세요.",
    "협업 중에 맡은 일이 늦어지는 팀원이 있다면 어떻게 하시겠습니까?",
    "다른 직무의 동료와 함께 일하면서 가장 어려웠던 점과 해결 방법을 말씀해 주세요.",
    "팀 프로젝트에서 본인이 맡았던 역할과, 팀에 가장 크게 기여한 점은 무엇입니까?",
    "피드백을 받고 일하는 방식을 바꿨던 경험을 말씀해 주세요.",
    "갈등을 피하지 않고 직접 이야기해서 문제를 풀었던 경험이 있습니까?",
]


def norm_hash(s: str) -> bytes:
    """Same rule as the seed migration: NFKC, all whitespace removed, SHA-256."""
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)
    conn = op.get_bind()
    for q in COLLABORATION_SEED:
        conn.execute(text("INSERT INTO question_bank(kind, text, text_norm_hash, source) "
                          "VALUES ('COLLABORATION', :t, :h, 'SEED') ON CONFLICT DO NOTHING"),
                     {"t": q, "h": norm_hash(q)})


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)
    op.execute("CREATE UNIQUE INDEX ux_iq_custom ON interview_questions(question_set_id) WHERE source = 'CUSTOM'")
