"""T12 question_adapt: a common question from the bank, fitted to this candidate and this company.

The bank holds general questions ("피드백을 받고 일하는 방식을 바꿨던 경험을 말씀해 주세요"). One call takes every bank question of a
question set with the candidate's work (experience and projects of the resume; no name, contact, school or certificates) and the
company documents found for the posting. For each question the model CHOOSES the phrase of the resume or of a document that fits
it (copied, so code can check it is really there) and, for a resume phrase, writes a short GENERAL scene of that kind of work.
The sentence is built by code around the bank question, which stays word for word:

    resume  "<general scene>, <bank question>"   e.g. "예측 결과를 운영에 반영하는 일에서, <bank question>"
    document (only for a question about what one would do)  "<문서 제목> 같은 일을 맡았을 때 <bank question>"

The resume's own words never reach the question (2026-11-09, user: "이력서를 참고한 질문이라는 티가 안나도록"): the scene shares at
most one content word with the chosen phrase and has no numbers; a scene that fails keeps the bank text. The chosen phrase stays
in `grounding.basis` for the recruiter.

Measured on the 6 GB box (2026-11-07): the 4B model asked to write the whole sentence copied the quote and dropped the question, the
same lesson as the resume questions (ADR-0020). A choice that cannot be verified, or a sentence over the length limit, keeps the bank
text: adapting is an improvement, never a reason to lose a question.
"""
import re

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskSpec
from app.ai.tasks.questions import QUESTION_MAX, _OWN_COMPANY, _shape_ok, flatten_resume
from app.ai.textrules import content_tokens, fence
from app.evaluation.quotes import find_span

WORK_FIELDS = re.compile(r"(experience|projects)\[\d+\]\.(description|title|role)$")
_HYPOTHETICAL = re.compile(r"시겠|라면|다면")


class _Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=40)
    basis_quote: str = Field(max_length=400)
    scene: str = Field("", max_length=80)


class AdaptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[_Pick] = Field(max_length=10)


def entry_of(field: str | None) -> str | None:
    """`experience[0].description` -> `experience[0]`: the job or project a resume field belongs to."""
    m = re.match(r"(experience|projects)\[\d+\]", field or "")
    return m.group(0) if m else None


def work_resume(resume: dict) -> dict:
    """Only the work: experience and projects (education, certificates and skills say nothing about a situation)."""
    return {k: resume.get(k) or [] for k in ("experience", "projects")}


SCENE_MIN, SCENE_MAX = 6, 30
_SCENE_END = re.compile(r"(에서|때|중에|면서)$")


def general_scene(scene: str, quote: str) -> str | None:
    """The model's scene when it reads as a kind of work, not as this resume: no numbers, at most one content word of the
    chosen phrase, short, ending as a setting ('~에서', '~때'). None otherwise."""
    s = re.sub(r"\s+", " ", scene or "").strip(" ,.")
    if not (SCENE_MIN <= len(s) <= SCENE_MAX) or not _SCENE_END.search(s) or re.search(r"\d|%", s):
        return None
    if len(content_tokens(s) & content_tokens(quote)) > 1:
        return None  # it repeats the resume
    return s


def fit(original: str, quote: str, kind: str, title: str = "", scene: str = "") -> str | None:
    """The bank question with the chosen situation in front, or None when nothing natural can be built."""
    q = original.strip()
    if kind == "RESUME":
        s = general_scene(scene, quote)
        if s is None:
            return None  # the bank text, rather than the resume's own words in the question
        text = f"{s}, {q}"
    else:
        if not _HYPOTHETICAL.search(q) or not title:
            return None  # a document is not the candidate's past: only a question about what they would do takes it
        text = f"{title} 같은 일을 맡았을 때 {q}"
    return text if len(text) <= QUESTION_MAX and _shape_ok(text) else None


class QuestionAdapt(TaskSpec):
    name = "question_adapt"
    version = "question_adapt@1.3.0"
    lane = "INTERACTIVE"
    temperature = 0.2
    num_predict = 400
    regen_limits = {"gen": 1}
    out_model = AdaptOut
    system = ("너는 면접 질문 편집 보조다. 질문마다 그 질문을 지원자에게 맞추는 데 가장 잘 어울리는 구절 하나를 고른다. 질문 문장은 쓰지 않는다.\n"
              "- 먼저 이력서 경력·프로젝트에서 그 질문(피드백, 갈등, 우선순위, 협업 등)과 어울리는 업무 구절을 고른다. 업무 이름이 드러나는 "
              "짧은 구절(8~40자, 예: '물류 수요 예측 모델 개선')을 basis_quote에 글자 그대로 복사한다.\n"
              "- 이력서 구절을 골랐으면 scene에 그런 종류의 일을 일반적으로 표현한 상황을 6~30자로 쓴다. 이력서의 단어·회사명·기술 이름·"
              "수치를 쓰지 않고 누구나 겪을 수 있는 업무 상황처럼 쓰며, '~에서' 또는 '~때'로 끝낸다. "
              "예: 구절 '물류 수요 예측 모델 개선' → scene '예측 결과를 운영에 반영하는 일에서', "
              "구절 '규칙 기반 경보와 점검 우선순위 수립' → scene '여러 요청을 동시에 처리해야 하는 업무에서'. 회사 자료 구절이면 scene은 비운다.\n"
              "- 이력서에 어울리는 구절이 없을 때만 회사 자료(또는 직무 참고 사례)의 과제 구절을 고른다.\n"
              "- 질문마다 서로 다른 경력·프로젝트에서 고른다. '이미 다른 질문에 쓴' 경력·프로젝트는 고르지 않는다. "
              "출력: {\"questions\": [{\"id\": \"0\", \"basis_quote\": \"...\", \"scene\": \"...\"}]}")

    @staticmethod
    def _sources(inp: dict) -> list[tuple[dict, str, str]]:
        """(reference, text, document title)"""
        flat, _ = flatten_resume(work_resume(inp.get("resume") or {}))
        out = [({"type": "RESUME_SNAPSHOT"}, flat, "")] if flat else []
        out += [({"type": "KNOWLEDGE", "id": k["id"]} | ({"shared": True} if k.get("shared") else {}), f"{k['title']}: {k['text']}", k["title"])
                for k in inp.get("knowledge") or []]
        return out

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        flat, ranges = flatten_resume(work_resume(inp.get("resume") or {}))
        docs = "\n".join(t for ref, t, _ in self._sources(inp) if ref["type"] == "KNOWLEDGE")
        items = "\n".join(f"- id={q['id']} ({q['kind']}): {q['text']}" for q in inp["items"])
        user = (f"질문:\n{fence('BANK', items, nonce)}\n이력서 경력·프로젝트:\n{fence('RESUME', flat or '(없음)', nonce)}")
        avoid = [a["label"] for a in inp.get("avoid") or [] if a.get("label")]
        if avoid:  # the resume entries other questions of this set already ask about
            user += "\n이미 다른 질문에 쓴 경력·프로젝트(여기서는 고르지 않는다):\n" + fence("USED", "\n".join(avoid), nonce)
        if docs:
            user += f"\n회사 자료·직무 참고 사례:\n{fence('DOCS', docs, nonce)}"
        return user, {"flat": flat, "ranges": ranges}

    def check(self, inp: dict, p: AdaptOut, ctx: dict, final: bool) -> Check:
        base = {q["id"]: q["text"] for q in inp["items"]}
        ranges = ctx["ranges"]
        done: dict[str, dict] = {}
        used: set[str] = set()
        # One resume entry (a job, a project) gives one question of the set: entries other questions use, then the ones taken here.
        # A resume with one job and nothing else still gives it to every question: repeating it beats the bare bank text.
        entries: set[str] = {a["entry"] for a in inp.get("avoid") or []}
        every = {e for r in ranges if WORK_FIELDS.match(r[0]) and (e := entry_of(r[0]))}
        for q in p.questions:
            original = base.get(q.id)
            if original is None or q.id in done:
                continue
            found = next(((ref, s, title) for ref, t, title in self._sources(inp) if (s := find_span(q.basis_quote, t, min_chars=8)) is not None), None)
            if found is None or found[1].text in used:
                continue
            ref, span, title = found
            basis = {"text": span.text}
            if ref["type"] == "RESUME_SNAPSHOT":
                owner = next((r for r in ranges if r[1] <= span.start and span.end <= r[2]), None)
                if owner is None or not WORK_FIELDS.match(owner[0]) or (entry_of(owner[0]) in entries and every - entries):
                    continue
                basis |= {"field": owner[0], "start": span.start - owner[1], "end": span.end - owner[1]}
                entries.add(entry_of(owner[0]))
                text = fit(original, span.text, "RESUME", scene=q.scene)
            else:
                text = fit(original, span.text, "DOCUMENT", title)
            if text is None or (ref.get("shared") and _OWN_COMPANY.search(text)):
                continue
            used.add(span.text)
            done[q.id] = {"id": q.id, "text": text, "basis": basis, "source": ref}
        missing = [i for i in base if i not in done]
        if missing and not final:
            return Check(feedback={"gen": f"id {', '.join(missing)}의 구절을 다시 골라라. 이력서 경력·프로젝트 값에서 업무 이름이 드러나는 8~40자 구절을 "
                                          "글자 그대로 복사하고, scene에는 이력서 단어·수치 없이 그런 일을 일반적으로 표현한 6~30자 상황('~에서'로 끝남)을 쓰고, "
                                          "질문마다 다른 경력·프로젝트를 고른다. '이미 다른 질문에 쓴' 경력은 고르지 않는다."})
        return Check(output={"questions": list(done.values())}, flags={"adapted": len(done), "asked": len(base)})
