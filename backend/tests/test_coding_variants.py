"""Coding problem templates and per-application variants: generated hidden tests, solved by code, never shared."""
import random
from collections import Counter
from uuid import uuid4

from sqlalchemy import select

from app.coding import templates as T
from app.coding.variants import ensure_variant, seed_for, statement_preserves
from app.core.db import session_scope
from app.models import CodingProblem, CodingTestcase


def test_every_template_generates_valid_cases_and_changes_with_the_seed():
    assert len(T.TEMPLATES) >= 50
    for t in T.TEMPLATES.values():
        a, b = t.hidden(random.Random(1)), t.hidden(random.Random(2))
        assert len(a) >= 5 and a == t.hidden(random.Random(1)), t.key  # deterministic per seed
        assert a != b, t.key  # a different applicant gets different hidden inputs
        for inp in [*t.public, *a]:
            out = t.solve(inp)
            assert out.endswith("\n") and out.strip() != "", t.key
        assert "### 입력" in t.statement and "### 출력" in t.statement


def test_reference_solutions_agree_with_brute_force():
    rnd = random.Random(7)
    for _ in range(200):
        a = [rnd.randint(-3, 3) for _ in range(rnd.randint(2, 9))]
        tgt = rnd.randint(-4, 4)
        brute = sum(1 for i in range(len(a)) for j in range(i + 1, len(a)) if a[i] + a[j] == tgt)
        assert T.TEMPLATES["pair_count"].solve(T._pairs_in(a, tgt)) == f"{brute}\n"
        iv = [(s, s + rnd.randint(1, 6)) for s in (rnd.randint(0, 12) for _ in range(rnd.randint(1, 8)))]
        pts = {x for s, e in iv for x in (s, e)}
        assert T.TEMPLATES["meeting_rooms"].solve(T._rooms_in(iv)) == f"{max(sum(1 for s, e in iv if s <= x < e) for x in pts)}\n"
        b = [rnd.randint(-9, 9) for _ in range(rnd.randint(2, 7))]
        best = max(b[i] * b[j] for i in range(len(b)) for j in range(len(b)) if i != j)
        assert T.TEMPLATES["top_product"].solve(T._tp_in(b)) == f"{best}\n"
        s = "".join(rnd.choice("()[]{}") for _ in range(rnd.randint(0, 8)))
        stack, ok = [], True
        for ch in s:
            if ch in "([{":
                stack.append(ch)
            elif not stack or "([{".index(stack.pop()) != ")]}".index(ch):
                ok = False
                break
        assert T._balanced1(s) == ("YES" if ok and not stack else "NO")

    def rec(n):
        return 1 if n == 0 else sum(rec(n - k) for k in (1, 2, 3) if n >= k)
    assert all(T.TEMPLATES["stairs"].solve(f"{n}\n") == f"{rec(n)}\n" for n in range(0, 14))
    assert T.TEMPLATES["islands"].solve("4 5\n11000\n11000\n00100\n00011\n") == "3\n"
    assert T.TEMPLATES["maze"].solve("3 3\n110\n010\n011\n") == "5\n"
    assert T.TEMPLATES["maze"].solve("2 2\n10\n01\n") == "-1\n"


def test_statement_rewrite_check_keeps_format_and_numbers():
    t = T.TEMPLATES["meeting_rooms"]
    story = t.statement.replace("회의가", "스터디 모임이").replace("회의실의 최소 개수", "스터디룸의 최소 개수")
    assert statement_preserves(t.statement, story)
    assert not statement_preserves(t.statement, story.replace("100,000", "10,000"))
    assert not statement_preserves(t.statement, story.replace("### 출력", "출력"))
    assert not statement_preserves(t.statement, story.replace("`n`", "`m`"))
    # Seen with the real model: it dropped the first heading of a problem. Every section has to stay.
    full = T.TEMPLATES["next_taller"].statement
    assert statement_preserves(full, full)
    assert not statement_preserves(full, full.replace("### 문제 상황" + chr(10), ""))


async def test_variant_is_private_stable_and_differs_per_application():
    async with session_scope() as db:
        base = (await db.execute(select(CodingProblem).where(CodingProblem.template_key == "stairs"))).scalar_one()
        assert base.variant_of is None
        # Templates hold no hidden case; variants are the only place they exist.
        assert not (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == base.id,
                                                                  CodingTestcase.visibility == "HIDDEN"))).first()
        a, b = uuid4(), uuid4()
        va, vb = await ensure_variant(db, a, base), await ensure_variant(db, b, base)
        assert (await ensure_variant(db, a, base)).id == va.id  # once per application
        assert va.id != vb.id and va.variant_of == base.id and va.is_active is False and va.variant_seed == seed_for(a)

        async def hidden(pid):
            rows = (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == pid, CodingTestcase.visibility == "HIDDEN")
                                     .order_by(CodingTestcase.ordinal))).scalars().all()
            return [(r.input, r.expected_output) for r in rows]
        ha, hb = await hidden(va.id), await hidden(vb.id)
        assert len(ha) >= 5 and ha != hb
        assert all(T.TEMPLATES["stairs"].solve(i) == o for i, o in ha)  # expected outputs come from the reference solution
        public = (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == va.id,
                                                                CodingTestcase.visibility == "PUBLIC"))).scalars().all()
        assert Counter(p.input for p in public) == Counter(T.TEMPLATES["stairs"].public)
        # A reworded statement is used only when it passes the check.
        c = uuid4()
        bad = await ensure_variant(db, c, base, "아무 설명")
        assert bad.statement_md == T.TEMPLATES["stairs"].statement
        d = uuid4()
        ok = await ensure_variant(db, d, base, T.TEMPLATES["stairs"].statement.replace("계단", "사다리"))
        assert "사다리" in ok.statement_md
        await db.rollback()


async def test_rewrite_task_strips_echoed_markers_and_falls_back_to_the_original():
    from app.ai.client import set_ai_client  # noqa: F401
    from app.ai.fake import FakeAIClient
    t = T.TEMPLATES["stairs"]
    ai = FakeAIClient(responses={"problem_rewrite": lambda inp, _m: {"statement": t.statement.replace("계단", "사다리") + "\n<<<END_PROBLEM_ab12>>>"}})
    r = await ai.llm("problem_rewrite", {"statement": t.statement}, lane="BATCH")
    assert r.status == "OK" and "사다리" in r.output["statement"] and "<<<" not in r.output["statement"]
    ai = FakeAIClient(responses={"problem_rewrite": lambda inp, _m: {"statement": "숫자도 형식도 없는 다른 문제입니다. 아무거나 풀어 보세요."}})
    r = await ai.llm("problem_rewrite", {"statement": t.statement}, lane="BATCH")
    assert r.output["statement"] == t.statement and r.flags.get("kept_original") is True
