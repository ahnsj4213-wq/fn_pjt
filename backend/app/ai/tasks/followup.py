"""T4 followup_decide (QST-05, 6.2, INT-10; TRD 05 §6 T4). Lane RT, deadline = answer end + 30 s (ADR-0019).

Rules before the LLM: follow-up already asked for this main / 4 used -> SKIP(LIMIT); no answer or no-content answer
-> SKIP(RULE). An anchor the source does not hold is asked again once; any other validation failure ends with status INVALID
(caller skips, cause INVALID).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskInvalid, TaskSpec
from app.ai.tasks.questions import similar_question, unsupported_terms
from app.ai.textrules import (accuses_lie, content_tokens, fence, has_p8, is_forbidden_question, is_short_or_no_content,
                              looks_like_question, shares_content)
from app.core.config import get_settings
from app.evaluation.quotes import find_span


class _Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["answer", "resume", "previous"]
    quote: str = Field(max_length=400)


class FollowupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ASK", "SKIP"]
    reason: Literal["VAGUE", "NO_EXAMPLE", "INCONSISTENT", "RESUME_MISMATCH", "DEEPEN", "NONE"]
    anchor: _Anchor | None = None
    question: str | None = Field(default=None, max_length=200)


def _skip(cause: str, reason: str = "NONE") -> dict:
    return {"decision": "SKIP", "reason": reason, "skip_cause": cause, "anchor": None, "question": None}


class FollowupDecide(TaskSpec):
    name = "followup_decide"
    version = "followup_decide@1.3.0"
    lane = "RT"
    temperature = 0.3
    num_predict = 160
    format_retries = 1
    regen_limits = {"quote": 1}
    out_model = FollowupOut
    system = ("너는 실제 채용 면접의 면접관이다. 지원자의 답변을 듣고 바로 이어서 물을 꼬리질문을 정한다. "
              "내용이 있는 답변이면 잘한 답변이어도 꼬리질문을 만든다.\n"
              "- 답변에서 지원자가 실제로 말한 구체적인 표현(기술, 수치, 결정, 결과) 하나를 고르고, 왜 그렇게 했는지, 어떻게 확인했는지, "
              "다른 대안은 무엇이었는지, 본인의 역할은 무엇이었는지를 묻는다.\n"
              "- '공고와 이력서의 공통 항목'에 있는 기술·경험이 답변에 나오면 그것을 먼저 고른다.\n"
              "- reason: 모호하면 VAGUE, 근거·사례가 없으면 NO_EXAMPLE, 앞뒤가 다르면 INCONSISTENT, 이력서 문장과 다르면 RESUME_MISMATCH, "
              "문제는 없지만 더 깊이 확인할 때는 DEEPEN.\n"
              "- 내용이 없는 답변은 이미 걸러졌다. 그러므로 decision은 항상 ASK이고 reason은 NONE이 될 수 없다.\n"
              "- '앞선 답변'과 이어지거나 어긋나는 점이 있으면 그것을 물어도 된다. 이때 anchor.source는 previous다.\n"
              "- '회사 자료'의 프로젝트·기술이 답변 내용과 직접 관련되면, 답변의 표현을 그 상황에 연결해 물어도 된다. 회사 자료에 없는 회사 사실은 말하지 않는다.\n"
              "- '이미 한 꼬리질문'과 같은 내용을 다시 묻지 않는다.\n"
              "- anchor.quote에 고른 표현을 글자 그대로 복사하고, 질문은 그 표현을 넣은 60자 이내의 짧은 의문문으로 쓰고 '?'로 끝낸다. 답변 문장을 되풀이하지 않는다. "
              "형태 예: '재순위화 모델을 고른 이유는 무엇입니까?' "
              "새 주제를 꺼내지 않는다. 거짓 여부를 판정하지 말고 확인만 한다. 나이·가족·출신·종교 등 사적인 질문을 하지 않는다.")

    def preflight(self, inp: dict):
        if inp.get("has_followup") or int(inp.get("followups_left", 0)) <= 0:
            return _skip("LIMIT"), {}
        if inp.get("is_no_answer") or is_short_or_no_content(inp.get("answer", ""), get_settings().short_answer_min_chars):
            return _skip("RULE"), {}
        return None

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        resume = "\n".join(inp.get("resume_evidence") or [])
        user = (f"질문 유형: {inp.get('question_type', '')}\n본질문:\n{fence('QUESTION', inp.get('question', ''), nonce)}\n"
                f"답변:\n{fence('ANSWER', inp['answer'], nonce)}\n")
        if resume:
            user += f"이력서 근거 문장:\n{fence('RESUME', resume, nonce)}\n"
        if inp.get("job_brief"):
            user += f"지원 공고:\n{fence('JOB', inp['job_brief'], nonce)}\n"
        if inp.get("shared_terms"):
            user += f"공고와 이력서의 공통 항목: {', '.join(inp['shared_terms'])}\n"
        previous = "\n".join(p["text"] for p in inp.get("previous_answers") or [])
        if previous:
            user += f"앞선 답변:\n{fence('PREVIOUS', previous, nonce)}\n"
        docs = "\n".join(f"{d['title']}: {d['text']}" for d in inp.get("company_context") or [])
        if docs:
            user += f"회사 자료:\n{fence('COMPANY_DOCS', docs, nonce)}\n"
        if inp.get("asked"):
            user += "이미 한 꼬리질문: " + " / ".join(inp["asked"]) + "\n"
        return user, {}

    @staticmethod
    def _anchor_source(inp: dict, source: str, quote: str) -> tuple[str, dict | None]:
        """The text the anchor must be in, and what to record as the question's source."""
        if source == "answer":
            return inp["answer"], {"type": "ANSWER", "id": inp.get("answer_id")}
        if source == "resume":
            return "\n".join(inp.get("resume_evidence") or []), {"type": "RESUME_SNAPSHOT", "id": inp.get("application_id")}
        for p in inp.get("previous_answers") or []:
            if find_span(quote, p["text"], min_chars=4) is not None:
                return p["text"], {"type": "ANSWER", "id": p["id"]}
        return "", None

    @staticmethod
    def _knowledge_used(inp: dict, q: str) -> list[dict]:
        """Documents the question really draws on: it names their title's words or their technologies, and the answer did not.
        Everyday words ("방식", "효과") overlap by chance, and reading a document is not using it."""
        new = content_tokens(q) - content_tokens(inp.get("answer", "")) - content_tokens(inp.get("question", ""))
        return [{"type": "KNOWLEDGE", "id": d["id"]} for d in inp.get("company_context") or []
                if new & (content_tokens(d["title"]) | {t for t in content_tokens(d["text"]) if t.isascii()})]

    def check(self, inp: dict, p: FollowupOut, ctx: dict, final: bool) -> Check:
        if p.decision == "SKIP":
            return Check(output=_skip("LLM", p.reason if p.reason != "NONE" else "NONE"))
        if p.reason == "NONE" or p.anchor is None or not p.question:
            raise TaskInvalid("ASK without reason/anchor/question")
        source, ref = self._anchor_source(inp, p.anchor.source, p.anchor.quote)
        span = find_span(p.anchor.quote, source, min_chars=4)
        q = p.question.strip()
        if span is None:
            if final:
                raise TaskInvalid("anchor not in source")
            return Check(feedback={"quote": "anchor.quote가 원문에 그대로 없다. 원문에 실제로 있는 표현을 고쳐 쓰지 말고 글자 그대로 복사해 다시 출력하라."})
        if len(q) > 100 or not looks_like_question(q):
            raise TaskInvalid("question shape")
        if not (shares_content(q, span.text) or shares_content(q, inp.get("question", ""))):
            raise TaskInvalid("new topic")
        if accuses_lie(q) or is_forbidden_question(q) or has_p8(q):
            raise TaskInvalid("forbidden wording")
        known = "\n".join([inp["answer"], inp.get("question", ""), *(inp.get("resume_evidence") or []),
                           *(x["text"] for x in inp.get("previous_answers") or []),
                           *(f"{d['title']} {d['text']}" for d in inp.get("company_context") or [])])
        if unsupported_terms(q, known):
            raise TaskInvalid("term not in any source")
        if any(similar_question(q, a) for a in inp.get("asked") or []):
            if final:
                raise TaskInvalid("repeats an earlier follow-up")
            return Check(feedback={"quote": "이미 한 꼬리질문과 같은 내용이다. 답변의 다른 표현을 골라 다른 내용을 물어라."})
        return Check(output={"decision": "ASK", "reason": p.reason, "skip_cause": None,
                             "anchor": {"source": p.anchor.source, "start": span.start, "end": span.end, "text": span.text},
                             "question": q, "sources": [ref, *self._knowledge_used(inp, q)]})
