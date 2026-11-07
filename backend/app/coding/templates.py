"""Coding problem templates: a statement, fixed PUBLIC examples, and a generator for HIDDEN inputs.

No hidden test lives in the repository or in the seed: every application gets its own variant (coding/variants.py)
whose hidden inputs are generated here from a seed and whose expected outputs come from `solve` (never from an LLM).
`solve` takes the exact stdin text and returns the exact stdout text. All problems are original work.
"""
import heapq
import random
from collections import Counter, deque
from dataclasses import replace

from app.coding.example_notes import NOTES
from app.coding.levels import LEVELS
from app.coding.template_base import Template, lines_starter, starter

MOD = 1_000_000_007


def _ln(*xs) -> str:
    return "\n".join(str(x) for x in xs) + "\n"


def _arr(a) -> str:
    return " ".join(map(str, a))


# ── 구간 합 구하기 ─────────────────────────────────────────────────────────────
def _prefix(inp: str) -> str:
    rows = inp.split("\n")
    n, q = map(int, rows[0].split())
    a = list(map(int, rows[1].split()))
    p = [0]
    for x in a:
        p.append(p[-1] + x)
    out = []
    for i in range(q):
        l, r = map(int, rows[2 + i].split())
        out.append(p[r] - p[l - 1])
    return _ln(*out)


def _prefix_in(a: list[int], qs: list[tuple[int, int]]) -> str:
    return _ln(f"{len(a)} {len(qs)}", _arr(a), *[f"{l} {r}" for l, r in qs])


def _prefix_hidden(rnd: random.Random) -> list[str]:
    out = [_prefix_in([7], [(1, 1)]), _prefix_in([-5, 5, -5, 5], [(1, 4), (2, 3)])]
    for n, q in ((6, 3), (50, 20), (1000, 200), (30000, 1000)):
        a = [rnd.randint(-1000, 1000) for _ in range(n)]
        qs = []
        for _ in range(q):
            l = rnd.randint(1, n)
            qs.append((l, rnd.randint(l, n)))
        out.append(_prefix_in(a, qs))
    return out


# ── 회문 판별 ──────────────────────────────────────────────────────────────────
def _palindrome(inp: str) -> str:
    t = [c.lower() for c in inp.rstrip("\n") if c.isalnum()]
    return "YES\n" if t == t[::-1] else "NO\n"


def _pal_hidden(rnd: random.Random) -> list[str]:
    fixed = ["a", "0P", "No lemon, no melon", "12321", "ab", "Was it a car or a cat I saw?", "!?", "abccba1"]
    out = [_ln(s) for s in fixed]
    alpha = "abcdefghijklmnopqrstuvwxyz0123456789 ,."
    for _ in range(3):
        half = "".join(rnd.choice(alpha) for _ in range(rnd.randint(1, 60)))
        mid = rnd.choice(["", "x", "7"])
        s = half + mid + (half[::-1] if rnd.random() < 0.6 else half)
        out.append(_ln(s.swapcase() if rnd.random() < 0.5 else s))
    return out


# ── 가장 큰 두 수의 곱 ──────────────────────────────────────────────────────────
def _top_product(inp: str) -> str:
    rows = inp.split("\n")
    b = sorted(map(int, rows[1].split()))
    return _ln(max(b[0] * b[1], b[-1] * b[-2]))


def _tp_in(a: list[int]) -> str:
    return _ln(len(a), _arr(a))


def _tp_hidden(rnd: random.Random) -> list[str]:
    fixed = [[-1, -2], [0, 0, 0], [7, 7], [-10, 3, 4, -9, 1], [5, -1, -2, 6], [100000, 99999, -100000, -99999]]
    out = [_tp_in(a) for a in fixed]
    for n, lim in ((10, 20), (40, 1000), (1000, 100000)):
        out.append(_tp_in([rnd.randint(-lim, lim) for _ in range(n)]))
    return out


# ── 문자 빈도 정렬 ──────────────────────────────────────────────────────────────
def _freq(inp: str) -> str:
    c = Counter(ch for ch in inp.rstrip("\n") if ch != " ")
    return _ln(*[f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))])


def _freq_hidden(rnd: random.Random) -> list[str]:
    fixed = ["z", "banana", "the quick brown fox jumps over the lazy dog", "aaaa bbb cc d", "abcabcabc", "mississippi"]
    out = [_ln(s) for s in fixed]
    for letters, n in (("abc", 20), ("abcdefghij", 60), ("abcdefghijklmnopqrstuvwxyz ", 200)):
        s = "".join(rnd.choice(letters) for _ in range(n)).strip() or "a"
        out.append(_ln(s))
    return out


# ── 배열 오른쪽으로 회전 ────────────────────────────────────────────────────────
def _rotate(inp: str) -> str:
    rows = inp.split("\n")
    n, k = map(int, rows[0].split())
    a = list(map(int, rows[1].split()))
    k %= n
    return _ln(_arr(a[-k:] + a[:-k] if k else a))


def _rot_in(a: list[int], k: int) -> str:
    return _ln(f"{len(a)} {k}", _arr(a))


def _rot_hidden(rnd: random.Random) -> list[str]:
    out = [_rot_in([9], 100), _rot_in([1, 2], 0), _rot_in([1, 2, 3, 4], 7), _rot_in([0, 1], 1)]
    for n, kmax in ((6, 10), (30, 1000), (100, 1_000_000)):
        out.append(_rot_in([rnd.randint(-50, 50) for _ in range(n)], rnd.randint(0, kmax)))
    return out


# ── 괄호 짝 맞추기 ──────────────────────────────────────────────────────────────
def _balanced1(s: str) -> str:
    pair, st = {")": "(", "]": "[", "}": "{"}, []
    for ch in s:
        if ch in "([{":
            st.append(ch)
        elif not st or st.pop() != pair[ch]:
            return "NO"
    return "NO" if st else "YES"


def _balanced(inp: str) -> str:
    rows = inp.split("\n")
    t = int(rows[0])
    return _ln(*[_balanced1(rows[1 + i]) for i in range(t)])


def _rand_brackets(rnd: random.Random, n: int, valid: bool) -> str:
    pairs = ["()", "[]", "{}"]
    s: list[str] = []
    st: list[str] = []
    for _ in range(n):
        if st and rnd.random() < 0.5:
            s.append(st.pop())
        else:
            o = rnd.choice(pairs)
            s.append(o[0])
            st.append(o[1])
    s += st[::-1]
    if not valid and s:
        i = rnd.randrange(len(s))
        s[i] = rnd.choice("()[]{}")
    return "".join(s)


def _br_hidden(rnd: random.Random) -> list[str]:
    def block(strings: list[str]) -> str:
        return _ln(len(strings), *strings)
    out = [block(["(((", ")))", "()()()"]), block(["", "(", "}{"]), block(["{[}]", "[({})]", "(()"])]
    for n in (5, 30, 300):
        out.append(block([_rand_brackets(rnd, n, rnd.random() < 0.5) for _ in range(10)]))
    return out


# ── 합이 목표인 쌍의 개수 ───────────────────────────────────────────────────────
def _pairs(inp: str) -> str:
    rows = inp.split("\n")
    n, t = map(int, rows[0].split())
    seen, cnt = Counter(), 0
    for x in map(int, rows[1].split()):
        cnt += seen[t - x]
        seen[x] += 1
    return _ln(cnt)


def _pairs_in(a: list[int], t: int) -> str:
    return _ln(f"{len(a)} {t}", _arr(a))


def _pairs_hidden(rnd: random.Random) -> list[str]:
    out = [_pairs_in([5], 10), _pairs_in([0, 0, 0], 0), _pairs_in([3, 3, 3, 3, 3], 6), _pairs_in([-1, 1, 2, -2, 3], 1)]
    out.append(_pairs_in([rnd.randint(-5, 5) for _ in range(200)], rnd.randint(-3, 3)))
    out.append(_pairs_in([rnd.randint(-10**9, 10**9) for _ in range(2000)], rnd.randint(-10**9, 10**9)))
    out.append(_pairs_in([rnd.randint(0, 49) for _ in range(100000)], 49))
    return out


# ── 섬의 개수 ──────────────────────────────────────────────────────────────────
def _grid_parse(inp: str) -> list[str]:
    rows = inp.split("\n")
    h, _w = map(int, rows[0].split())
    return rows[1:1 + h]


def _islands(inp: str) -> str:
    g = _grid_parse(inp)
    h, w, seen, n = len(g), len(g[0]), set(), 0
    for i in range(h):
        for j in range(w):
            if g[i][j] == "1" and (i, j) not in seen:
                n += 1
                seen.add((i, j))
                q = deque([(i, j)])
                while q:
                    y, x = q.popleft()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and g[ny][nx] == "1" and (ny, nx) not in seen:
                            seen.add((ny, nx))
                            q.append((ny, nx))
    return _ln(n)


def _grid(rnd: random.Random, h: int, w: int, p: float) -> list[str]:
    return ["".join("1" if rnd.random() < p else "0" for _ in range(w)) for _ in range(h)]


def _grid_in(g: list[str]) -> str:
    return _ln(f"{len(g)} {len(g[0])}", *g)


def _isl_hidden(rnd: random.Random) -> list[str]:
    fixed = [["1"], ["0"], ["101", "010", "101"], ["111", "111", "111"], ["1010", "0101", "1010"]]
    out = [_grid_in(g) for g in fixed]
    for h, w, p in ((20, 30, 0.45), (50, 50, 0.4), (1, 12, 0.5), (12, 1, 0.5), (100, 100, 0.55)):
        out.append(_grid_in(_grid(rnd, h, w, p)))
    return out


# ── 미로 최단 거리 ──────────────────────────────────────────────────────────────
def _maze(inp: str) -> str:
    g = _grid_parse(inp)
    h, w = len(g), len(g[0])
    if g[0][0] != "1" or g[h - 1][w - 1] != "1":
        return _ln(-1)
    dist = {(0, 0): 1}
    q = deque([(0, 0)])
    while q:
        y, x = q.popleft()
        if (y, x) == (h - 1, w - 1):
            return _ln(dist[(y, x)])
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and g[ny][nx] == "1" and (ny, nx) not in dist:
                dist[(ny, nx)] = dist[(y, x)] + 1
                q.append((ny, nx))
    return _ln(-1)


def _open_ends(g: list[str]) -> list[str]:
    g = list(g)
    g[0] = "1" + g[0][1:]
    g[-1] = g[-1][:-1] + "1"
    return g


def _maze_hidden(rnd: random.Random) -> list[str]:
    fixed = [["1"], ["10", "01"], ["1111", "0001", "1111", "1000", "1111"], ["0111", "1111"], ["1101", "1101", "1111"],
             ["111111", "000001", "111111", "100000", "111111"]]
    out = [_grid_in(g) for g in fixed]
    for h, w, p in ((15, 15, 0.8), (30, 40, 0.75), (60, 60, 0.85), (100, 100, 0.8)):
        out.append(_grid_in(_open_ends(_grid(rnd, h, w, p))))
    return out


# ── 계단 오르기 경우의 수 ───────────────────────────────────────────────────────
def _stairs(inp: str) -> str:
    n = int(inp.split()[0])
    d = [1, 1, 2] + [0] * max(0, n - 2)
    for i in range(3, n + 1):
        d[i] = (d[i - 1] + d[i - 2] + d[i - 3]) % MOD
    return _ln(d[n])


def _stairs_hidden(rnd: random.Random) -> list[str]:
    out = [_ln(n) for n in (0, 2, 3, 100000)]
    out += [_ln(rnd.randint(5, 40)), _ln(rnd.randint(41, 1000)), _ln(rnd.randint(1001, 99999))]
    return out


# ── 회의실 최소 개수 ────────────────────────────────────────────────────────────
def _rooms(inp: str) -> str:
    rows = inp.split("\n")
    n = int(rows[0])
    iv = sorted(tuple(map(int, rows[1 + i].split())) for i in range(n))
    h: list[int] = []
    for s, e in iv:
        if h and h[0] <= s:
            heapq.heapreplace(h, e)
        else:
            heapq.heappush(h, e)
    return _ln(len(h))


def _rooms_in(iv: list[tuple[int, int]]) -> str:
    return _ln(len(iv), *[f"{s} {e}" for s, e in iv])


def _rooms_hidden(rnd: random.Random) -> list[str]:
    out = [_rooms_in([(1, 2)]), _rooms_in([(1, 5), (5, 9)]), _rooms_in([(1, 10), (2, 9), (3, 8), (4, 7)]),
           _rooms_in([(0, 1), (1, 2), (2, 3)])]
    for n, span, dur in ((30, 60, 20), (500, 5000, 300), (5000, 100000, 1000)):
        out.append(_rooms_in([(s, s + rnd.randint(1, dur)) for s in (rnd.randint(0, span) for _ in range(n))]))
    return out


TEMPLATES: dict[str, Template] = {t.key: t for t in (
    Template("prefix_sum", "구간 합 구하기", "하",
             "배열과 여러 개의 구간이 주어집니다. 각 구간 `[l, r]` (1부터 시작, 양 끝 포함)의 합을 구합니다.\n\n"
             "### 입력\n첫째 줄에 `n` `q` (1 ≤ n ≤ 30,000, 1 ≤ q ≤ 1,000), 둘째 줄에 `n`개의 정수 (절댓값 1,000 이하), "
             "이후 `q`줄에 `l` `r` (1 ≤ l ≤ r ≤ n)\n\n### 출력\n각 구간의 합을 한 줄에 하나씩\n",
             (_prefix_in([1, 2, 3, 4, 5], [(1, 3), (2, 4), (5, 5)]), _prefix_in([-1, 0, 1], [(1, 3)])), _prefix, _prefix_hidden),
    Template("palindrome", "회문 판별", "하",
             "문자열이 **회문**인지 판별합니다. 영문자와 숫자만 보고, 대소문자는 구분하지 않으며 나머지 문자(공백, 기호)는 무시합니다.\n\n"
             "### 입력\n한 줄의 문자열 `s` (길이 1 이상 200 이하)\n\n### 출력\n회문이면 `YES`, 아니면 `NO`\n",
             (_ln("A man, a plan, a canal: Panama"), _ln("race a car")), _palindrome, _pal_hidden),
    Template("top_product", "가장 큰 두 수의 곱", "하",
             "정수 배열에서 서로 다른 두 위치의 수를 골라 곱했을 때 나오는 **가장 큰 값**을 구합니다. 음수가 섞여 있을 수 있습니다.\n\n"
             "### 입력\n첫째 줄에 개수 `n` (2 이상 1000 이하), 둘째 줄에 `n`개의 정수 (절댓값 10만 이하)\n\n### 출력\n가장 큰 곱\n",
             (_tp_in([1, 2, 3]), _tp_in([-5, -4, 1, 2])), _top_product, _tp_hidden),
    Template("char_freq", "문자 빈도 정렬", "하",
             "문자열에서 공백을 뺀 각 문자가 몇 번 나왔는지 셉니다. **많이 나온 문자 순**으로, 횟수가 같으면 **사전 순**으로 "
             "한 줄에 하나씩 `문자:횟수` 형태로 출력합니다.\n\n"
             "### 입력\n영소문자와 공백으로 된 한 줄 (길이 1 이상 200 이하, 공백만으로 이루어지지 않음)\n\n### 출력\n`문자:횟수`를 한 줄에 하나씩\n",
             (_ln("hello world"), _ln("aabbcc")), _freq, _freq_hidden),
    Template("rotate_right", "배열 오른쪽으로 회전", "하",
             "배열을 오른쪽으로 `k`칸 회전한 결과를 출력합니다. 맨 끝의 값은 맨 앞으로 돌아옵니다. `k`는 배열 길이보다 클 수 있습니다.\n\n"
             "### 입력\n첫째 줄에 `n` `k` (1 ≤ n ≤ 100, 0 ≤ k ≤ 1,000,000), 둘째 줄에 `n`개의 정수\n\n### 출력\n회전한 배열을 공백으로 구분해 한 줄에\n",
             (_rot_in([1, 2, 3, 4, 5], 2), _rot_in([1, 2, 3], 3)), _rotate, _rot_hidden),
    Template("brackets", "괄호 짝 맞추기", "중",
             "`()`, `[]`, `{}` 세 종류의 괄호로 이루어진 문자열이 올바르게 짝지어졌는지 판별합니다. "
             "여는 괄호는 같은 종류의 닫는 괄호로, 안쪽부터 닫혀야 합니다.\n\n"
             "### 입력\n첫째 줄에 테스트 개수 `T` (1 ≤ T ≤ 100), 이후 `T`줄에 괄호 문자열 (길이 0 이상 1000 이하, 빈 줄이면 빈 문자열)\n\n"
             "### 출력\n각 문자열마다 올바르면 `YES`, 아니면 `NO`를 한 줄씩\n",
             (_ln(3, "()", "([)]", "{[()]}"),), _balanced, _br_hidden),
    Template("pair_count", "합이 목표인 쌍의 개수", "중",
             "정수 배열에서 `i < j`이고 `a[i] + a[j] = target`인 위치 쌍 `(i, j)`가 **몇 개**인지 셉니다. "
             "같은 값이 여러 번 나오면 위치가 다른 쌍은 모두 따로 셉니다.\n\n"
             "### 입력\n첫째 줄에 `n` `target` (1 ≤ n ≤ 100,000, 절댓값 10억 이하), 둘째 줄에 `n`개의 정수 (절댓값 10억 이하)\n\n### 출력\n쌍의 개수\n",
             (_pairs_in([1, 2, 3, 4, 5], 6), _pairs_in([1, 1, 1, 1], 2)), _pairs, _pairs_hidden, 3000),
    Template("islands", "섬의 개수", "중",
             "지도에서 `1`은 땅, `0`은 바다입니다. 땅이 **상하좌우**로 이어진 덩어리 하나를 섬이라고 할 때, 섬이 모두 몇 개인지 구합니다. "
             "대각선은 이어진 것으로 보지 않습니다.\n\n"
             "### 입력\n첫째 줄에 `H` `W` (1 ≤ H, W ≤ 100), 이후 `H`줄에 길이 `W`의 `0`/`1` 문자열\n\n### 출력\n섬의 개수\n",
             (_grid_in(["11000", "11000", "00100", "00011"]), _grid_in(["101", "010", "101"])), _islands, _isl_hidden, 3000),
    Template("maze", "미로 최단 거리", "중",
             "`1`은 지나갈 수 있는 칸, `0`은 벽입니다. 왼쪽 위 `(1, 1)`에서 오른쪽 아래 `(H, W)`까지 상하좌우로 한 칸씩 움직일 때, "
             "**지나는 칸 수가 가장 적은 경로**의 칸 수를 구합니다. 시작 칸과 도착 칸도 셉니다. 갈 수 없으면 `-1`을 출력합니다.\n\n"
             "### 입력\n첫째 줄에 `H` `W` (1 ≤ H, W ≤ 100), 이후 `H`줄에 길이 `W`의 `0`/`1` 문자열\n\n### 출력\n최소 칸 수 또는 `-1`\n",
             (_grid_in(["110", "010", "011"]), _grid_in(["10", "01"])), _maze, _maze_hidden, 3000),
    Template("stairs", "계단 오르기 경우의 수", "중",
             "계단이 `n`칸 있습니다. 한 번에 1칸, 2칸, 3칸 중 하나를 올라갈 수 있을 때, 맨 위까지 오르는 방법의 수를 구합니다. "
             "값이 매우 커질 수 있으니 `1,000,000,007`로 나눈 나머지를 출력합니다.\n\n"
             "### 입력\n정수 `n` (0 ≤ n ≤ 100,000). `n`이 0이면 방법은 1가지입니다.\n\n### 출력\n방법의 수를 `1,000,000,007`로 나눈 나머지\n",
             (_ln(1), _ln(3)), _stairs, _stairs_hidden),
    Template("meeting_rooms", "회의실 최소 개수", "중",
             "회의가 `[시작, 끝)` 시간으로 여러 개 잡혀 있습니다. 한 회의가 끝나는 시각에 다른 회의가 시작하는 것은 겹치지 않는 것으로 봅니다. "
             "모든 회의를 열 수 있는 **회의실의 최소 개수**를 구합니다.\n\n"
             "### 입력\n첫째 줄에 회의 수 `n` (1 ≤ n ≤ 100,000), 이후 `n`줄에 `시작 끝` (0 ≤ 시작 < 끝 ≤ 1,000,000)\n\n### 출력\n회의실의 최소 개수\n",
             (_rooms_in([(0, 30), (5, 10), (15, 20)]), _rooms_in([(1, 2)])), _rooms, _rooms_hidden, 3000),
)}
_ARR = ("n = int(data[0])", "a = list(map(int, data[1:1 + n]))")
_GRID = ("h, w = map(int, rows[0].split())", "grid = rows[1:1 + h]")
# The first problems, placed on the Lv.0~5 scale (ADR-0021): level, tags, starter code.
_FIRST: dict[str, tuple] = {
    "prefix_sum": (2, ("누적 합", "배열"), starter("a, queries", ("n, q = int(data[0]), int(data[1])", "a = list(map(int, data[2:2 + n]))",
                                                                 "queries = [(int(data[2 + n + 2 * i]), int(data[3 + n + 2 * i])) for i in range(q)]"),
                                                default="[]", show="print(*solution({args}), sep=chr(10))")),
    "palindrome": (1, ("문자열", "투 포인터"), lines_starter("s", ("s = rows[0]",), default='"NO"')),
    "top_product": (1, ("정렬", "배열"), starter("n, a", _ARR)),
    "char_freq": (1, ("해시", "정렬", "문자열"), lines_starter("s", ("s = rows[0]",), default="[]", show="print(*solution({args}), sep=chr(10))")),
    "rotate_right": (1, ("배열", "구현"), starter("n, k, a", ("n, k = int(data[0]), int(data[1])", "a = list(map(int, data[2:2 + n]))"),
                                                default="[]", show="print(*solution({args}))")),
    "brackets": (2, ("스택", "문자열"), lines_starter("strings", ("t = int(rows[0])", "strings = [rows[1 + i] for i in range(t)]"),
                                                     default="[]", show="print(*solution({args}), sep=chr(10))")),
    "pair_count": (2, ("해시", "배열"), starter("n, target, a", ("n, target = int(data[0]), int(data[1])", "a = list(map(int, data[2:2 + n]))"))),
    "islands": (3, ("BFS", "DFS", "격자"), lines_starter("h, w, grid", _GRID)),
    "maze": (3, ("BFS", "격자", "최단 경로"), lines_starter("h, w, grid", _GRID)),
    "stairs": (3, ("DP",), starter("n", ("n = int(data[0])",))),
    "meeting_rooms": (3, ("정렬", "우선순위 큐", "그리디"), starter("n, meetings", ("n = int(data[0])",
                                                                       "meetings = [(int(data[1 + 2 * i]), int(data[2 + 2 * i])) for i in range(n)]"))),
}


def _placed(t: Template) -> Template:
    level, tags, code = _FIRST[t.key]
    return replace(t, notes=NOTES.get(t.key, ()), level=level, tags=tags, starter=code, difficulty=LEVELS[level].difficulty, time_limit_ms=LEVELS[level].run_ms)


from app.coding.bank_advanced import ADVANCED  # noqa: E402 - the bank builds on this module's Template
from app.coding.bank_basic import BASIC  # noqa: E402

TEMPLATES = {**{k: _placed(t) for k, t in TEMPLATES.items()}, **{t.key: t for t in (*BASIC, *ADVANCED)}}
BY_TITLE = {t.title: t for t in TEMPLATES.values()}
