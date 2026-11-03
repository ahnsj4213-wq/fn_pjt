"""ORM mapping of the schema created by migrations/versions/*_0001_initial.py.

The migration (raw SQL with CHECKs and triggers) is the source of truth; these classes only map columns
used by the application. Company-owned tables carry `company_id` and are queried through
app.auth.scope helpers (SEC-03).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, LargeBinary, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.constants import EMBED_DIM

TS = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text)
    intro: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    biz_no: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(Text, default="APPROVED")  # PENDING blocks posting (SC-R01)
    verification_method: Mapped[str | None] = mapped_column(Text)
    evidence_key: Mapped[str | None] = mapped_column(Text)
    evidence_name: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(TS)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    suspended_at: Mapped[datetime | None] = mapped_column(TS)  # SC-O02


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    role: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    email: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    birth_date: Mapped[date | None] = mapped_column(Date)  # account only: never copied into AI inputs (SC-A01)
    gender: Mapped[str | None] = mapped_column(Text)
    terms_agreed_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    withdrawn_at: Mapped[datetime | None] = mapped_column(TS)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    csrf_token: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    last_seen_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)


class SignupCode(Base):
    __tablename__ = "signup_codes"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(TS)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class AuthThrottle(Base):
    __tablename__ = "auth_throttle"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(TS)
    locked_until: Mapped[datetime | None] = mapped_column(TS)


class CodingProblem(Base):
    __tablename__ = "coding_problems"
    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(Text)
    statement_md: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, default="python")
    time_limit_ms: Mapped[int] = mapped_column(Integer)
    memory_mb: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    difficulty: Mapped[str | None] = mapped_column(Text)


class CodingTestcase(Base):
    __tablename__ = "coding_testcases"
    id: Mapped[uuid.UUID] = _pk()
    problem_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("coding_problems.id"))
    ordinal: Mapped[int] = mapped_column(SmallInteger)
    visibility: Mapped[str] = mapped_column(Text)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str] = mapped_column(Text)


class JobPosting(Base):
    __tablename__ = "job_postings"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    status: Mapped[str] = mapped_column(Text, default="DRAFT")
    title: Mapped[str | None] = mapped_column(Text)
    job_family: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    preferred: Mapped[str | None] = mapped_column(Text)
    duties: Mapped[str | None] = mapped_column(Text)
    headcount: Mapped[int | None] = mapped_column(Integer)
    experience_level: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    image_key: Mapped[str | None] = mapped_column(Text)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    question_areas: Mapped[list | None] = mapped_column(JSONB)
    deadline_at: Mapped[datetime | None] = mapped_column(TS)
    coding_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    coding_problem_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("coding_problems.id"))
    coding_language: Mapped[str | None] = mapped_column(Text)
    coding_time_limit_min: Mapped[int | None] = mapped_column(SmallInteger)
    current_settings_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    settings_locked_at: Mapped[datetime | None] = mapped_column(TS)
    published_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    close_reason: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)
    hidden_at: Mapped[datetime | None] = mapped_column(TS)  # SC-O02
    hidden_by: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class PostingEvalSettings(Base):
    __tablename__ = "posting_eval_settings"
    id: Mapped[uuid.UUID] = _pk()
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    w_expertise: Mapped[int] = mapped_column(SmallInteger)
    w_logic: Mapped[int] = mapped_column(SmallInteger)
    w_attitude: Mapped[int] = mapped_column(SmallInteger)
    w_delivery: Mapped[int] = mapped_column(SmallInteger)
    w_coding: Mapped[int | None] = mapped_column(SmallInteger)
    custom_question: Mapped[str | None] = mapped_column(Text)
    question_plan: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))  # None is SQL NULL, not JSON null (CHECK)
    think_sec: Mapped[int] = mapped_column(SmallInteger, default=10)
    answer_sec: Mapped[int] = mapped_column(SmallInteger, default=60)
    followup_max: Mapped[int] = mapped_column(SmallInteger, default=2)
    rubric_version: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")

    def weights(self) -> dict[str, int]:
        w = {"expertise": self.w_expertise, "logic": self.w_logic, "attitude": self.w_attitude,
             "delivery": self.w_delivery}
        if self.w_coding is not None:
            w["coding"] = self.w_coding
        return w


class ConsentDocument(Base):
    __tablename__ = "consent_documents"
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    body_md: Mapped[str] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSONB)
    is_draft: Mapped[bool] = mapped_column(Boolean)
    published_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[uuid.UUID] = _pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_version: Mapped[str] = mapped_column(Text)
    answers: Mapped[dict] = mapped_column(JSONB)
    all_required_agreed: Mapped[bool] = mapped_column(Boolean)
    agreed_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ResumeFile(Base):
    __tablename__ = "resume_files"
    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    page_count: Mapped[int | None] = mapped_column(Integer)
    text_extractable: Mapped[bool] = mapped_column(Boolean)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(Text, default="PENDING")
    extracted_fields: Mapped[dict | None] = mapped_column(JSONB)
    ocr_pages: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))  # RES-04: pages read by OCR in the job
    extraction_method: Mapped[str | None] = mapped_column(Text)  # TEXT | OCR
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    low_conf_fields: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Resume(Base):
    __tablename__ = "resumes"
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    content: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    consent_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    resume_snapshot: Mapped[dict] = mapped_column(JSONB)
    resume_snapshot_text: Mapped[str] = mapped_column(Text)
    resume_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_status: Mapped[str] = mapped_column(Text, default="READY")
    eval_status: Mapped[str | None] = mapped_column(Text)
    decision_status: Mapped[str] = mapped_column(Text, default="UNDECIDED")
    eval_settings_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    answered_main_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    share_result: Mapped[bool] = mapped_column(Boolean, default=True)
    interrupted_from: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    interview_started_at: Mapped[datetime | None] = mapped_column(TS)
    submitted_at: Mapped[datetime | None] = mapped_column(TS)
    expired_at: Mapped[datetime | None] = mapped_column(TS)
    eval_done_at: Mapped[datetime | None] = mapped_column(TS)
    first_decided_at: Mapped[datetime | None] = mapped_column(TS)
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(TS)
    audio_purged_at: Mapped[datetime | None] = mapped_column(TS)
    content_purged_at: Mapped[datetime | None] = mapped_column(TS)


class Interview(Base):
    __tablename__ = "interviews"
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    question_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    precheck_media_ok_at: Mapped[datetime | None] = mapped_column(TS)
    precheck_webrtc_ok_at: Mapped[datetime | None] = mapped_column(TS)
    current_question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    current_phase: Mapped[str | None] = mapped_column(Text)
    answer_opened_at: Mapped[datetime | None] = mapped_column(TS)
    answer_deadline_at: Mapped[datetime | None] = mapped_column(TS)
    presentation_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    followups_used: Mapped[int] = mapped_column(SmallInteger, default=0)
    text_mode_since: Mapped[datetime | None] = mapped_column(TS)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(TS)
    interruption_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    gaze_warning_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    ended_at: Mapped[datetime | None] = mapped_column(TS)


class InterviewSlot(Base):
    __tablename__ = "interview_slots"
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    lease_expires_at: Mapped[datetime] = mapped_column(TS)


class QuestionBank(Base):
    __tablename__ = "question_bank"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(Text)
    job_family: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(Text)
    text_norm_hash: Mapped[bytes] = mapped_column(LargeBinary)
    source: Mapped[str] = mapped_column(Text)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))  # pgvector, BGE-M3 (migration 0004)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InterviewSchedule(Base):
    """SC-A15: an interview date the company sent by mail, with the candidate's answer (migration 0011)."""
    __tablename__ = "interview_schedules"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("applications.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(TS)
    place: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)  # NULL for the company's own entries (SC-R11, migration 0016)
    kind: Mapped[str] = mapped_column(Text, default="INTERVIEW")
    response_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class RecruiterMail(Base):
    """SC-R14: a mail a recruiter sent from an applicant's page (migration 0016)."""
    __tablename__ = "recruiter_mails"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("applications.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    sender_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class Notification(Base):
    """SC-R12: one event of a company's notification inbox (migration 0017)."""
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    done_text: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class QuestionExample(Base):
    """RAG reference questions (migration 0010): shown to T2/T3 as examples, never asked as they are."""
    __tablename__ = "question_examples"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(Text)
    field: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    text_norm_hash: Mapped[bytes] = mapped_column(LargeBinary)
    origin: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS)


class QuestionSet(Base):
    __tablename__ = "question_sets"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    settings_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    composer_version: Mapped[str] = mapped_column(Text)
    main_count: Mapped[int] = mapped_column(SmallInteger, default=10)
    followup_max: Mapped[int] = mapped_column(SmallInteger, default=4)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    ready_at: Mapped[datetime | None] = mapped_column(TS)


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"
    id: Mapped[uuid.UUID] = _pk()
    question_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(SmallInteger)
    role: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    slot_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    bank_question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(Text)
    grounding: Mapped[dict | None] = mapped_column(JSONB)
    answer_limit_sec: Mapped[int] = mapped_column(SmallInteger)
    answer_mode: Mapped[str] = mapped_column(Text, default="SPEECH")
    think_sec: Mapped[int] = mapped_column(SmallInteger, default=0)
    whiteboard_optional: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class QuestionTtsAsset(Base):
    __tablename__ = "question_tts_assets"
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    audio_key: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    voice_model: Mapped[str] = mapped_column(Text)


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    presentation_no: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(Text)
    input_mode: Mapped[str] = mapped_column(Text, default="VOICE")
    presented_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    tts_ended_at: Mapped[datetime | None] = mapped_column(TS)
    opened_at: Mapped[datetime | None] = mapped_column(TS)
    deadline_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    end_reason: Mapped[str | None] = mapped_column(Text)
    think_until: Mapped[datetime | None] = mapped_column(TS)
    replayed_at: Mapped[datetime | None] = mapped_column(TS)
    interjected_at: Mapped[datetime | None] = mapped_column(TS)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_language: Mapped[str | None] = mapped_column(Text)
    whiteboard_key: Mapped[str | None] = mapped_column(Text)


class InterviewAnswer(Base):
    __tablename__ = "interview_answers"
    id: Mapped[uuid.UUID] = _pk()
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    input_mode: Mapped[str] = mapped_column(Text)
    end_reason: Mapped[str] = mapped_column(Text)
    is_no_answer: Mapped[bool] = mapped_column(Boolean)
    stt_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript: Mapped[str | None] = mapped_column(Text)
    eval_text: Mapped[str | None] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(Text)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer)
    stt_model: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(TS)
    confirmed_at: Mapped[datetime] = mapped_column(TS)
    reask_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_language: Mapped[str | None] = mapped_column(Text)
    whiteboard_key: Mapped[str | None] = mapped_column(Text)
    interjected: Mapped[bool] = mapped_column(Boolean, default=False)


class FollowupDecision(Base):
    __tablename__ = "followup_decisions"
    main_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    skip_cause: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ProctorEvent(Base):
    __tablename__ = "proctor_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(TS)
    away_ms: Mapped[int | None] = mapped_column(Integer)


class CodingAttempt(Base):
    __tablename__ = "coding_attempts"
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    problem_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TS)
    deadline_at: Mapped[datetime] = mapped_column(TS)
    draft_code: Mapped[str] = mapped_column(Text, default="")
    draft_revision: Mapped[int] = mapped_column(Integer, default=0)
    draft_saved_at: Mapped[datetime | None] = mapped_column(TS)


class CodingRun(Base):
    __tablename__ = "coding_runs"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)
    stdout_head: Mapped[str | None] = mapped_column(Text)
    stderr_head: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class CodingSubmission(Base):
    __tablename__ = "coding_submissions"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    kind: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    grading_status: Mapped[str] = mapped_column(Text, default="PENDING")
    compile_ok: Mapped[bool | None] = mapped_column(Boolean)
    passed: Mapped[int | None] = mapped_column(SmallInteger)
    total: Mapped[int | None] = mapped_column(SmallInteger)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    graded_at: Mapped[datetime | None] = mapped_column(TS)


class CodingTestResult(Base):
    __tablename__ = "coding_test_results"
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    testcase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[str] = mapped_column(Text)
    time_ms: Mapped[int | None] = mapped_column(Integer)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_no: Mapped[int] = mapped_column(SmallInteger)
    job_id: Mapped[int] = mapped_column(BigInteger)
    lease_token: Mapped[int] = mapped_column(BigInteger)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text)
    model_digest: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    rubric_version: Mapped[str] = mapped_column(Text)
    eval_settings_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    scoring_version: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    error_code: Mapped[str | None] = mapped_column(Text)


class AnswerEvaluation(Base):
    __tablename__ = "answer_evaluations"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    criterion: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[int] = mapped_column(SmallInteger)
    evidence_status: Mapped[str] = mapped_column(Text)
    insufficient_reason: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    quote_start: Mapped[int | None] = mapped_column(Integer)
    quote_end: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    llm_attempts: Mapped[int] = mapped_column(SmallInteger, default=0)


class AnswerPreEvaluation(Base):
    __tablename__ = "answer_pre_evaluations"
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class RecruiterReport(Base):
    __tablename__ = "recruiter_reports"
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    score_expertise: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_logic: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_attitude: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_delivery: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_coding: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    insufficient_count: Mapped[int] = mapped_column(Integer)
    no_answer_count: Mapped[int] = mapped_column(Integer)
    insufficient_by_criterion: Mapped[dict] = mapped_column(JSONB)
    answered_main_count: Mapped[int] = mapped_column(SmallInteger)
    question_scores: Mapped[list] = mapped_column(JSONB)
    weights: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    strengths: Mapped[list] = mapped_column(JSONB)
    improvements: Mapped[list] = mapped_column(JSONB)
    code_review: Mapped[dict | None] = mapped_column(JSONB)
    point_refs: Mapped[dict | None] = mapped_column(JSONB)  # SC-A08: question numbers behind each point
    proctor_summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class CandidateFeedback(Base):
    __tablename__ = "candidate_feedback"
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    score_expertise: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_logic: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_attitude: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    score_delivery: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    strengths: Mapped[list] = mapped_column(JSONB)
    improvements: Mapped[list] = mapped_column(JSONB)
    coding_all_passed: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class DecisionHistory(Base):
    __tablename__ = "decision_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    from_status: Mapped[str] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class RecruiterNote(Base):
    __tablename__ = "recruiter_notes"
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime | None] = mapped_column(TS)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_role: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger, default=50)
    resource: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="QUEUED")
    run_after: Mapped[datetime] = mapped_column(TS, server_default="now()")
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger)
    lost_leases: Mapped[int] = mapped_column(SmallInteger, default=0)
    infra_deferrals: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[int | None] = mapped_column(BigInteger)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[int | None] = mapped_column(BigInteger)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TS)
    attempt_deadline_at: Mapped[datetime | None] = mapped_column(TS)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    dispatched_at: Mapped[datetime | None] = mapped_column(TS)  # last hand-off to Redis (ADR-0013)


class PostingTemplate(Base):
    """SC-R05: a posting body the company saved to start new postings from (migration 0020)."""
    __tablename__ = "posting_templates"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(Text)
    job_family: Mapped[str | None] = mapped_column(Text)
    experience_level: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    duties: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    preferred: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class InquiryThread(Base):
    """SC-R15: one conversation of a company with an applicant or with the operator (migration 0020)."""
    __tablename__ = "inquiry_threads"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(Text)  # APPLICANT | OPERATOR
    applicant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    applicant_name: Mapped[str | None] = mapped_column(Text)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject: Mapped[str] = mapped_column(Text)
    last_from: Mapped[str] = mapped_column(Text)  # COMPANY | OTHER
    last_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    company_read_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class InquiryMessage(Base):
    __tablename__ = "inquiry_messages"
    id: Mapped[uuid.UUID] = _pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inquiry_threads.id"))
    from_side: Mapped[str] = mapped_column(Text)  # COMPANY | OTHER
    sender_label: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
