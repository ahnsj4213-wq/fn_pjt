"""The question quality gate and its loop (app/questions/quality.py, 2026-11-11): the failure codes by rule, one regeneration
with the reasons, the code-made question after that, and the bound on model calls per slot (never an endless retry)."""
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.questions import compose as C
from app.questions import live, quality
from app.questions import steps
from app.resumes.schemas import ResumeContent
from tests.fixtures import RESUME
from tests.test_handlers import fake_ai, q, setup_app  # noqa: F401 - the fixture
from tests.test_interview import started, url

HAN = "".join(map(chr, (0x7528, 0x4EC0, 0x4E48)))
SNAP = ResumeContent(**RESUME).model_dump()
POSTING = SimpleNamespace(title="백엔드 개발자", duties="주문·결제 API 개발과 운영", requirements="Python 웹 서비스 개발 경험 3년 이상",
                          preferred="대용량 트래픽 경험", experience_level=None, id=uuid.uuid4())


def _ctx(level: str = "JUNIOR", picked=()):
    return SimpleNamespace(app=SimpleNamespace(resume_snapshot=SNAP, id=uuid.uuid4()), posting=POSTING,
                           company=SimpleNamespace(intro=""), level=level, picked=list(picked))


def _resume_pick(seq, text, field="experience[0].description", quote="주문 처리 API의 응답 지연을 줄이기 위해 복합 인덱스를 추가"):
    return C.Picked(seq, "PROJECT", text, "AI", grounding={"evidence": [{"field": field, "text": quote, "start": 0, "end": len(quote)}],
                                                          "sources": [{"type": "RESUME_SNAPSHOT"}]})


def _posting_pick(seq, slot, text, basis="주문·결제 API 개발과 운영", **kw):
    return C.Picked(seq, slot, text, "AI", grounding={"basis": {"text": basis}, "sources": [{"type": "POSTING", "id": "p"}], **kw})


GOOD_JOB = "주문·결제 API 운영을 맡는다면 무엇부터 확인하시겠습니까?"
GOOD_RESUME = "커머스랩 백엔드 개발자 업무에서 주문 처리 API의 응답 지연을 줄이셨는데, 그 방법을 고른 이유는 무엇이었습니까?"


# ── the reasons, one rule at a time ────────────────────────────────────────────
def test_a_sound_question_has_no_reason():
    c = _ctx()
    assert quality.judge(c, _posting_pick(3, "JOB_SKILL", GOOD_JOB)) == []
    assert quality.judge(c, _resume_pick(8, GOOD_RESUME)) == []


def test_grounding_a_resume_period_or_a_quote_not_in_the_resume_is_ungrounded():
    c = _ctx()
    period = _resume_pick(8, "한국대학교 컴퓨터공학 과정에서 2016-2020 경험에 대해 여쭙겠습니다. 그 방법을 고른 이유는 무엇이었습니까?",
                          field="education[0].period", quote="2016-2020")
    made_up = _resume_pick(8, GOOD_RESUME, quote="쿠버네티스 클러스터를 직접 운영했다")
    no_evidence = C.Picked(8, "PROJECT", GOOD_RESUME, "AI", grounding={"sources": []})
    posting = _posting_pick(3, "JOB_SKILL", GOOD_JOB, basis="공고에 없는 문장을 근거로 댔다")
    for p in (period, made_up, no_evidence, posting):
        assert "UNGROUNDED" in quality.judge(c, p), p.text


def test_level_type_and_clarity():
    c = _ctx("NEW")
    hard = _posting_pick(3, "JOB_SKILL", "주문·결제 API를 Kafka와 Redis로 확장성 있게 설계한다면 어떻게 하시겠습니까?", difficulty="BASIC")
    assert {"TOO_HARD", "MANY_TOPICS"} & set(quality.judge(c, hard))
    assert "NOT_MOTIVATION" in quality.judge(c, _posting_pick(2, "MOTIVATION", "주문·결제 API 아키텍처를 어떻게 설계하시겠습니까?"))
    assert "NOT_JOB" in quality.judge(c, _posting_pick(3, "JOB_SKILL", "주문·결제 API 개발과 운영에 지원하게 된 이유는 무엇입니까?"))
    assert "FOREIGN_SCRIPT" in quality.judge(c, _posting_pick(3, "JOB_SKILL", f"주문·결제 API 운영에서 {HAN} 기준을 쓰시겠습니까?"))
    assert "NESTED" in quality.judge(c, _posting_pick(3, "JOB_SKILL", "주문·결제 API 운영에서 트래픽이 몰릴 때 캐시가 없다면 어떻게 하시겠습니까?"))
    assert "NOT_A_QUESTION" in quality.judge(c, _posting_pick(3, "JOB_SKILL", "주문·결제 API 운영을 설명했다"))
    fixed = C.Picked(2, "MOTIVATION", C.MOTIVATION_QUESTION.format(title="아주 긴 이름을 가진 정산 배치 재설계와 주문 이벤트 파이프라인 통합 프로젝트"),
                     "AI", grounding={"sources": [{"type": "KNOWLEDGE", "id": "k"}]}, fixed=True)
    assert quality.judge(c, fixed) == []  # the fixed sentence is code: its length is not the model's


def test_repetition_duplicate_and_same_basis():
    first = _posting_pick(3, "JOB_SKILL", GOOD_JOB)
    c = _ctx(picked=[first])
    assert "DUPLICATE" in quality.judge(c, _posting_pick(4, "JOB_SKILL", GOOD_JOB, basis="대용량 트래픽 경험"))
    same = _posting_pick(4, "JOB_SKILL", "결제 장애가 나면 가장 먼저 무엇을 보시겠습니까?")  # another question, the same posting line
    assert quality.judge(c, same) == ["SAME_BASIS"]
    r1 = _resume_pick(8, GOOD_RESUME)
    r2 = _resume_pick(9, "커머스랩 백엔드 개발자 업무에서 복합 인덱스를 고른 기준은 무엇이었습니까?", quote="복합 인덱스를 추가하고")
    assert "SAME_BASIS" in quality.judge(_ctx(picked=[r1]), r2)  # two quotes from one resume sentence are one part of the resume


def test_custom_questions_are_never_judged_and_the_code_made_ones_pass():
    c = _ctx()
    assert quality.judge(c, C.Picked(1, "PERSONALITY", f"{HAN}?? 회사가 쓴 그대로", "CUSTOM")) == []
    real = C._Ctx(SimpleNamespace(resume_snapshot=SNAP, id=uuid.uuid4()), POSTING, SimpleNamespace(intro="", id=uuid.uuid4()),
                  SimpleNamespace(question_plan=None, custom_question=None, id=uuid.uuid4()), None)
    C.common_fallback(real, [(2, "PERSONALITY"), (10, "COLLABORATION")], "PERSONALITY")
    C.posting_fallback(real, "COMPANY", [(3, "MOTIVATION")])
    C.posting_fallback(real, "JOB", [(4, "JOB_SKILL"), (5, "JOB_SKILL")])
    C.experience_fallback(real, C.to_ai_resume(SNAP), [(8, "PROJECT"), (9, "PROJECT")])
    for p in real.picked:
        assert quality.judge(real, p) == [], (p.text, quality.judge(real, p))


# ── the loop ───────────────────────────────────────────────────────────────────
async def test_the_loop_asks_once_more_with_the_reasons_then_the_code_fills_in():
    c = C._Ctx(SimpleNamespace(resume_snapshot=SNAP, id=uuid.uuid4()), POSTING, SimpleNamespace(intro="", id=uuid.uuid4()),
               SimpleNamespace(question_plan=None, custom_question=None, id=uuid.uuid4()), None)
    seen = []

    async def attempt(c, empty):  # always one foreign-script question
        seen.append((c.attempt, list(c.feedback), list(empty)))
        quality.take(c, _posting_pick(empty[0][0], "JOB_SKILL", f"주문·결제 API 운영에서 {HAN} 기준을 쓰시겠습니까?"))
        return "OK"
    await quality.fill(c, [(3, "JOB_SKILL"), (4, "JOB_SKILL")], attempt, lambda c, rest: C.posting_fallback(c, "JOB", rest))
    assert [a for a, _f, _e in seen] == [0, 1]  # one regeneration, never a third request
    assert seen[0][1] == [] and seen[1][1][0]["codes"] == ["FOREIGN_SCRIPT"]  # the reasons go back to the model
    assert [p.source for p in c.picked] == ["TEMPLATE", "TEMPLATE"] and c.requests == 2 and c.rejects == {"FOREIGN_SCRIPT": 2}


async def test_a_timeout_takes_the_code_made_question_without_a_second_request():
    c = C._Ctx(SimpleNamespace(resume_snapshot=SNAP, id=uuid.uuid4()), POSTING, SimpleNamespace(intro="", id=uuid.uuid4()),
               SimpleNamespace(question_plan=None, custom_question=None, id=uuid.uuid4()), None)
    calls = []

    async def attempt(c, empty):
        calls.append(1)
        return "TIMEOUT"
    await quality.fill(c, [(3, "JOB_SKILL")], attempt, lambda c, rest: C.posting_fallback(c, "JOB", rest))
    assert len(calls) == 1 and [p.source for p in c.picked] == ["TEMPLATE"]


def test_every_reason_has_its_feedback_sentence():
    from app.ai.tasks.questions import CHECK_TEXT
    assert set(quality.REASON_GROUP) <= set(CHECK_TEXT)
    assert set(quality.REASON_GROUP.values()) == {"grounding", "level", "type", "clarity", "repetition"}


def test_the_bound_is_two_requests_with_one_format_repair_each():
    from app.ai.tasks import TASKS
    assert quality.MAX_REQUESTS == 2
    assert all(TASKS[t].regen_limits == {} for t in quality.GEN_TASKS)  # no regeneration hidden inside a task
    assert quality.max_model_calls() == 4


# ── the bound, end to end with the fake model ──────────────────────────────────
def _stubborn(fake):
    """Every question the model writes has foreign script: nothing ever passes."""
    def gen(inp, msgs):
        out = fake._reply_question_generate(inp)
        return {"questions": [{**x, "text": x["text"].replace("본인", f"{HAN} 본인")} for x in out["questions"]]}

    def res(inp, msgs):
        out = fake._reply_resume_question(inp)
        return {"questions": [{**x, "text": x["text"].replace("본인", f"{HAN} 본인")} for x in out["questions"]]}
    fake.responses["question_generate"], fake.responses["resume_question"] = gen, res


@pytest.mark.parametrize("broken", ["stubborn", "invalid"])
async def test_a_model_that_never_passes_makes_at_most_the_bound_and_every_slot_is_filled(client, make_client, fake_ai, broken):
    if broken == "stubborn":
        _stubborn(fake_ai)
    else:  # never valid JSON: every request uses its format repairs
        fake_ai.fail["question_generate"] = fake_ai.fail["resume_question"] = "invalid"
    _, app_id, cand = await setup_app(client, make_client, default_plan=True)
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    (sid,) = (await q("SELECT p.current_settings_id FROM applications a JOIN job_postings p ON p.id=a.posting_id WHERE a.id=:a", a=app_id))[0]
    from app.interview import graph as G
    qset = await G.compose(uuid.UUID(app_id), sid)
    for task, areas in (("resume_question", 1), ("question_generate", 2)):  # 경험 / 지원 동기 + 직무
        calls = [x for x in fake_ai.calls if x.task == task]
        assert len(calls) <= quality.MAX_REQUESTS * areas
        assert sum(len(x.messages) for x in calls) <= quality.max_model_calls() * areas
    rows = await q("SELECT seq, slot_type, source FROM interview_questions WHERE question_set_id=:s ORDER BY seq", s=str(qset))
    assert [r[0] for r in rows] == list(range(1, 11))  # 인성2·동기1·직무3·경험3·협업1, every slot filled
    assert [r[1] for r in rows].count("JOB_SKILL") == 3 and [r[1] for r in rows].count("PROJECT") == 3
    assert {r[2] for r in rows if r[1] in ("MOTIVATION", "JOB_SKILL", "PROJECT")} == {"TEMPLATE"}


async def test_live_composition_of_one_slot_keeps_the_same_bound(client, make_client, voice_ai, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_mode", "live")
    app_id, _cand = await started(client, make_client)
    _stubborn(voice_ai)
    before = len(voice_ai.calls)
    assert await live.ensure_next(uuid.UUID(app_id), ahead=True) is True  # slot 2 (지원 동기 in the five-question plan)
    calls = [x for x in voice_ai.calls[before:] if x.task in quality.GEN_TASKS]
    assert len(calls) <= quality.MAX_REQUESTS and sum(len(x.messages) for x in calls) <= quality.max_model_calls()
    assert await q("SELECT source FROM interview_questions WHERE application_id=:a AND seq=2 AND role='MAIN'", a=app_id) == [("TEMPLATE",)]


# ── code-made questions pass the same gate (PR #2 review) ──────────────────────
def _real(posting=POSTING, snap=SNAP):
    return C._Ctx(SimpleNamespace(resume_snapshot=snap, id=uuid.uuid4()), posting, SimpleNamespace(intro="", id=uuid.uuid4()),
                  SimpleNamespace(question_plan=None, custom_question=None, id=uuid.uuid4()), None)


def _foreign_posting(n: int):
    lines = "\n".join(f"{w} {HAN} 업무 담당과 운영" for w in ("주문 결제", "정산 배치", "로그 수집", "알림 발송")[:n])
    return SimpleNamespace(title="백엔드 개발자", duties=lines, requirements=None, preferred=None, experience_level=None, id=uuid.uuid4())


def _foreign_resume():
    import copy
    r = copy.deepcopy(RESUME)
    r["career"] = [{"company": f"{HAN}랩", "role": "개발자", "period": "2020-2021", "description": "주문 처리 API 개발"},
                   {"company": f"{HAN}웍스", "role": "개발자", "period": "2021-2022", "description": "정산 배치 개발"}]
    r["projects"] = [{"title": f"{HAN} 개선", "role": "리드", "period": "2023", "description": "배치 병렬화"}]
    return ResumeContent(**r).model_dump()


def _safe(p) -> bool:
    return p.source == "TEMPLATE" and p.text in quality.SAFE_TEXT[p.slot] and p.grounding.get("fallback") == "SAFE"


@pytest.mark.parametrize("area", list(quality.SAFE_TEXT))
def test_every_safe_sentence_passes_the_gate_and_none_repeats_another(area):
    texts = quality.SAFE_TEXT[area]
    assert texts
    for i, t in enumerate(texts):
        p = C.Picked(1, area, t, "TEMPLATE", grounding={"sources": [{"type": "TEMPLATE"}], "fallback": "SAFE"})
        assert quality.judge(_real(), p, []) == [], t
        assert not any(C.similar_question(t, u) for u in texts[:i]), t
        assert HAN not in t and "커머스랩" not in t and POSTING.title not in t  # no company or candidate text in it


async def test_a_code_made_job_question_with_a_foreign_script_line_is_replaced_by_a_safe_sentence():
    c = _real(_foreign_posting(3))

    async def attempt(c, empty):
        return "TIMEOUT"
    slots = [(4, "JOB_SKILL"), (5, "JOB_SKILL"), (6, "JOB_SKILL")]  # the default plan's three 직무 slots
    await quality.fill(c, slots, attempt, lambda c, rest: C.posting_fallback(c, "JOB", rest))
    assert [p.seq for p in c.picked] == [4, 5, 6] and all(_safe(p) for p in c.picked)
    assert len({p.text for p in c.picked}) == 3 and c.rejects.get("FOREIGN_SCRIPT") == 3
    assert all(quality.judge(c, p) == [] for p in c.picked)


async def test_common_and_experience_questions_from_a_foreign_script_resume_get_safe_sentences():
    c = _real(snap=_foreign_resume())
    await C._fill_common(None, c, "PERSONALITY", [(2, "PERSONALITY")])
    assert _safe(c.picked[-1]) and c.rejects.get("FOREIGN_SCRIPT") == 1
    start = len(c.picked)
    C.experience_fallback(c, C.to_ai_resume(c.app.resume_snapshot), [(7, "PROJECT"), (8, "PROJECT"), (9, "PROJECT")])
    assert any(HAN in p.text for p in c.picked[start:])
    quality.check_fallback(c, start)  # three 경험 slots: three safe sentences, no composition failure
    assert all(_safe(p) for p in c.picked[start:]) and all(quality.judge(c, p) == [] for p in c.picked)


def test_a_code_made_question_that_passes_is_kept_as_it_is():
    c = _real()
    C.posting_fallback(c, "JOB", [(4, "JOB_SKILL")])
    before = c.picked[0]
    quality.check_fallback(c, 0)
    assert c.picked[0] is before and not _safe(before)


def test_no_passing_sentence_left_fails_the_composition_explicitly(monkeypatch):
    c = _real(_foreign_posting(4))
    C.posting_fallback(c, "JOB", [(3, "JOB_SKILL"), (4, "JOB_SKILL"), (5, "JOB_SKILL"), (6, "JOB_SKILL")])
    with pytest.raises(C.ComposeError):  # four failing slots, three safe sentences: never a question that failed the gate
        quality.check_fallback(c, 0)
    c = _real(_foreign_posting(1))
    C.posting_fallback(c, "JOB", [(4, "JOB_SKILL")])
    monkeypatch.setitem(quality.SAFE_TEXT, "JOB_SKILL", (f"{HAN} 업무를 설명해 주시겠습니까?",))
    with pytest.raises(C.ComposeError):
        quality.check_fallback(c, 0)


async def test_live_fallback_passes_the_gate_too():
    c = _real(_foreign_posting(2))
    asked = _posting_pick(2, "MOTIVATION", "이 회사에 지원하게 된 가장 큰 이유는 무엇입니까?")
    c.picked.append(asked)  # already asked in this interview
    await live._fallback(None, c, C.Slot(4, C.Area.JOB_SKILL, None))
    new = [p for p in c.picked if p.seq == 4]
    assert len(new) == 1 and _safe(new[0]) and quality.judge(c, new[0]) == []
    await live._fallback(None, c, C.Slot(3, C.Area.MOTIVATION, None))  # the safe motivation sentence repeats the asked one
    mot = [p for p in c.picked if p.seq == 3]
    assert len(mot) == 1 and quality.judge(c, mot[0]) == [] and not C.similar_question(mot[0].text, asked.text)


def test_the_steps_module_is_only_nodes():
    assert not hasattr(steps, "build") and not hasattr(steps, "run")  # the one graph is interview/graph.py
