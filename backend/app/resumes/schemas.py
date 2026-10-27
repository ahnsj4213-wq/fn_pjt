"""Standard resume fields (RES-03) and the flattened text used to ground resume questions (QST-03)."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Education(_Item):
    school: str = Field(min_length=1, max_length=200)
    major: str | None = Field(None, max_length=200)
    degree: str | None = Field(None, max_length=100)
    period: str | None = Field(None, max_length=100)


class Career(_Item):
    company: str = Field(min_length=1, max_length=200)
    role: str | None = Field(None, max_length=200)
    period: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=3000)


class Project(_Item):
    title: str = Field(min_length=1, max_length=200)
    role: str | None = Field(None, max_length=200)
    period: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=3000)


class Certificate(_Item):
    name: str = Field(min_length=1, max_length=200)
    issuer: str | None = Field(None, max_length=200)
    date: str | None = Field(None, max_length=50)


class ResumeContent(_Item):
    name: str = Field(min_length=1, max_length=50)
    education: list[Education] = Field(default_factory=list, max_length=20)
    career: list[Career] = Field(default_factory=list, max_length=30)
    projects: list[Project] = Field(default_factory=list, max_length=30)
    skills: list[str] = Field(default_factory=list, max_length=100)
    certificates: list[Certificate] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _minimum(self) -> "ResumeContent":
        # RES-06: name + (at least one education or one career entry)
        if not (self.education or self.career):
            raise ValueError("RESUME_MIN_FIELDS")
        return self


# The AI contract (TRD 05 T1 "resume-schema v1") names two sections differently from the stored form/snapshot.
_AI_TO_FORM = {"experience": "career", "certifications": "certificates"}
_FORM_TO_AI = {v: k for k, v in _AI_TO_FORM.items()}


def from_ai_resume(fields: dict) -> dict:
    """T1 output -> the form/snapshot shape (career, certificates). Unknown keys are dropped."""
    out = {"name": fields.get("name"), "education": [], "career": [], "projects": [], "skills": [], "certificates": []}
    for k, v in fields.items():
        key = _AI_TO_FORM.get(k, k)
        if key in out and key != "name":
            out[key] = list(v or [])
    return out


def to_ai_resume(content: dict) -> dict:
    """Snapshot -> T3 input (experience, certifications). The name is not passed (questions never need it)."""
    return {_FORM_TO_AI.get(k, k): v for k, v in (content or {}).items() if k != "name"}


def flatten(c: ResumeContent) -> str:
    """Plain text of the snapshot without name/contact: the only source resume questions may quote (QST-03)."""
    lines: list[str] = []
    for e in c.education:
        lines.append(" ".join(x for x in ("학력:", e.school, e.major, e.degree, e.period) if x))
    for w in c.career:
        lines.append(" ".join(x for x in ("경력:", w.company, w.role, w.period) if x))
        if w.description:
            lines.append(w.description)
    for p in c.projects:
        lines.append(" ".join(x for x in ("프로젝트:", p.title, p.role, p.period) if x))
        if p.description:
            lines.append(p.description)
    if c.skills:
        lines.append("기술 스택: " + ", ".join(c.skills))
    for cert in c.certificates:
        lines.append(" ".join(x for x in ("자격증:", cert.name, cert.issuer, cert.date) if x))
    return "\n".join(lines)
