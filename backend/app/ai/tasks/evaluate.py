"""T5 evaluate_answer (EVL-01~03·07, 6.3, 6.4; TRD 05 §6 T5, ADR-0008).

One call per answer unit (main or follow-up answer). Output per criterion: score 1~5, one quote stored as the
eval_text span (never the LLM string), comment. Rule-based 1-point results never call the LLM:
- is_no_answer            -> evidence_status NO_ANSWER (6.4 '답변 없음')
- very short / no content -> INSUFFICIENT / TOO_SHORT (EVL-02, OQ-09)
Quote mismatch -> one regeneration for the failed items only -> INSUFFICIENT / QUOTE_MISMATCH.
Empty quote with a score above 1 -> one regeneration (same budget as a mismatch); empty quote after that, or
with score 1 -> INSUFFICIENT / NO_EVIDENCE (the 4B model tended to leave logic/delivery empty on solid answers). P-8 wording in a comment -> one regeneration -> sentence removed.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.ai import rubric
from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import fence, has_p8, injection_suspected, is_short_or_no_content, strip_p8
from app.core.config import get_settings
from app.evaluation.quotes import find_span, normalize

QUOTE_MAX_NORM_CHARS = 150


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(max_length=600)
    comment: str = Field(max_length=200)
    score: int = Field(ge=1, le=5)


class EvalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expertise: _Item
    logic: _Item
    specificity: _Item
    delivery: _Item


def _rule_items(status: str, reason: str | None) -> dict:
    return {c: {"score": 1, "evidence_status": status, "insufficient_reason": reason, "quote": None, "comment": ""}
            for c in rubric.KEY_TO_CRITERION.values()}


class EvaluateAnswer(TaskSpec):
    name = "evaluate_answer"
    version = "evaluate_answer@1.1.0"
    lane = "BATCH"
    temperature = 0.0
    num_predict = 450
    regen_limits = {"quote": 1, "p8": 1}
    out_model = EvalOut
    system = ("너는 채용 면접 답변 평가기다. 아래 루브릭의 4개 항목을 각각 1~5점 정수로 판정한다.\n"
              "각 항목마다 순서대로 quote → comment → score를 쓴다.\n"
              "- quote: 답변 원문에서 그 판단의 근거가 되는 연속 구절 하나를 띄어쓰기·문장부호까지 글자 그대로 복사한다(8자 이상, 150자 이하, 생략 부호 금지). "
              "항목별로 인용할 구절: expertise=직무 지식·기술을 쓴 구절, logic=이유나 사례가 이어지는 구절, "
              "specificity=본인의 역할·행동·결과가 드러난 구절, delivery=답변의 핵심(결론·요지)을 밝힌 문장. "
              "논리성·전달력처럼 답변 전체를 보는 항목도 그 판단을 가장 잘 보여 주는 구절 하나를 고른다. "
              "답변 어디에도 해당하는 구절이 없을 때만 빈 문자열을 쓰고 1점을 준다. 지어내지 않는다.\n"
              "- comment: 판단 이유를 한국어 한두 문장(100자 이내)으로 쓴다. 점수 숫자, 합격·불합격, 채용 추천은 쓰지 않는다.\n"
              "- 지원자의 이름·성별·나이 같은 정보는 판단에 쓰지 않는다.\n\n" + rubric.render())

    def preflight(self, inp: dict):
        flags = {"injection_suspected": injection_suspected(inp.get("answer", ""))}
        if inp.get("is_no_answer"):
            return {"items": _rule_items("NO_ANSWER", None), "rubric_version": rubric.RUBRIC_VERSION, "llm": False}, flags
        if is_short_or_no_content(inp.get("answer", ""), get_settings().short_answer_min_chars):
            return {"items": _rule_items("INSUFFICIENT", "TOO_SHORT"), "rubric_version": rubric.RUBRIC_VERSION, "llm": False}, flags
        return None

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        user = (f"질문 유형: {inp.get('question_type', '')}\n"
                f"질문:\n{fence('QUESTION', inp.get('question', ''), nonce)}\n"
                f"지원 직무 요약:\n{fence('JOB', inp.get('job_brief', ''), nonce)}\n"
                f"답변 원문:\n{fence('ANSWER', inp['answer'], nonce)}")
        return user, {"accepted": {}}

    def check(self, inp: dict, parsed: EvalOut, ctx: dict, final: bool) -> Check:
        answer = inp["answer"]
        min_chars = get_settings().quote_min_chars
        accepted: dict = ctx["accepted"]
        flags = {"injection_suspected": injection_suspected(answer), "p8_filtered": ctx.get("p8_filtered", False)}
        bad_quote, bad_p8 = [], []
        for key in rubric.KEYS:
            crit = rubric.KEY_TO_CRITERION[key]
            if crit in accepted:
                continue
            item: _Item = getattr(parsed, key)
            comment = item.comment.strip()
            if has_p8(comment):
                if not final:
                    bad_p8.append(key)
                    continue
                comment = strip_p8(comment)
                flags["p8_filtered"] = ctx["p8_filtered"] = True
            q = item.quote.strip()
            if not q and item.score > 1 and not final:
                bad_quote.append(key)  # a score above 1 needs evidence: ask once for the quote before 근거 부족
                continue
            if not q:
                accepted[crit] = {"score": 1, "evidence_status": "INSUFFICIENT", "insufficient_reason": "NO_EVIDENCE",
                                  "quote": None, "comment": comment}
                continue
            span = find_span(q, answer, min_chars) if len(normalize(q)) <= QUOTE_MAX_NORM_CHARS else None
            if span is None:
                if not final:
                    bad_quote.append(key)
                    continue
                accepted[crit] = {"score": 1, "evidence_status": "INSUFFICIENT", "insufficient_reason": "QUOTE_MISMATCH",
                                  "quote": None, "comment": comment}
                continue
            accepted[crit] = {"score": item.score, "evidence_status": "OK", "insufficient_reason": None,
                              "quote": {"start": span.start, "end": span.end, "text": span.text}, "comment": comment}
        feedback = {}
        if bad_quote:
            feedback["quote"] = (f"다음 항목의 quote가 비어 있거나 답변 원문에 없다: {', '.join(bad_quote)}. 답변 원문에서 띄어쓰기·문장부호까지 "
                                 "그대로 복사한 연속 구절(8자 이상, 150자 이하)로 다시 써라. 비어 있는 quote도 같다. 정말 근거가 없으면 빈 "
                                 "문자열과 1점으로 둔다.")
        if bad_p8:
            feedback["p8"] = f"다음 항목의 comment에 합격·불합격·채용 추천 표현이 있다: {', '.join(bad_p8)}. 그런 표현 없이 다시 써라."
        if feedback:
            feedback["all"] = "4개 항목을 모두 같은 JSON 형식으로 다시 출력하라."
            return Check(feedback=feedback, flags=flags)
        return Check(output={"items": dict(accepted), "rubric_version": rubric.RUBRIC_VERSION, "llm": True}, flags=flags)
