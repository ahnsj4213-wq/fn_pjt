"""T1 resume_extract (RES-03, AC-06; TRD 05 §6 T1). Output keys = resume-schema v1.

Every string value must be copied verbatim from the PDF text; values that fail the ADR-0008 source check are dropped
(counted in flags.dropped_values) so only text actually written in the resume reaches the form (RES-03).
Long sources are split at page/line boundaries and merged.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import fence
from app.evaluation.quotes import find_span

CHUNK_CHARS = 6000
S = str | None


class _Edu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school: str
    major: S = None
    degree: S = None
    period: S = None


class _Exp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    role: S = None
    period: S = None
    description: S = Field(default=None, max_length=1200)


class _Proj(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    role: S = None
    period: S = None
    description: S = Field(default=None, max_length=1200)


class _Cert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    issuer: S = None
    date: S = None


class ResumeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: S = None
    education: list[_Edu] = []
    experience: list[_Exp] = []
    projects: list[_Proj] = []
    skills: list[str] = []
    certifications: list[_Cert] = []


REQUIRED = {"education": "school", "experience": "company", "projects": "title", "certifications": "name"}
DESC_MAX = 300


def _verify(value: str | None, source: str) -> str | None:
    if value is None or not value.strip():
        return None
    span = find_span(value.strip(), source, min_chars=1)
    return span.text if span else None


class ResumeExtract(TaskSpec):
    name = "resume_extract"
    version = "resume_extract@1.0.0"
    lane = "INTERACTIVE"
    temperature = 0.0
    num_predict = 1500
    out_model = ResumeOut
    system = ("너는 이력서 텍스트에서 표준 항목(성명, 학력, 경력, 프로젝트, 기술 스택, 자격증)을 뽑는 추출기다. "
              "모든 값은 원문에 적힌 글자를 그대로 복사한다. 날짜·학위명·회사명을 바꿔 쓰거나 요약·번역하지 않는다. "
              "원문에 없는 항목은 null 또는 빈 배열로 둔다. description은 원문의 연속 구절(300자 이내)만 쓴다.")

    def split(self, inp: dict) -> list[dict]:
        text = inp["source_text"]
        if len(text) <= CHUNK_CHARS:
            return [inp]
        chunks, cur = [], ""
        for line in text.splitlines(keepends=True):
            if len(cur) + len(line) > CHUNK_CHARS and cur:
                chunks.append(cur)
                cur = ""
            cur += line
        if cur:
            chunks.append(cur)
        return [{"source_text": c} for c in chunks]

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        return f"이력서 원문:\n{fence('RESUME', inp['source_text'], nonce)}", {}

    def check(self, inp: dict, p: ResumeOut, ctx: dict, final: bool) -> Check:
        src = inp["source_text"]
        dropped = 0
        out: dict = {"name": None, "education": [], "experience": [], "projects": [], "skills": [], "certifications": []}
        if p.name:
            out["name"] = _verify(p.name, src)
            dropped += out["name"] is None
        for section, key in REQUIRED.items():
            for item in getattr(p, section):
                row = {}
                for f, v in item.model_dump().items():
                    if v is None:
                        row[f] = None
                        continue
                    if f == "description":
                        v = v[:DESC_MAX]
                    row[f] = _verify(v, src)
                    dropped += row[f] is None
                if row.get(key):
                    out[section].append(row)
        for s in p.skills:
            v = _verify(s, src)
            if v:
                out["skills"].append(v)
            else:
                dropped += 1
        return Check(output=out, flags={"dropped_values": dropped})

    def merge(self, outputs: list[dict], flags: list[dict]) -> dict:
        merged = {"name": None, "education": [], "experience": [], "projects": [], "skills": [], "certifications": []}
        for o in outputs:
            merged["name"] = merged["name"] or o["name"]
            for k in ("education", "experience", "projects", "certifications"):
                for row in o[k]:
                    if row not in merged[k]:
                        merged[k].append(row)
            for s in o["skills"]:
                if s not in merged["skills"]:
                    merged["skills"].append(s)
        return merged
