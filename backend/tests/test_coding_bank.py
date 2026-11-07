"""The problem bank (ADR-0021): every problem is really gradable, explained, independently checked, and picked by the server."""
import itertools
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.coding import refsol as R
from app.coding.grading import normalize_output
from app.coding.levels import LEVELS
from app.coding.reference import reference_program
from app.coding.selection import base_problem_for, experience_level, target_level
from app.coding.selection import test_minutes as minutes_for
from app.coding.templates import TEMPLATES
from app.coding.variants import ensure_variant, statement_preserves
from app.core.db import session_scope
from app.models import Application, CodingProblem, CodingTestcase, JobPosting

ALL = list(TEMPLATES.values())
KEYS = [t.key for t in ALL]
MAX_STDIN = 1024 * 1024  # runner/config.py
MAX_CODE = 64 * 1024
MAX_STDOUT = 64 * 1024  # runner/config.py STDOUT_CAP: more than this is OUTPUT_LIMIT, however right it is


def test_bank_size_and_every_level_has_variety():
    assert len(ALL) >= 50
    for lv in LEVELS:
        here = [t for t in ALL if t.level == lv]
        assert len(here) >= 7, lv
        assert len({tag for t in here for tag in t.tags}) >= 5, lv
    tags = {tag for t in ALL for tag in t.tags}
    for need in ("배열", "문자열", "해시", "스택", "큐", "정렬", "BFS", "DFS", "DP", "그래프"):
        assert need in tags, need


@pytest.mark.parametrize("key", KEYS)
def test_problem_has_everything_a_candidate_and_the_grader_need(key):
    t = TEMPLATES[key]
    assert t.level in LEVELS and t.tags and t.difficulty == LEVELS[t.level].difficulty and t.time_limit_ms == LEVELS[t.level].run_ms
    assert "### 입력" in t.statement and "### 출력" in t.statement
    assert len(t.public) >= 1 and len(t.notes) == len(t.public) and all(len(n) >= 10 for n in t.notes), "every example is explained"
    assert t.starter and "def solution" in t.starter
    compile(t.starter, key, "exec")
    assert t.source["origin"] == "ORIGINAL" and t.source["upstream_url"] is None and t.source["reference_urls"]
    a, b = t.hidden(random.Random(1)), t.hidden(random.Random(2))
    assert len(a) >= 5, "hidden tests"
    assert a == t.hidden(random.Random(1)) and a != b, "deterministic per seed, different per applicant"
    assert all(len(x.encode()) <= MAX_STDIN for x in [*t.public, *a]), "the runner refuses more"
    assert len(t.public) + len(a) <= 50, "the runner refuses more"
    for inp in [*t.public, *a]:
        out = t.solve(inp)
        assert out.endswith("\n") and out.strip()
        assert len(out.encode()) < MAX_STDOUT, "a right answer would be cut off by the runner"
    assert len(set(t.solve(x) for x in a)) >= 2, "hidden tests that all have one answer prove nothing"
    assert len(reference_program(key).encode()) <= MAX_CODE


@pytest.mark.parametrize("t", [t for t in ALL if t.expected], ids=lambda t: t.key)
def test_public_answers_match_the_ones_worked_out_by_hand(t):
    for inp, want in zip(t.public, t.expected, strict=True):
        assert t.solve(inp) == want


def test_new_statements_are_written_out():
    for t in ALL:
        if t.expected:
            for head in ("### 문제 상황", "### 해야 할 일", "### 제한사항"):
                assert head in t.statement, t.key
            assert len(t.statement) >= 180, t.key


@pytest.mark.parametrize("key", KEYS)
def test_reference_program_passes_as_a_candidate_submission(key, tmp_path):
    """The same program the sandbox gets, in a plain process (the real sandbox: tools/verify_coding_bank.py)."""
    t = TEMPLATES[key]
    path = tmp_path / "main.py"
    path.write_text(reference_program(key), encoding="utf-8")
    for inp in [*t.public, *t.hidden(random.Random(5))[:3]]:
        r = subprocess.run([sys.executable, "-I", str(path)], input=inp.encode(), capture_output=True, timeout=30)
        assert r.returncode == 0, r.stderr.decode()[-400:]
        assert normalize_output(r.stdout.decode()) == normalize_output(t.solve(inp))


@pytest.mark.parametrize("key", KEYS)
def test_starter_runs_on_the_examples(key, tmp_path):
    t = TEMPLATES[key]
    path = tmp_path / "main.py"
    path.write_text(t.starter, encoding="utf-8")
    for inp in t.public:
        r = subprocess.run([sys.executable, "-I", str(path)], input=inp.encode(), capture_output=True, timeout=10)
        assert r.returncode == 0, r.stderr.decode()[-400:]


# ── independent slow solutions: a different method on small random inputs ──────
def _pairs(r, n, m):
    return [(r.randint(1, n), r.randint(1, n)) for _ in range(m)]


def _reach(n, edges):
    adj = {i: set() for i in range(1, n + 1)}
    for a, b, *_ in edges:
        adj[a].add(b)
    out = {}
    for s in adj:
        seen, st = {s}, [s]
        while st:
            for v in adj[st.pop()]:
                if v not in seen:
                    seen.add(v)
                    st.append(v)
        out[s] = seen
    return out


def _lcs(a, b):
    if not a or not b:
        return 0
    return 1 + _lcs(a[1:], b[1:]) if a[0] == b[0] else max(_lcs(a[1:], b), _lcs(a, b[1:]))


def _edit(a, b, i, d, s):
    if not a:
        return len(b) * i
    if not b:
        return len(a) * d
    return min(_edit(a[1:], b, i, d, s) + d, _edit(a, b[1:], i, d, s) + i, _edit(a[1:], b[1:], i, d, s) + (0 if a[0] == b[0] else s))


def _merge(a):
    return 0 if len(a) < 2 else min(_merge(a[:k]) + _merge(a[k:]) + sum(a) for k in range(1, len(a)))


def _bfs_grid(g, y0, x0, wall):
    h, w = len(g), len(g[0])
    d = {(y0, x0): 0}
    frontier = [(y0, x0)]
    while frontier:
        nxt = []
        for y, x in frontier:
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and not wall(g[ny][nx]) and (ny, nx) not in d:
                    d[(ny, nx)] = d[(y, x)] + 1
                    nxt.append((ny, nx))
        frontier = nxt
    return d


def check_printer(r):
    n, m = r.randint(1, 7), 0
    m = r.randint(1, n)
    p = [r.randint(1, 4) for _ in range(n)]
    q, done = [(x, i + 1) for i, x in enumerate(p)], 0
    while True:
        x, i = q.pop(0)
        if q and any(y > x for y, _ in q):
            q.append((x, i))
            continue
        done += 1
        if i == m:
            break
    return R.printer_order(R._ln(f"{n} {m}", R._arr(p))), f"{done}\n"


def check_next_taller(r):
    a = [r.randint(1, 6) for _ in range(r.randint(1, 9))]
    want = [next((j + 1 for j in range(i + 1, len(a)) if a[j] > a[i]), 0) for i in range(len(a))]
    return R.next_taller(R._ln(len(a), R._arr(a))), R._ln(R._arr(want))


def check_window(r):
    n = r.randint(1, 9)
    k = r.randint(1, n)
    a = [r.randint(-9, 9) for _ in range(n)]
    return R.best_window(R._ln(f"{n} {k}", R._arr(a))), f"{max(sum(a[i:i + k]) for i in range(n - k + 1))}\n"


def check_span(r):
    n, t = r.randint(1, 9), r.randint(1, 25)
    a = [r.randint(1, 6) for _ in range(n)]
    want = min((j - i for i in range(n) for j in range(i + 1, n + 1) if sum(a[i:j]) >= t), default=0)
    return R.shortest_span(R._ln(f"{n} {t}", R._arr(a))), f"{want}\n"


def check_bookings(r):
    iv = [(s, s + r.randint(1, 4)) for s in (r.randint(0, 15) for _ in range(r.randint(1, 7)))]
    pts = sorted({2 * x for s, e in iv for x in range(s, e + 1)} | {2 * x + 1 for s, e in iv for x in range(s, e)})
    out, cur = [], None
    for p in pts:
        if cur and p == cur[1] + 1:
            cur[1] = p
        else:
            cur = [p, p]
            out.append(cur)
    return R.merge_bookings(R._ln(len(iv), *[f"{a} {b}" for a, b in iv])), R._ln(*[f"{a // 2} {b // 2}" for a, b in out])


def check_badges(r):
    names = [f"u{i}" for i in range(r.randint(2, 5))]
    msgs = [r.sample(names, 2) for _ in range(r.randint(0, 12))]
    badge = {}
    for x in names:
        badge[x] = sum(1 for y in names if y != x and msgs.count([y, x]) > msgs.count([x, y]))
    best = max(badge.values())
    return R.badge_ledger(R._ln(f"{len(names)} {len(msgs)}", " ".join(names), *[" ".join(m) for m in msgs])), f"{sorted(x for x in names if badge[x] == best)[0]} {best}\n"


def check_sprinkler(r):
    h, w = r.randint(1, 5), r.randint(1, 5)
    g = ["".join(r.choice("S...#") for _ in range(w)) for _ in range(h)]
    dist = {}
    for y in range(h):
        for x in range(w):
            if g[y][x] == "S":
                for c, d in _bfs_grid(g, y, x, lambda v: v != ".").items():
                    dist[c] = min(dist.get(c, 99), d)
    empty = [(y, x) for y in range(h) for x in range(w) if g[y][x] == "."]
    want = -1 if any(c not in dist for c in empty) else max((dist[c] for c in empty), default=0)
    return R.sprinkler(R._ln(f"{h} {w}", *g)), f"{want}\n"


def check_groups(r):
    n, m = r.randint(1, 8), r.randint(0, 7)
    e = _pairs(r, n, m)
    reach = _reach(n, [*e, *[(b, a) for a, b in e]])
    groups = {frozenset(s) for s in reach.values()}
    return R.network_groups(R._ln(f"{n} {m}", *[f"{a} {b}" for a, b in e])), f"{len(groups)} {max(map(len, groups))}\n"


def check_delivery(r):
    n, m = r.randint(1, 6), r.randint(0, 10)
    e = [(a, b, r.randint(1, 9)) for a, b in _pairs(r, n, m)]
    s = r.randint(1, n)
    d = {s: 0}
    for _ in range(n):  # Bellman-Ford
        for a, b, c in e:
            if a in d and d[a] + c < d.get(b, 10**9):
                d[b] = d[a] + c
    return R.delivery_time(R._ln(f"{n} {m} {s}", *[f"{a} {b} {c}" for a, b, c in e])), R._ln(R._arr(d.get(i, -1) for i in range(1, n + 1)))


def check_course(r):
    n = r.randint(1, 6)
    e = [(a, b) for a, b in _pairs(r, n, r.randint(0, 6)) if a != b]
    inp = R._ln(f"{n} {len(e)}", *[f"{a} {b}" for a, b in e])
    for p in itertools.permutations(range(1, n + 1)):  # permutations come in lexicographic order
        pos = {x: i for i, x in enumerate(p)}
        if all(pos[a] < pos[b] for a, b in e):
            return R.course_order(inp), R._ln(R._arr(p))
    return R.course_order(inp), "IMPOSSIBLE\n"


def check_budget(r):
    n, b = r.randint(1, 7), r.randint(1, 15)
    it = [(r.randint(1, 9), r.randint(1, 20)) for _ in range(n)]
    want = 0
    for mask in range(1 << n):
        pick = [it[i] for i in range(n) if mask >> i & 1]
        if sum(c for c, _ in pick) <= b:
            want = max(want, sum(v for _, v in pick))
    return R.budget_pick(R._ln(f"{n} {b}", *[f"{c} {v}" for c, v in it])), f"{want}\n"


def check_streak(r):
    a = [r.randint(-5, 5) for _ in range(r.randint(1, 9))]
    best = [1] * len(a)
    for i in range(len(a)):
        for j in range(i):
            if a[j] < a[i]:
                best[i] = max(best[i], best[j] + 1)
    return R.growth_streak(R._ln(len(a), R._arr(a))), f"{max(best)}\n"


def check_line(r):
    n, m = r.randint(1, 4), r.randint(1, 20)
    a = [r.randint(1, 9) for _ in range(n)]
    t = 0
    while sum(t // x for x in a) < m:
        t += 1
    return R.line_time(R._ln(f"{n} {m}", R._arr(a))), f"{t}\n"


def check_pipes(r):
    n = r.randint(1, 8)
    p = r.sample(range(1, n + 1), n)
    q = r.sample(range(1, n + 1), n)
    m = r.randint(0, n)
    e = list(zip(p[:m], q[:m]))
    und = _reach(n, [*e, *[(b, a) for a, b in e]])
    comps = {frozenset(s) for s in und.values()}
    rings = sum(1 for c in comps if sum(1 for a, _ in e if a in c) == len(c))
    return R.pipe_shapes(R._ln(f"{n} {m}", *[f"{a} {b}" for a, b in e])), f"{len(comps) - rings} {rings}\n"


def check_lcs(r):
    a, b = ("".join(r.choice("ab") for _ in range(r.randint(1, 7))) for _ in range(2))
    return R.common_fragment(R._ln(a, b)), f"{_lcs(a, b)}\n"


def check_typo(r):
    a, b = ("".join(r.choice("ab") for _ in range(r.randint(1, 6))) for _ in range(2))
    i, d, s = r.randint(1, 5), r.randint(1, 5), r.randint(1, 9)
    return R.typo_cost(R._ln(f"{i} {d} {s}", a, b)), f"{_edit(a, b, i, d, s)}\n"


def check_autocomplete(r):
    w = ["".join(r.choice("ab") for _ in range(r.randint(1, 4))) for _ in range(r.randint(1, 8))]
    q = ["".join(r.choice("ab") for _ in range(r.randint(1, 4))) for _ in range(5)]
    return R.autocomplete(R._ln(f"{len(w)} {len(q)}", " ".join(w), " ".join(q))), R._ln(*[sum(x.startswith(y) for x in w) for y in q])


def check_cable(r):
    n = r.randint(1, 6)
    e = [(a, b, r.randint(1, 9)) for a, b in _pairs(r, n, r.randint(0, 8)) if a != b]
    best = None
    for k in range(len(e) + 1):
        for pick in itertools.combinations(e, k):
            reach = _reach(n, [*pick, *[(b, a, c) for a, b, c in pick]])
            if len(reach[1]) == n:
                c = sum(x[2] for x in pick)
                best = c if best is None else min(best, c)
    return R.cable_cost(R._ln(f"{n} {len(e)}", *[f"{a} {b} {c}" for a, b, c in e])), f"{-1 if best is None else best}\n"


def check_keys(r):
    h, w = r.randint(1, 4), r.randint(2, 5)
    cells = [list(r.choice("....#aAbB") for _ in range(w)) for _ in range(h)]
    spots = r.sample([(y, x) for y in range(h) for x in range(w)], 2)
    cells[spots[0][0]][spots[0][1]] = "S"
    cells[spots[1][0]][spots[1][1]] = "E"
    g = ["".join(c) for c in cells]
    # Iterate to a fixed point: how far with which keys, relaxing every state until nothing changes.
    best = {(spots[0][0], spots[0][1], frozenset()): 0}
    changed = True
    while changed:
        changed = False
        for (y, x, k), d in list(best.items()):
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if not (0 <= ny < h and 0 <= nx < w) or g[ny][nx] == "#" or (g[ny][nx] in "AB" and g[ny][nx].lower() not in k):
                    continue
                nk = k | {g[ny][nx]} if g[ny][nx] in "ab" else k
                if best.get((ny, nx, nk), 10**9) > d + 1:
                    best[(ny, nx, nk)] = d + 1
                    changed = True
    ends = [d for (y, x, _k), d in best.items() if (y, x) == spots[1]]
    return R.keys_doors(R._ln(f"{h} {w}", *g)), f"{min(ends) if ends else -1}\n"


def check_stock(r):
    n = r.randint(1, 6)
    a = [r.randint(0, 9) for _ in range(n)]
    start, ops, want = list(a), [], []
    for _ in range(r.randint(0, 8)):
        x, y = sorted((r.randint(1, n), r.randint(1, n)))
        if r.random() < 0.5:
            v = r.randint(0, 9)
            a[x - 1] = v
            ops.append(f"S {x} {v}")
        else:
            ops.append(f"Q {x} {y}")
            want.append(sum(a[x - 1:y]))
    ops.append(f"Q 1 {n}")
    want.append(sum(a))
    return R.stock_ranges(R._ln(f"{n} {len(ops)}", R._arr(start), *ops)), R._ln(*want)


def check_dinner(r):
    n = r.randint(1, 9)
    v = [r.randint(1, 20) for _ in range(n)]
    par = [r.randint(1, i - 1) for i in range(2, n + 1)]
    best = 0
    for mask in range(1 << n):
        if all(not (mask >> (i - 1) & 1 and mask >> (par[i - 2] - 1) & 1) for i in range(2, n + 1)):
            best = max(best, sum(v[i] for i in range(n) if mask >> i & 1))
    return R.team_dinner(R._ln(n, R._arr(v), R._arr(par))), f"{best}\n"


def check_range(r):
    n = r.randint(1, 7)
    a = [r.randint(-9, 9) for _ in range(n)]
    start, ops, want = list(a), [], []
    for _ in range(r.randint(0, 9)):
        x, y = sorted((r.randint(1, n), r.randint(1, n)))
        if r.random() < 0.5:
            v = r.randint(-5, 5)
            ops.append(f"A {x} {y} {v}")
            a[x - 1:y] = [c + v for c in a[x - 1:y]]
        else:
            ops.append(f"M {x} {y}")
            want.append(max(a[x - 1:y]))
    ops.append(f"M 1 {n}")
    want.append(max(a))
    return R.range_add_max(R._ln(f"{n} {len(ops)}", R._arr(start), *ops)), R._ln(*want)


def check_tour(r):
    n = r.randint(2, 6)
    c = [[0 if i == j or r.random() < 0.25 else r.randint(1, 9) for j in range(n)] for i in range(n)]
    best = None
    for p in itertools.permutations(range(1, n)):
        path = [0, *p, 0]
        if all(c[a][b] for a, b in zip(path, path[1:])):
            t = sum(c[a][b] for a, b in zip(path, path[1:]))
            best = t if best is None else min(best, t)
    return R.delivery_tour(R._ln(n, *[R._arr(x) for x in c])), f"{-1 if best is None else best}\n"


def check_scc(r):
    n, m = r.randint(1, 8), r.randint(0, 12)
    e = _pairs(r, n, m)
    reach = _reach(n, e)
    groups = {frozenset(y for y in reach[x] if x in reach[y]) for x in reach}
    return R.dependency_cycles(R._ln(f"{n} {m}", *[f"{a} {b}" for a, b in e])), f"{len(groups)} {max(map(len, groups))}\n"


def check_lca(r):
    n = r.randint(1, 10)
    par = [0, 0] + [r.randint(1, i - 1) for i in range(2, n + 1)]

    def chain(x):
        out = [x]
        while x != 1:
            x = par[x]
            out.append(x)
        return out
    qs = [(r.randint(1, n), r.randint(1, n)) for _ in range(5)]
    want = []
    for a, b in qs:
        ca, cb = chain(a), chain(b)
        l = next(x for x in ca if x in cb)
        want.append(f"{l} {ca.index(l) + cb.index(l)}")
    return R.org_distance(R._ln(f"{n} 5", R._arr(par[2:]), *[f"{a} {b}" for a, b in qs])), R._ln(*want)


def check_flow(r):
    n, m = r.randint(2, 5), r.randint(0, 8)
    e = [(a, b, r.randint(1, 9)) for a, b in _pairs(r, n, m)]
    # Max-flow equals the smallest cut over every split of the nodes (min-cut theorem).
    best = None
    mid = list(range(2, n))
    for k in range(len(mid) + 1):
        for side in itertools.combinations(mid, k):
            s = {1, *side}
            c = sum(w for a, b, w in e if a in s and b not in s)
            best = c if best is None else min(best, c)
    return R.max_throughput(R._ln(f"{n} {m}", *[f"{a} {b} {c}" for a, b, c in e])), f"{best}\n"


def check_area(r):
    rs = [(x, y, x + r.randint(1, 4), y + r.randint(1, 4)) for x, y in ((r.randint(0, 8), r.randint(0, 8)) for _ in range(r.randint(1, 5)))]
    cells = {(x, y) for a, b, c, d in rs for x in range(a, c) for y in range(b, d)}
    return R.billboard_area(R._ln(len(rs), *[" ".join(map(str, q)) for q in rs])), f"{len(cells)}\n"


def check_merge(r):
    a = [r.randint(1, 20) for _ in range(r.randint(1, 8))]
    return R.merge_files(R._ln(len(a), R._arr(a))), f"{_merge(a)}\n"


CHECKS = [check_printer, check_next_taller, check_window, check_span, check_bookings, check_badges, check_sprinkler, check_groups,
          check_delivery, check_course, check_budget, check_streak, check_line, check_pipes, check_lcs, check_typo, check_autocomplete,
          check_cable, check_keys, check_stock, check_dinner, check_range, check_tour, check_scc, check_lca, check_flow, check_area, check_merge]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_reference_solution_agrees_with_a_slow_independent_one(check):
    r = random.Random(20261105)
    for _ in range(120):
        got, want = check(r)
        assert got == want


# ── selection ──────────────────────────────────────────────────────────────────
def test_level_follows_job_experience_difficulty_and_time():
    assert experience_level("신입") == 2 and experience_level("경력 3년 이상") == 3 and experience_level("경력") == 3
    assert experience_level("7년 이상") == 4 and experience_level("10년차") == 5 and experience_level(None) == 2
    assert target_level("DEV", "신입", "NORMAL", 60) == 2
    assert target_level("DEV", "신입", "EASY", 60) == 1 and target_level("DEV", "신입", "HARD", 60) == 3
    assert target_level("DATA_AI", "경력 5년", "NORMAL", 60) == 4
    assert target_level("MARKETING", "10년", "NORMAL", 60) == 1, "not a coding job"
    assert target_level("DEV", "10년", "HARD", 60) == 5 and target_level("DEV", "10년", "HARD", 30) == 2, "the time limit caps the level"
    assert target_level("DEV", "신입", "EASY", 10) == 0
    assert minutes_for(2, 60) == LEVELS[2].minutes and minutes_for(5, 30) == 30 and minutes_for(None, 25) == 25


async def _issue(db, posting, candidate, when) -> CodingProblem:
    """A real application row and the variant row that records which base problem it was given."""
    app = Application(company_id=posting.company_id, posting_id=posting.id, candidate_id=candidate, consent_record_id=uuid4(),
                      resume_snapshot={}, resume_snapshot_text="", applied_at=when)
    db.add(app)
    await db.flush()
    base = await base_problem_for(db, app, posting)
    db.add(CodingProblem(title=base.title, statement_md="x", time_limit_ms=1000, memory_mb=128, is_active=False,
                         variant_of=base.id, variant_for=app.id, variant_seed=1))
    await db.flush()
    return base


async def test_auto_selection_stays_on_the_level_and_avoids_recent_and_repeated_problems():
    async with session_scope() as db:
        await db.execute(text("SET LOCAL session_replication_role = replica"))  # only the selection rows matter here, not users or consents
        FALLBACK = await db.scalar(select(CodingProblem.id).where(CodingProblem.template_key == "stairs"))
        posting = JobPosting(company_id=uuid4(), created_by=uuid4(), status="DRAFT", job_family="DEV", title="t", experience_level="신입",
                             coding_enabled=True, coding_language="python", coding_problem_id=FALLBACK, coding_time_limit_min=60, coding_selection="AUTO", coding_difficulty="NORMAL")
        db.add(posting)
        await db.flush()
        pool = (await db.execute(select(CodingProblem).where(CodingProblem.level == 2, CodingProblem.variant_of.is_(None), CodingProblem.is_active))).scalars().all()
        assert len(pool) >= 7
        t0 = datetime(2026, 1, 1, tzinfo=UTC)

        first_round = [await _issue(db, posting, uuid4(), t0 + timedelta(minutes=i)) for i in range(len(pool))]
        assert all(p.level == 2 for p in first_round)
        assert len({p.id for p in first_round}) == len(pool), "no repeat until every problem of the level was issued"
        again = await _issue(db, posting, uuid4(), t0 + timedelta(minutes=len(pool)))
        assert again.id == first_round[0].id, "then the one issued longest ago"

        # The same candidate applying elsewhere does not get a problem they already had, until the level runs out.
        repeat, seen = uuid4(), []
        for i in range(len(pool)):
            other = JobPosting(company_id=posting.company_id, created_by=posting.created_by, status="DRAFT", job_family="DEV", title=f"u{i}", experience_level="신입",
                               coding_enabled=True, coding_language="python", coding_problem_id=FALLBACK, coding_time_limit_min=60, coding_selection="AUTO", coding_difficulty="NORMAL")
            db.add(other)
            await db.flush()
            seen.append((await _issue(db, other, repeat, t0 + timedelta(minutes=200 + i))).id)
        assert len(set(seen)) == len(pool), "a candidate does not meet the same problem twice"

        pinned = JobPosting(company_id=posting.company_id, created_by=posting.created_by, status="DRAFT", job_family="DEV", title="v", experience_level="신입",
                            coding_enabled=True, coding_language="python", coding_time_limit_min=60, coding_selection="FIXED", coding_problem_id=pool[0].id)
        db.add(pinned)
        await db.flush()
        assert (await _issue(db, pinned, uuid4(), t0)).id == pool[0].id, "a pinned problem stays"
        await db.rollback()


async def test_variant_carries_the_metadata_and_starter_but_the_template_has_no_hidden_case():
    async with session_scope() as db:
        base = (await db.execute(select(CodingProblem).where(CodingProblem.template_key == "sprinkler"))).scalar_one()
        assert base.level == 3 and "BFS" in base.tags and base.starter_code and base.source["upstream_url"] is None
        assert "https://lovelyunsh.tistory.com/289" in base.source["reference_urls"]
        assert not (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == base.id, CodingTestcase.visibility == "HIDDEN"))).first()
        v = await ensure_variant(db, uuid4(), base)
        assert v.level == 3 and v.starter_code == TEMPLATES["sprinkler"].starter and v.tags == list(TEMPLATES["sprinkler"].tags)
        cases = (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == v.id))).scalars().all()
        assert sum(c.visibility == "HIDDEN" for c in cases) >= 5 and all(c.note for c in cases if c.visibility == "PUBLIC")
        assert statement_preserves(base.statement_md, base.statement_md)
        await db.rollback()

