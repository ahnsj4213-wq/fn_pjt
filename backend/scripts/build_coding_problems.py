"""Builds app/coding/problem_set.json: original Python problems (stdin/stdout) with PUBLIC and HIDDEN cases.

Expected outputs come from the reference solutions below; a few are cross-checked against brute force.
Run: python scripts/build_coding_problems.py   (rewrites the JSON; the migration loads it)
"""
import heapq
import json
import random
from collections import Counter, deque
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "coding" / "problem_set.json"
rnd = random.Random(20261103)
MOD = 1_000_000_007


def lines(*xs) -> str:
    return "\n".join(str(x) for x in xs) + "\n"


def arr(a) -> str:
    return " ".join(map(str, a))


# --- reference solutions + input builders -------------------------------------------------------------------------

def palindrome(s: str) -> str:
    t = [c.lower() for c in s if c.isalnum()]
    return "YES" if t == t[::-1] else "NO"


def top_product(a: list[int]) -> int:
    b = sorted(a)
    return max(b[0] * b[1], b[-1] * b[-2])


def freq(s: str) -> list[str]:
    c = Counter(ch for ch in s if ch != " ")
    return [f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


def rotate(a: list[int], k: int) -> list[int]:
    k %= len(a)
    return a[-k:] + a[:-k] if k else a[:]


def prefix(a: list[int], qs: list[tuple[int, int]]) -> list[int]:
    p = [0]
    for x in a:
        p.append(p[-1] + x)
    return [p[r] - p[l - 1] for l, r in qs]


def balanced(s: str) -> str:
    pair, st = {")": "(", "]": "[", "}": "{"}, []
    for ch in s:
        if ch in "([{":
            st.append(ch)
        elif not st or st.pop() != pair[ch]:
            return "NO"
    return "NO" if st else "YES"


def pair_count(a: list[int], t: int) -> int:
    seen, n = Counter(), 0
    for x in a:
        n += seen[t - x]
        seen[x] += 1
    return n


def pair_brute(a, t):
    return sum(1 for i in range(len(a)) for j in range(i + 1, len(a)) if a[i] + a[j] == t)


def islands(g: list[str]) -> int:
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
    return n


def maze(g: list[str]) -> int:
    h, w = len(g), len(g[0])
    if g[0][0] != "1" or g[h - 1][w - 1] != "1":
        return -1
    dist = {(0, 0): 1}
    q = deque([(0, 0)])
    while q:
        y, x = q.popleft()
        if (y, x) == (h - 1, w - 1):
            return dist[(y, x)]
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and g[ny][nx] == "1" and (ny, nx) not in dist:
                dist[(ny, nx)] = dist[(y, x)] + 1
                q.append((ny, nx))
    return -1


def stairs(n: int) -> int:
    d = [1, 1, 2] + [0] * max(0, n - 2)
    for i in range(3, n + 1):
        d[i] = (d[i - 1] + d[i - 2] + d[i - 3]) % MOD
    return d[n]


def stairs_rec(n: int) -> int:
    return 1 if n == 0 else sum(stairs_rec(n - s) for s in (1, 2, 3) if n - s >= 0)


def rooms(iv: list[tuple[int, int]]) -> int:
    h: list[int] = []
    for s, e in sorted(iv):
        if h and h[0] <= s:
            heapq.heapreplace(h, e)
        else:
            heapq.heappush(h, e)
    return len(h)


def rooms_brute(iv):
    pts = {t for s, e in iv for t in (s, e)}
    return max(sum(1 for s, e in iv if s <= t < e) for t in pts)


# --- cross checks --------------------------------------------------------------------------------------------------

for _ in range(300):
    a = [rnd.randint(-3, 3) for _ in range(rnd.randint(2, 9))]
    t = rnd.randint(-4, 4)
    assert pair_count(a, t) == pair_brute(a, t)
    iv = [(s, s + rnd.randint(1, 6)) for s in (rnd.randint(0, 12) for _ in range(rnd.randint(1, 8)))]
    assert rooms(iv) == rooms_brute(iv)
for n in range(0, 15):
    assert stairs(n) == stairs_rec(n)
    assert top_product([rnd.randint(-9, 9) for _ in range(5)]) is not None


# --- problems ------------------------------------------------------------------------------------------------------

def case(vis, inp, out):
    return {"visibility": vis, "input": inp, "output": out}


def grid(h, w, p):
    return ["".join("1" if rnd.random() < p else "0" for _ in range(w)) for _ in range(h)]


P: list[dict] = []

# 1 구간 합 구하기 (already in the DB from the first seed; kept here so the set is complete, inserted only if missing)
qs_big = [(1, 6), (3, 5), (6, 6)]
P.append({"title": "구간 합 구하기", "difficulty": "하", "existing": True, "cases": []})

# 2 회문 판별
pal = [("A man, a plan, a canal: Panama", ), ("race a car", ), ("a", ), ("0P", ), ("No lemon, no melon", ),
       ("12321", ), ("ab", ), ("Was it a car or a cat I saw?", ), ("!?", ), ("abccba1", )]
P.append({"title": "회문 판별", "difficulty": "하", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "문자열이 **회문**인지 판별합니다. 영문자와 숫자만 보고, 대소문자는 구분하지 않으며 나머지 문자(공백, 기호)는 무시합니다.\n\n"
                       "### 입력\n한 줄의 문자열 `s` (길이 1 이상 200 이하)\n\n### 출력\n회문이면 `YES`, 아니면 `NO`\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(*x), lines(palindrome(*x))) for i, x in enumerate(pal)]})

# 3 가장 큰 두 수의 곱
tp = [[1, 2, 3], [-5, -4, 1, 2], [-1, -2], [0, 0, 0], [7, 7], [-10, 3, 4, -9, 1], [5, -1, -2, 6], [100000, 99999, -100000, -99999]]
tp += [[rnd.randint(-1000, 1000) for _ in range(rnd.randint(3, 40))] for _ in range(3)]
P.append({"title": "가장 큰 두 수의 곱", "difficulty": "하", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "정수 배열에서 서로 다른 두 위치의 수를 골라 곱했을 때 나오는 **가장 큰 값**을 구합니다. 음수가 섞여 있을 수 있습니다.\n\n"
                       "### 입력\n첫째 줄에 개수 `n` (2 이상 1000 이하), 둘째 줄에 `n`개의 정수 (절댓값 10만 이하)\n\n### 출력\n가장 큰 곱\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(len(a), arr(a)), lines(top_product(a))) for i, a in enumerate(tp)]})

# 4 문자 빈도 정렬
fs = ["hello world", "aabbcc", "z", "banana", "the quick brown fox jumps over the lazy dog", "aaaa bbb cc d", "abcabcabc", "mississippi"]
P.append({"title": "문자 빈도 정렬", "difficulty": "하", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "문자열에서 공백을 뺀 각 문자가 몇 번 나왔는지 셉니다. **많이 나온 문자 순**으로, 횟수가 같으면 **사전 순**으로 한 줄에 하나씩 `문자:횟수` 형태로 출력합니다.\n\n"
                       "### 입력\n영소문자와 공백으로 된 한 줄 (길이 1 이상 200 이하, 공백만으로 이루어지지 않음)\n\n### 출력\n`문자:횟수`를 한 줄에 하나씩\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(s), lines(*freq(s))) for i, s in enumerate(fs)]})

# 5 배열 오른쪽으로 회전
rt = [([1, 2, 3, 4, 5], 2), ([1, 2, 3], 3), ([9], 100), ([1, 2], 0), ([1, 2, 3, 4], 7), ([5, 6, 7, 8, 9, 10], 1),
      ([rnd.randint(-50, 50) for _ in range(30)], 12345), ([0, 1], 1)]
P.append({"title": "배열 오른쪽으로 회전", "difficulty": "하", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "배열을 오른쪽으로 `k`칸 회전한 결과를 출력합니다. 맨 끝의 값은 맨 앞으로 돌아옵니다. `k`는 배열 길이보다 클 수 있습니다.\n\n"
                       "### 입력\n첫째 줄에 `n` `k` (1 ≤ n ≤ 100, 0 ≤ k ≤ 1,000,000), 둘째 줄에 `n`개의 정수\n\n### 출력\n회전한 배열을 공백으로 구분해 한 줄에\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(f"{len(a)} {k}", arr(a)), lines(arr(rotate(a, k)))) for i, (a, k) in enumerate(rt)]})

# 6 괄호 짝 맞추기
bs = [["()", "([)]", "{[()]}"], ["(((", ")))", "()()()"], ["", "(", "}{"], ["{[}]", "[({})]", "(()"]]
bs = [x for x in bs]
P.append({"title": "괄호 짝 맞추기", "difficulty": "중", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "`()`, `[]`, `{}` 세 종류의 괄호로 이루어진 문자열이 올바르게 짝지어졌는지 판별합니다. 여는 괄호는 같은 종류의 닫는 괄호로, 안쪽부터 닫혀야 합니다.\n\n"
                       "### 입력\n첫째 줄에 테스트 개수 `T` (1 ≤ T ≤ 100), 이후 `T`줄에 괄호 문자열 (길이 0 이상 1000 이하, 빈 줄이면 빈 문자열)\n\n"
                       "### 출력\n각 문자열마다 올바르면 `YES`, 아니면 `NO`를 한 줄씩\n",
          "cases": [case("PUBLIC" if i == 0 else "HIDDEN", lines(len(g), *g), lines(*[balanced(s) for s in g])) for i, g in enumerate(bs)]})

# 7 합이 목표인 쌍의 개수
pc = [([1, 2, 3, 4, 5], 6), ([1, 1, 1, 1], 2), ([5], 10), ([0, 0, 0], 0), ([-1, 1, 2, -2, 3], 1), ([3, 3, 3, 3, 3], 6),
      ([rnd.randint(-5, 5) for _ in range(200)], 0), ([rnd.randint(-10**9, 10**9) for _ in range(2000)], 7),
      ([i % 50 for i in range(100000)], 49)]
P.append({"title": "합이 목표인 쌍의 개수", "difficulty": "중", "time_limit_ms": 3000, "memory_mb": 256,
          "statement": "정수 배열에서 `i < j`이고 `a[i] + a[j] = target`인 위치 쌍 `(i, j)`가 **몇 개**인지 셉니다. 같은 값이 여러 번 나오면 위치가 다른 쌍은 모두 따로 셉니다.\n\n"
                       "### 입력\n첫째 줄에 `n` `target` (1 ≤ n ≤ 100,000, 절댓값 10억 이하), 둘째 줄에 `n`개의 정수 (절댓값 10억 이하)\n\n### 출력\n쌍의 개수\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(f"{len(a)} {t}", arr(a)), lines(pair_count(a, t))) for i, (a, t) in enumerate(pc)]})

# 8 섬의 개수
gs = [["11000", "11000", "00100", "00011"], ["1"], ["0"], ["101", "010", "101"], ["111", "111", "111"], ["1010", "0101", "1010"],
      grid(20, 30, 0.45), grid(50, 50, 0.4), grid(1, 12, 0.5), grid(12, 1, 0.5)]
P.append({"title": "섬의 개수", "difficulty": "중", "time_limit_ms": 3000, "memory_mb": 256,
          "statement": "지도에서 `1`은 땅, `0`은 바다입니다. 땅이 **상하좌우**로 이어진 덩어리 하나를 섬이라고 할 때, 섬이 모두 몇 개인지 구합니다. 대각선은 이어진 것으로 보지 않습니다.\n\n"
                       "### 입력\n첫째 줄에 `H` `W` (1 ≤ H, W ≤ 100), 이후 `H`줄에 길이 `W`의 `0`/`1` 문자열\n\n### 출력\n섬의 개수\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(f"{len(g)} {len(g[0])}", *g), lines(islands(g))) for i, g in enumerate(gs)]})

# 9 미로 최단 거리
def open_ends(g):
    g = list(g)
    g[0] = "1" + g[0][1:]
    g[-1] = g[-1][:-1] + "1"
    return g


mz = [["110", "010", "011"], ["1"], ["10", "01"], ["1111", "0001", "1111", "1000", "1111"], ["0111", "1111"], ["1101", "1101", "1111"],
      open_ends(grid(15, 15, 0.8)), open_ends(grid(30, 40, 0.75)), ["111111", "000001", "111111", "100000", "111111"]]
P.append({"title": "미로 최단 거리", "difficulty": "중", "time_limit_ms": 3000, "memory_mb": 256,
          "statement": "`1`은 지나갈 수 있는 칸, `0`은 벽입니다. 왼쪽 위 `(1, 1)`에서 오른쪽 아래 `(H, W)`까지 상하좌우로 한 칸씩 움직일 때, **지나는 칸 수가 가장 적은 경로**의 칸 수를 구합니다. 시작 칸과 도착 칸도 센니다. 갈 수 없으면 `-1`을 출력합니다.\n\n"
                       "### 입력\n첫째 줄에 `H` `W` (1 ≤ H, W ≤ 100), 이후 `H`줄에 길이 `W`의 `0`/`1` 문자열\n\n### 출력\n최소 칸 수 또는 `-1`\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(f"{len(g)} {len(g[0])}", *g), lines(maze(g))) for i, g in enumerate(mz)]})

# 10 계단 오르기 경우의 수
st = [1, 3, 4, 0, 2, 10, 30, 100, 1000, 100000]
P.append({"title": "계단 오르기 경우의 수", "difficulty": "중", "time_limit_ms": 2000, "memory_mb": 256,
          "statement": "계단이 `n`칸 있습니다. 한 번에 1칸, 2칸, 3칸 중 하나를 올라갈 수 있을 때, 맨 위까지 오르는 방법의 수를 구합니다. 값이 매우 커질 수 있으니 `1,000,000,007`로 나눈 나머지를 출력합니다.\n\n"
                       "### 입력\n정수 `n` (0 ≤ n ≤ 100,000). `n`이 0이면 방법은 1가지입니다.\n\n### 출력\n방법의 수를 `1,000,000,007`로 나눈 나머지\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(n), lines(stairs(n))) for i, n in enumerate(st)]})

# 11 회의실 최소 개수
rv = [[(0, 30), (5, 10), (15, 20)], [(1, 2)], [(1, 5), (5, 9)], [(1, 10), (2, 9), (3, 8), (4, 7)], [(0, 1), (1, 2), (2, 3)],
      [(s, s + rnd.randint(1, 20)) for s in (rnd.randint(0, 60) for _ in range(30))],
      [(s, s + rnd.randint(1, 1000)) for s in (rnd.randint(0, 10**5) for _ in range(5000))]]
P.append({"title": "회의실 최소 개수", "difficulty": "중", "time_limit_ms": 3000, "memory_mb": 256,
          "statement": "회의가 `[시작, 끝)` 시간으로 여러 개 잡혀 있습니다. 한 회의가 끝나는 시각에 다른 회의가 시작하는 것은 겹치지 않는 것으로 봅니다. 모든 회의를 열 수 있는 **회의실의 최소 개수**를 구합니다.\n\n"
                       "### 입력\n첫째 줄에 회의 수 `n` (1 ≤ n ≤ 100,000), 이후 `n`줄에 `시작 끝` (0 ≤ 시작 < 끝 ≤ 1,000,000)\n\n### 출력\n회의실의 최소 개수\n",
          "cases": [case("PUBLIC" if i < 2 else "HIDDEN", lines(len(iv), *[f"{s} {e}" for s, e in iv]), lines(rooms(iv))) for i, iv in enumerate(rv)]})

# sanity: sizes and visibility mix
for p in P:
    if p.get("existing"):
        continue
    vis = Counter(c["visibility"] for c in p["cases"])
    assert vis["PUBLIC"] >= 1 and vis["HIDDEN"] >= 3, (p["title"], vis)
    assert all(len(c["input"]) < 2_000_000 for c in p["cases"])

OUT.write_text(json.dumps(P, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {len([p for p in P if not p.get('existing')])} new problems -> {OUT}")
