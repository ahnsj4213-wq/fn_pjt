"""T4 followup_decide (QST-05, 6.2, INT-10; TRD 05 §6 T4). Lane RT, deadline = answer end + 5 s (T27).

Rules before the LLM: follow-up already asked for this main / 4 used -> SKIP(LIMIT); no answer or no-content answer
-> SKIP(RULE). No retries (time budget): any validation failure ends with status INVALID (caller skips, cause INVALID).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskInvalid, TaskSpec
from app.ai.textrules import (accuses_lie, fence, has_p8, is_forbidden_question, is_short_or_no_content,
                              looks_like_question, shares_content)
from app.core.config import get_settings
from app.evaluation.quotes import find_span


class _Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["answer", "resume"]
    quote: str = Field(max_length=400)


class FollowupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ASK", "SKIP"]
    reason: Literal["VAGUE", "NO_EXAMPLE", "INCONSISTENT", "RESUME_MISMATCH", "NONE"]
    anchor: _Anchor | None = None
    question: str | None = Field(default=None, max_length=200)


def _skip(cause: str, reason: str = "NONE") -> dict:
    return {"decision": "SKIP", "reason": reason, "skip_cause": cause, "anchor": None, "question": None}


class FollowupDecide(TaskSpec):
    name = "followup_decide"
    version = "followup_decide@1.0.0"
    lane = "RT"
    temperature = 0.3
    num_predict = 120
    format_retries = 0
    out_model = FollowupOut
    system = ("너는 채용 면접의 꼬리질문 판단기다. 지원자 답변이 (1) 모호하거나(VAGUE) (2) 근거·사례가 없거나(NO_EXAMPLE) "
              "(3) 앞뒤가 맞지 않거나(INCONSISTENT) (4) 제공된 이력서 문장과 다를 때(RESUME_MISMATCH)만 꼬리질문을 만든다. "
              "그 밖에는 decision=SKIP, reason=NONE, anchor=null, question=null.\n"
              "꼬리질문을 만들 때: anchor.quote에 지원자가 실제로 말한 표현(또는 제공된 이력서 문장)을 글자 그대로 복사하고, "
              "질문은 그 표현을 짚어 묻는 한 문장(100자 이내)으로 쓴다. 새 주제를 꺼내지 않는다. 거짓 여부를 판정하지 말고 확인만 한다. "
              "나이·가족·출신·종교 등 사적인 질문을 하지 않는다.")

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
        return user, {}

    def check(self, inp: dict, p: FollowupOut, ctx: dict, final: bool) -> Check:
        if p.decision == "SKIP":
            return Check(output=_skip("LLM", p.reason if p.reason != "NONE" else "NONE"))
        if p.reason == "NONE" or p.anchor is None or not p.question:
            raise TaskInvalid("ASK without reason/anchor/question")
        source = inp["answer"] if p.anchor.source == "answer" else "\n".join(inp.get("resume_evidence") or [])
        span = find_span(p.anchor.quote, source, min_chars=4)
        q = p.question.strip()
        if span is None:
            raise TaskInvalid("anchor not in source")
        if len(q) > 100 or not looks_like_question(q):
            raise TaskInvalid("question shape")
        if not (shares_content(q, span.text) or shares_content(q, inp.get("question", ""))):
            raise TaskInvalid("new topic")
        if accuses_lie(q) or is_forbidden_question(q) or has_p8(q):
            raise TaskInvalid("forbidden wording")
        return Check(output={"decision": "ASK", "reason": p.reason, "skip_cause": None,
                             "anchor": {"source": p.anchor.source, "start": span.start, "end": span.end, "text": span.text},
                             "question": q})
