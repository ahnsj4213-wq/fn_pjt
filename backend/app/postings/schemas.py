from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

JOB_FAMILIES = {
    "BACKEND": "백엔드", "FRONTEND": "프런트엔드", "DATA": "데이터", "AI_ML": "AI·ML",
    "DEVOPS": "인프라·DevOps", "MOBILE": "모바일", "QA": "QA", "PM": "기획·PM",
}
RUBRIC_VERSION = "rubric-v1"


class PostingIn(BaseModel):
    """Create/update body. All fields optional while drafting; publish checks completeness (JOB-03)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(None, max_length=200)
    job_family: str | None = None
    requirements: str | None = Field(None, max_length=5000)
    preferred: str | None = Field(None, max_length=5000)
    duties: str | None = Field(None, max_length=5000)
    headcount: int | None = Field(None, ge=1, le=1000)
    deadline_date: date | None = None
    coding_enabled: bool = False
    coding_problem_id: UUID | None = None
    coding_time_limit_min: int | None = Field(None, ge=10, le=180)
    row_version: int | None = None  # required on update (optimistic concurrency)

    @field_validator("job_family")
    @classmethod
    def _family(cls, v: str | None) -> str | None:
        if v is not None and v not in JOB_FAMILIES:
            raise ValueError("unknown job_family")
        return v


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expertise: int = Field(ge=0, le=100)
    logic: int = Field(ge=0, le=100)
    specificity: int = Field(ge=0, le=100)
    delivery: int = Field(ge=0, le=100)
    coding: int | None = Field(None, ge=0, le=100)


class EvalSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    weights: Weights
    custom_question: str | None = Field(None, max_length=500)


class EvalSettingsOut(BaseModel):
    version: int
    weights: dict[str, int]
    custom_question: str | None
    rubric_version: str
    locked: bool
    coding_enabled: bool


class PostingPublicOut(BaseModel):
    """What candidates see (and the recruiter preview shows). No weights, no internal settings."""
    id: UUID
    title: str
    company_name: str
    company_intro: str | None
    job_family: str
    job_family_label: str
    requirements: str | None
    preferred: str | None
    duties: str | None
    headcount: int | None
    deadline_date: date | None
    deadline_at: datetime | None
    is_open: bool
    coding_enabled: bool
    coding_language: str | None
    coding_time_limit_min: int | None
    has_applied: bool = False


class PostingSummaryOut(BaseModel):
    id: UUID
    title: str | None
    job_family: str | None
    status: str                     # stored status
    effective_status: str           # OPEN past its deadline shows as CLOSED (JOB-03)
    deadline_date: date | None
    headcount: int | None
    coding_enabled: bool
    applicant_count: int
    completed_count: int
    updated_at: datetime


class PostingDetailOut(PostingSummaryOut):
    requirements: str | None
    preferred: str | None
    duties: str | None
    coding_problem_id: UUID | None
    coding_language: str | None
    coding_time_limit_min: int | None
    settings_locked: bool
    row_version: int
    evaluation_settings: EvalSettingsOut
    weights_reset: bool = False
