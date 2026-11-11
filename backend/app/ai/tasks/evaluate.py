"""T5 evaluate_answer (EVL-01~03·07, 6.3, 6.4; TRD 05 §6 T5, ADR-0008, ADR-0023).

One call per answer unit (main or follow-up answer). The model decides every score; code checks the form and the evidence.

Evidence by sentence number (ADR-0023, was a copied quote up to evaluate_answer@1.4.0): code cuts the answer into numbered
sentences, the model names one number per criterion, and the stored evidence is that sentence's span of eval_text. The model never
writes evidence text, so it cannot misquote or invent; a failed citation no longer turns a real answer into the lowest score.

Evidence status per criterion (requirement 7 of 2026-11-09):
- OK            the model's score with the sentence it named
- UNLINKED      근거 미연결: the model scored 3 or more but named no sentence of the answer. The model's score stays (the
                extraction failed, not the answer); no quote is shown and the report says so. Asking again is no longer done
                here (2.2.0): the unit's verification loop (app/evaluation/checks.py) asks once per unit with every reason
                found, so the samples of one unit do not each spend their own re-ask.
- INSUFFICIENT  내용 부족: LOW_CONTENT = the model scored 1~2 and named no sentence (nothing in the answer to point at), or the
                rule before the model, TOO_SHORT (normalised < 15 chars or a no-content phrase, OQ-09): 1 point.
- NO_ANSWER     답변 없음 (rule, 6.4): 1 point on every criterion.
평가 불가 (the model gave nothing usable after its retries) is not a criterion status: the run fails and the report is held for
re-evaluation (EVL-06), so no provisional score is ever shown.
P-8 wording in a comment -> one regeneration -> the sentence is removed.

`review` (optional input, 2.2.0): the reasons a server check found in the previous judgement of the same answer, written by code
(checks.review_lines: criterion, reason, the earlier score). Only present in the one re-judgement of a unit.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.ai import rubric
from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import fence, has_p8, injection_suspected, is_short_or_no_content, strip_p8
from app.core.config import get_settings
from app.evaluation.quotes import numbered, sentences

LINK_MIN_SCORE = 3  # a score this high claims something the answer shows: without a sentence it is asked again, then UNLINKED


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sent: int = Field(ge=0, le=99)  # sentence number of the answer, 0 = no sentence to point at
    comment: str = Field(max_length=120)
    score: int = Field(ge=1, le=5)


class _Sub3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int = Field(ge=1, le=5)
    b: int = Field(ge=1, le=5)
    c: int = Field(ge=1, le=5)


class _Star(BaseModel):
    model_config = ConfigDict(extra="forbid")
    situation: int = Field(ge=0, le=1)
    task: int = Field(ge=0, le=1)
    action: int = Field(ge=0, le=1)
    result: int = Field(ge=0, le=1)


class _Found(BaseModel):
    """Where the answer says each part of its reasoning (sentence numbers, 0 = nowhere). Written before the scores so the model
    reads the answer before judging it; the scores stay the model's own. Stored with the detail, never scored by code."""
    model_config = ConfigDict(extra="forbid")
    problem: int = Field(ge=0, le=99)
    cause: int = Field(ge=0, le=99)
    grounds: int = Field(ge=0, le=99)
    alternatives: int = Field(ge=0, le=99)
    action: int = Field(ge=0, le=99)
    result: int = Field(ge=0, le=99)
    knowledge: int = Field(ge=0, le=99)


class EvalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    found: _Found | None = None  # optional here so an older reply without it still validates; the grammar twin requires it
    expertise: _Item
    logic: _Item
    attitude: _Item
    delivery: _Item
    # Sub-items (SC-A08): only small integers, so the extra output stays short. Optional here so that an older reply without
    # them still validates; the grammar twin below requires them.
    expertise_sub: _Sub3 | None = None  # a=개념 정확성, b=실무 적용, c=깊이
    logic_sub: _Sub3 | None = None  # a=주장 명확성, b=근거 제시, c=논리적 흐름
    star: _Star | None = None


class _ItemG(BaseModel):
    """llama.cpp grammar twin: a short comment keeps the report ready in seconds."""
    model_config = ConfigDict(extra="forbid")
    sent: int = Field(ge=0, le=99)
    comment: str = Field(max_length=40)
    score: int = Field(ge=1, le=5)


class EvalOutG(BaseModel):
    model_config = ConfigDict(extra="forbid")
    found: _Found
    expertise: _ItemG
    logic: _ItemG
    attitude: _ItemG
    delivery: _ItemG
    expertise_sub: _Sub3
    logic_sub: _Sub3
    star: _Star


def _rule_items(status: str, reason: str | None) -> dict:
    return {c: {"score": 1, "evidence_status": status, "insufficient_reason": reason, "quote": None, "comment": ""}
            for c in rubric.KEY_TO_CRITERION.values()}


class EvaluateAnswer(TaskSpec):
    name = "evaluate_answer"
    version = "evaluate_answer@2.2.0"
    lane = "BATCH"
    temperature = 0.0
    num_predict = 420  # numbers instead of quotes: ~250 tokens with found and the sub-items, the margin keeps a long comment from being cut
    regen_limits = {"p8": 1, "example": 1}  # evidence: once per unit, app/evaluation/checks.py
    out_model = EvalOut
    grammar_model = EvalOutG
    system = ("너는 채용 면접 답변 평가기다. 아래 루브릭의 4개 항목을 각각 1~5점 정수로 판정한다.\n"
              "답변 원문은 [번호]가 붙은 문장 목록으로 주어진다.\n"
              "먼저 found에 답변의 각 부분이 드러난 문장 번호를 쓴다(없으면 0): problem=문제 상황, cause=원인 분석, grounds=판단 근거(왜 그렇게 "
              "했는가), alternatives=검토하거나 비교한 대안, action=실행 내용·방법, result=결과나 그 확인 방법, knowledge=직무 지식·기술 설명. "
              "문장을 하나씩 읽고 표시한다. 그 다음 4개 항목을 판정한다: found에서 번호가 있는 부분을 '없다'고 하지 않는다.\n"
              "각 항목마다 순서대로 sent → comment → score를 쓴다.\n"
              "- sent: 그 항목 판단의 근거가 가장 잘 드러난 문장의 번호 하나. 문장을 다시 쓰지 말고 번호만 쓴다. "
              "낮은 점수라면 부족함이 드러난 문장을 고른다. 근거로 가리킬 문장이 답변에 정말 없을 때만 0을 쓴다.\n"
              "- comment: 판단 이유를 한국어 한 문장(30자 이내)으로 쓴다. 답변에 없는 내용을 지어내지 않는다. "
              "점수 숫자, 합격·불합격, 채용 추천은 쓰지 않는다. 루브릭 참고 사례에만 나오는 기술·수치·행동을 답변에 있었던 것처럼 쓰지 않는다.\n"
              "- score: 답변 원문에 있는 사실만으로, 질문 유형과 지원자 수준에 맞춰 루브릭의 점수별 기준 문장과 답변의 의미를 비교해 정한다. "
              "참고 사례는 수준을 가늠하는 데만 쓰고, 사례와 표현·기술·길이가 비슷한지는 점수에 쓰지 않는다. "
              "같은 내용을 다른 말로 한 답변은 같은 점수다.\n"
              "- 지원자의 이름·성별·나이 같은 정보는 판단에 쓰지 않는다.\n"
              "- 항목 4개 뒤에 세부 점수를 쓴다(숫자만): expertise_sub와 logic_sub는 a·b·c를 각각 1~5점으로, "
              "star는 답변에 상황·과제·행동·결과 각각이 있으면 1, 없으면 0으로 쓴다.\n\n" + rubric.render())

    def preflight(self, inp: dict):
        flags = {"injection_suspected": injection_suspected(inp.get("answer", ""))}
        if inp.get("is_no_answer"):
            return {"items": _rule_items("NO_ANSWER", None), "rubric_version": rubric.RUBRIC_VERSION, "llm": False}, flags
        if is_short_or_no_content(inp.get("answer", ""), get_settings().short_answer_min_chars):
            return {"items": _rule_items("INSUFFICIENT", "TOO_SHORT"), "rubric_version": rubric.RUBRIC_VERSION, "llm": False}, flags
        return None

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        sents = sentences(inp["answer"])
        kind = inp.get("question_kind") or rubric.question_kind(inp.get("question_type"), inp.get("question", ""), bool(inp.get("code")))
        user = (f"{rubric.render_kind(kind, inp.get('level'))}\n"
                f"질문:\n{fence('QUESTION', inp.get('question', ''), nonce)}\n"
                f"지원 직무 요약:\n{fence('JOB', inp.get('job_brief', ''), nonce)}\n"
                f"답변 원문(문장 {len(sents)}개):\n{fence('ANSWER', numbered(sents), nonce)}")
        if inp.get("code"):
            # Live coding (SC-A06): the code informs expertise/logic; the evidence sentence is still one of the spoken answer.
            user += (f"\n제출한 코드({inp.get('code_language') or '언어 미상'}, 실행하지 않음. 직무 전문성·논리성 판단에 참고하고, "
                     f"sent는 답변 문장 번호에서만 고른다):\n{fence('CODE', inp['code'], nonce)}")
        if inp.get("review"):  # the one re-judgement of a unit (checks.py): reasons written by code, no answer text
            user += ("\n\n같은 답변의 이전 판정을 서버가 검사한 결과(다시 판정할 이유):\n" + "\n".join(f"- {x}" for x in inp["review"])
                     + "\n답변 문장 목록과 루브릭의 점수별 기준 문장을 다시 대조해 4개 항목을 모두 다시 판정하라. 점수는 답변 원문으로만 정한다.")
        return user,{"accepted": {}, "sentences": sents, "kind": kind}

    def check(self, inp: dict, parsed: EvalOut, ctx: dict, final: bool) -> Check:
        answer, sents = inp["answer"], ctx["sentences"]
        accepted: dict = ctx["accepted"]
        flags = {"injection_suspected": injection_suspected(answer), "p8_filtered": ctx.get("p8_filtered", False),
                 "example_leak": ctx.get("example_leak", False)}
        bad_p8, bad_example = [], []
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
            leaked = example_leak(comment, answer)
            if leaked:  # the reference case taken for the answer (rubric-v5): ask once, then drop the comment, keep the score
                if not final:
                    bad_example.append(f"{key}({', '.join(leaked)})")
                    continue
                comment = ""
                flags["example_leak"] = ctx["example_leak"] = True
            if 1 <= item.sent <= len(sents):
                s = sents[item.sent - 1]
                accepted[crit] = {"score": item.score, "evidence_status": "OK", "insufficient_reason": None,
                                  "quote": {"start": s.start, "end": s.end, "text": s.text, "sent": item.sent}, "comment": comment}
                continue
            # No sentence of the answer was named: 0, or a number the answer does not have. Settled here; the unit's
            # verification loop (checks.py) asks again once for the whole unit when the score claims something (3+).
            if item.score < LINK_MIN_SCORE:  # nothing to point at and a low score: the content is lacking
                accepted[crit] = _lacking(item.score, comment)
            else:  # the score stays: a failed citation is not a weak answer
                accepted[crit] = {"score": item.score, "evidence_status": "UNLINKED", "insufficient_reason": None,
                                  "quote": None, "comment": comment}
        feedback = {}
        if bad_p8:
            feedback["p8"] = f"다음 항목의 comment에 합격·불합격·채용 추천 표현이 있다: {', '.join(bad_p8)}. 그런 표현 없이 다시 써라."
        if bad_example:
            feedback["example"] = (f"다음 항목의 comment에 답변에 없는 말(루브릭 참고 사례의 내용)이 있다: {'; '.join(bad_example)}. "
                                   "참고 사례는 답변이 아니다. 답변 원문에 있는 내용만으로 판단을 다시 하고 comment를 다시 써라.")
        if feedback:
            feedback["all"] = "4개 항목을 모두 같은 JSON 형식으로 다시 출력하라."
            return Check(feedback=feedback, flags=flags)
        detail = {k: getattr(parsed, k).model_dump() for k in ("found", "expertise_sub", "logic_sub", "star") if getattr(parsed, k) is not None}
        if "found" in detail:  # a number the answer does not have points nowhere
            detail["found"] = {k: (v if 1 <= v <= len(sents) else 0) for k, v in detail["found"].items()}
        return Check(output={"items": dict(accepted), "detail": detail or None, "rubric_version": rubric.RUBRIC_VERSION,
                             "question_kind": ctx["kind"], "llm": True}, flags=flags)


def example_leak(comment: str, answer: str) -> list[str]:
    """Reference-case words (rubric.EXAMPLE_TERMS) the comment uses but the answer does not have."""
    flat = (answer or "").replace(" ", "")
    return [t for t in rubric.EXAMPLE_TERMS if t in (comment or "") and t.replace(" ", "") not in flat]


def _lacking(score: int, comment: str) -> dict:
    return {"score": score, "evidence_status": "INSUFFICIENT", "insufficient_reason": "LOW_CONTENT", "quote": None, "comment": comment}
