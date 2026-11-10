"""The interview graph's answer steps run at most once in effect (LangGraph refactor 2026-11-10, ②): a step that runs again
after a crash or a second resume of the same answer saves no second answer, makes no second model call and writes no second
follow-up."""
from uuid import UUID

from app.interview import turns
from tests.test_handlers import q
from tests.test_interview import started, url


def _ask(inp, msgs):
    quote = inp["answer"].strip()[:12]
    return {"decision": "ASK", "reason": "VAGUE", "anchor": {"source": "answer", "quote": quote},
            "question": f"말씀하신 '{quote}' 부분을 구체적인 사례로 설명해 주시겠어요?"}


async def _first_answer(client, make_client, voice_ai) -> tuple[str, UUID]:
    app_id, cand = await started(client, make_client)
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    t = (await cand.post(url(app_id, "/interview/next"))).json()["turn"]
    r = await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"),
                        json={"text": "결제 API 응답 지연을 캐시로 줄였습니다. 원인은 조회 쿼리였습니다."})
    assert r.status_code == 202, r.text
    ((answer_id,),) = await q("SELECT id FROM interview_answers WHERE application_id=:a", a=app_id)
    return app_id, answer_id


def _t4_calls(fake) -> int:
    return sum(1 for c in fake.calls if c.task == "followup_decide")


async def test_a_follow_up_is_decided_once_per_answer(client, make_client, voice_ai):
    voice_ai.responses["followup_decide"] = _ask
    app_id, answer_id = await _first_answer(client, make_client, voice_ai)
    assert _t4_calls(voice_ai) == 1
    first = await q("SELECT id FROM interview_questions WHERE application_id=:a AND role='FOLLOWUP'", a=app_id)
    assert len(first) == 1
    again = await turns.decide_followup(UUID(app_id), answer_id)  # the decide step again (crash before its checkpoint)
    assert again is not None and again.id == first[0][0]
    assert _t4_calls(voice_ai) == 1  # no second model call
    assert await q("SELECT count(*) FROM interview_questions WHERE application_id=:a AND role='FOLLOWUP'", a=app_id) == [(1,)]
    assert await q("SELECT followups_used FROM interviews WHERE application_id=:a", a=app_id) == [(1,)]


async def test_a_skipped_follow_up_is_not_asked_again(client, make_client, voice_ai):
    voice_ai.responses["followup_decide"] = lambda inp, msgs: {"decision": "SKIP", "reason": "NONE", "anchor": None, "question": None}
    app_id, answer_id = await _first_answer(client, make_client, voice_ai)
    assert _t4_calls(voice_ai) == 1
    assert await turns.decide_followup(UUID(app_id), answer_id) is None
    assert _t4_calls(voice_ai) == 1
    assert await q("SELECT decision FROM followup_decisions WHERE application_id=:a", a=app_id) == [("SKIP",)]


async def test_finalize_twice_keeps_one_answer(client, make_client, voice_ai):
    voice_ai.responses["followup_decide"] = lambda inp, msgs: {"decision": "SKIP", "reason": "NONE", "anchor": None, "question": None}
    app_id, answer_id = await _first_answer(client, make_client, voice_ai)
    ((turn_id,),) = await q("SELECT turn_id FROM interview_answers WHERE id=:a", a=answer_id)
    from app.core.db import session_scope
    async with session_scope() as db:
        assert await turns.finalize(db, turn_id, end_reason="BUTTON", transcript="다른 텍스트") is None
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM jobs WHERE application_id=:a AND kind='pre_evaluate'", a=app_id) == [(1,)]
