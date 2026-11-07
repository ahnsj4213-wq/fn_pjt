from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Screen spec job categories (SC-A02 category buttons, SC-R04 직무 select). Order = display order.
JOB_FAMILIES = {"DEV": "개발", "DATA_AI": "데이터·AI", "DESIGN": "디자인", "MARKETING": "마케팅", "MGMT": "경영지원"}
# Question areas a recruiter can pick (SC-R07); shown on the posting detail (SC-A03).
QUESTION_AREAS = {"PERSONALITY": "인성", "MOTIVATION": "지원 동기", "JOB_SKILL": "직무 기술",
                  "PROJECT": "프로젝트 경험", "COLLABORATION": "협업"}
DEFAULT_QUESTION_AREAS = list(QUESTION_AREAS)
RUBRIC_VERSION = "rubric-v3"


class PostingIn(BaseModel):
    """Create/update body. All fields optional while drafting; publish checks completeness (JOB-03)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(None, max_length=200)
    job_family: str | None = None
    requirements: str | None = Field(None, max_length=5000)
    preferred: str | None = Field(None, max_length=5000)
    duties: str | None = Field(None, max_length=5000)
    headcount: int | None = Field(None, ge=1, le=99)
    experience_level: str | None = Field(None, max_length=40)
    employment_type: str | None = Field(None, max_length=40)
    location: str | None = Field(None, max_length=100)
    deadline_date: date | None = None
    coding_enabled: bool = False
    coding_problem_id: UUID | None = None
    coding_time_limit_min: int | None = Field(None, ge=10, le=180)
    coding_difficulty: str | None = Field(None, pattern="^(EASY|NORMAL|HARD)$")
    row_version: int | None = None  # required on update (optimistic concurrency)

    @field_validator("job_family")
    @classmethod
    def _family(cls, v: str | None) -> str | None:
        if v is not None and v not in JOB_FAMILIES:
            raise ValueError("unknown job_family")
        return v


class PostingDraftIn(BaseModel):
    """What the recruiter has typed so far; the draft is written from the company's documents."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=200)
    job_family: str | None = None
    experience_level: str | None = Field(None, max_length=40)


class PostingDraftOut(BaseModel):
    duties: str
    requirements: str
    preferred: str
    sources: list[str]  # titles of the documents it was written from


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expertise: int = Field(ge=0, le=100)
    logic: int = Field(ge=0, le=100)
    attitude: int = Field(ge=0, le=100)
    delivery: int = Field(ge=0, le=100)
    coding: int | None = Field(None, ge=0, le=100)


class CustomItemIn(BaseModel):
    """A company's own evaluation item (SC-R07): a name, a one-line description and the 상/중/하 criterion; weight like the others."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str | None = Field(None, pattern=r"^[a-z0-9]{4,12}$")  # omitted on a new item; the server gives one
    name: str = Field(min_length=1, max_length=20)
    description: str = Field("", max_length=60)
    high: str = Field(min_length=1, max_length=80)
    mid: str = Field(min_length=1, max_length=80)
    low: str = Field(min_length=1, max_length=80)
    weight: int = Field(ge=0, le=60)


class PlanSlot(BaseModel):
    """One question of the interview (SC-R07): an area, and the question text or empty for AI/bank."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    area: Literal["PERSONALITY", "MOTIVATION", "JOB_SKILL", "PROJECT", "COLLABORATION"]
    text: str | None = Field(None, max_length=500)


class InterviewSettings(BaseModel):
    """SC-R07 면접 설정. Without a plan the default ten questions are used (core/constants.py DEFAULT_PLAN)."""
    model_config = ConfigDict(extra="forbid")
    question_plan: list[PlanSlot] | None = Field(None, min_length=1, max_length=10)
    think_sec: Literal[5, 10, 20, 30] = 10
    answer_sec: Literal[30, 60, 90, 120] = 60
    followup_max: int = Field(2, ge=0, le=4)


class EvalSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    weights: Weights
    custom_items: list[CustomItemIn] | None = Field(None, max_length=5)  # omitted: the current items stay
    custom_question: str | None = Field(None, max_length=500)
    interview: InterviewSettings | None = None  # omitted: the current interview settings stay
    # SC-R07 질문 분야 (shown on the posting page). Omitted: unchanged. At least one.
    question_areas: list[Literal["PERSONALITY", "MOTIVATION", "JOB_SKILL", "PROJECT", "COLLABORATION"]] | None = Field(None, min_length=1, max_length=5)


class EvalSettingsOut(BaseModel):
    version: int
    weights: dict[str, int]
    custom_items: list[dict] = []
    custom_question: str | None
    interview: InterviewSettings
    question_areas: list[str]
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
    experience_level: str | None = None
    employment_type: str | None = None
    location: str | None = None
    image_url: str | None = None
    question_areas: list[dict] = []   # [{code, label}]
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
    experience_level: str | None = None
    employment_type: str | None = None
    location: str | None = None
    view_count: int = 0
    hidden: bool = False             # taken down by the operator (SC-O02)
    coding_enabled: bool
    applicant_count: int
    completed_count: int
    updated_at: datetime


class PostingDetailOut(PostingSummaryOut):
    requirements: str | None
    preferred: str | None
    duties: str | None
    coding_problem_id: UUID | None
    coding_selection: str = "AUTO"
    coding_difficulty: str = "NORMAL"
    coding_language: str | None
    coding_time_limit_min: int | None
    settings_locked: bool
    row_version: int
    evaluation_settings: EvalSettingsOut
    weights_reset: bool = False
