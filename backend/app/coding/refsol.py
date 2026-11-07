"""Reference solutions of the problem bank (coding/bank.py). Each takes the exact stdin text and returns the exact stdout text.

Standard library only and nothing from this application, on purpose: `reference_program` in tools and tests turns one of
these into the program a candidate would submit, so that the bank can be proved through the real sandbox and grader.
Top-level helpers are _ln and _arr only; every solution holds its own logic.
"""
import bisect
import heapq
from collections import Counter, defaultdict, deque


def _ln(*xs) -> str:
    return "\n".join(str(x) for x in xs) + "\n"


def _arr(a) -> str:
    return " ".join(map(str, a))


# ── Lv.0 ───────────────────────────────────────────────────────────────────────
def overweight(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, limit, a = d[0], d[1], d[2:]
    over = [x for x in a[:n] if x > limit]
    return _ln(f"{len(over)} {sum(over)}")


def even_seats(inp: str) -> str:
    d = list(map(int, inp.split()))
    return _ln(sum(1 for x in d[1:1 + d[0]] if x % 2 == 0))


def reverse_words(inp: str) -> str:
    return _ln(" ".join(w[::-1] for w in inp.split()))


def temp_gap(inp: str) -> str:
    d = list(map(int, inp.split()))
    a = d[1:1 + d[0]]
    return _ln(max(a) - min(a))


def vowels(inp: str) -> str:
    return _ln(sum(1 for c in inp.lower() if c in "aeiou"))


def coupon_codes(inp: str) -> str:
    n = int(inp.split()[0])
    out = []
    for i in range(1, n + 1):
        s = ("A" if i % 3 == 0 else "") + ("B" if i % 5 == 0 else "")
        out.append(s or str(i))
    return _ln(*out)


def grades(inp: str) -> str:
    d = list(map(int, inp.split()))
    return _ln(*["A" if x >= 90 else "B" if x >= 70 else "C" if x >= 50 else "F" for x in d[1:1 + d[0]]])


# ── Lv.1 ───────────────────────────────────────────────────────────────────────
def dedupe_receipts(inp: str) -> str:
    rows = inp.split()
    seen, out = set(), []
    for x in rows[1:1 + int(rows[0])]:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return _ln(_arr(out))


def caesar(inp: str) -> str:
    rows = inp.split("\n")
    k = int(rows[0]) % 26
    out = []
    for c in rows[1]:
        if "a" <= c <= "z":
            out.append(chr((ord(c) - 97 + k) % 26 + 97))
        elif "A" <= c <= "Z":
            out.append(chr((ord(c) - 65 + k) % 26 + 65))
        else:
            out.append(c)
    return _ln("".join(out))


def second_best(inp: str) -> str:
    d = list(map(int, inp.split()))
    u = sorted(set(d[1:1 + d[0]]), reverse=True)
    return _ln(u[1] if len(u) > 1 else -1)


def run_length(inp: str) -> str:
    s = inp.strip()
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        out.append(f"{s[i]}{j - i}")
        i = j
    return _ln("".join(out))


def border_sum(inp: str) -> str:
    rows = inp.strip().split("\n")
    h, w = map(int, rows[0].split())
    g = [list(map(int, r.split())) for r in rows[1:1 + h]]
    return _ln(sum(g[y][x] for y in range(h) for x in range(w) if y in (0, h - 1) or x in (0, w - 1)))


def vote_winner(inp: str) -> str:
    rows = inp.split()
    names = rows[1:1 + int(rows[0])]
    c = Counter(names)
    best = max(c.values())
    return _ln(min(k for k, v in c.items() if v == best), best)


def anagram(inp: str) -> str:
    rows = inp.split("\n")
    key = lambda s: sorted(c for c in s.lower() if c != " ")  # noqa: E731
    return _ln("YES" if key(rows[0]) == key(rows[1]) else "NO")


# ── Lv.2 ───────────────────────────────────────────────────────────────────────
def printer_order(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m, p = d[0], d[1], d[2:2 + d[0]]
    q = deque((x, i) for i, x in enumerate(p))
    counts = Counter(p)
    top = max(p)
    done = 0
    while q:
        x, i = q.popleft()
        if x < top:
            q.append((x, i))
            continue
        done += 1
        if i == m - 1:
            return _ln(done)
        counts[x] -= 1
        while top > 0 and counts[top] == 0:
            top -= 1
    return _ln(-1)


def next_taller(inp: str) -> str:
    d = list(map(int, inp.split()))
    a = d[1:1 + d[0]]
    out = [0] * len(a)
    st: list[int] = []
    for i, x in enumerate(a):
        while st and a[st[-1]] < x:
            out[st.pop()] = i + 1
        st.append(i)
    return _ln(_arr(out))


def top_keywords(inp: str) -> str:
    rows = inp.split()
    n, k = int(rows[0]), int(rows[1])
    c = Counter(rows[2:2 + n])
    top = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return _ln(*[f"{w} {v}" for w, v in top])


def badge_ledger(inp: str) -> str:
    rows = inp.split("\n")
    n, m = map(int, rows[0].split())
    sent: dict[tuple[str, str], int] = defaultdict(int)
    names = rows[1].split()
    for i in range(m):
        a, b = rows[2 + i].split()
        sent[(a, b)] += 1
    badge = {x: 0 for x in names}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = names[i], names[j]
            if sent[(a, b)] > sent[(b, a)]:
                badge[b] += 1
            elif sent[(a, b)] < sent[(b, a)]:
                badge[a] += 1
    best = max(badge.values())
    return _ln(f"{min(x for x in names if badge[x] == best)} {best}")


def best_window(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, k, a = d[0], d[1], d[2:2 + d[0]]
    cur = sum(a[:k])
    best = cur
    for i in range(k, n):
        cur += a[i] - a[i - k]
        best = max(best, cur)
    return _ln(best)


def shortest_span(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, t, a = d[0], d[1], d[2:2 + d[0]]
    best, cur, l = n + 1, 0, 0
    for r, x in enumerate(a):
        cur += x
        while cur >= t:
            best = min(best, r - l + 1)
            cur -= a[l]
            l += 1
    return _ln(best if best <= n else 0)


def merge_bookings(inp: str) -> str:
    d = list(map(int, inp.split()))
    iv = sorted((d[1 + 2 * i], d[2 + 2 * i]) for i in range(d[0]))
    out = [list(iv[0])]
    for s, e in iv[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return _ln(*[f"{s} {e}" for s, e in out])


def sort_logs(inp: str) -> str:
    rows = inp.rstrip("\n").split("\n")
    rank = {"ERROR": 0, "WARN": 1, "INFO": 2}
    logs = []
    for i in range(int(rows[0])):
        lv, t, msg = rows[1 + i].split(" ", 2)
        logs.append((rank[lv], int(t), i, lv, msg))
    logs.sort(key=lambda x: x[:3])
    return _ln(*[f"{lv} {t} {msg}" for _, t, _i, lv, msg in logs])


# ── Lv.3 ───────────────────────────────────────────────────────────────────────
def sprinkler(inp: str) -> str:
    rows = inp.strip().split("\n")
    h, w = map(int, rows[0].split())
    g = rows[1:1 + h]
    dist = [[-1] * w for _ in range(h)]
    q = deque()
    for y in range(h):
        for x in range(w):
            if g[y][x] == "S":
                dist[y][x] = 0
                q.append((y, x))
    best = 0
    while q:
        y, x = q.popleft()
        best = max(best, dist[y][x])
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and g[ny][nx] == "." and dist[ny][nx] < 0:
                dist[ny][nx] = dist[y][x] + 1
                q.append((ny, nx))
    if any(g[y][x] == "." and dist[y][x] < 0 for y in range(h) for x in range(w)):
        return _ln(-1)
    return _ln(best)


def network_groups(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    p = list(range(n + 1))

    def find(x):
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x
    for i in range(m):
        a, b = find(d[2 + 2 * i]), find(d[3 + 2 * i])
        if a != b:
            p[a] = b
    sizes = Counter(find(i) for i in range(1, n + 1))
    return _ln(f"{len(sizes)} {max(sizes.values())}")


def delivery_time(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m, s = d[0], d[1], d[2]
    adj = [[] for _ in range(n + 1)]
    for i in range(m):
        a, b, c = d[3 + 3 * i: 6 + 3 * i]
        adj[a].append((b, c))
    dist = [None] * (n + 1)
    h = [(0, s)]
    while h:
        w, u = heapq.heappop(h)
        if dist[u] is not None:
            continue
        dist[u] = w
        for v, c in adj[u]:
            if dist[v] is None:
                heapq.heappush(h, (w + c, v))
    return _ln(_arr(-1 if x is None else x for x in dist[1:]))


def course_order(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    adj = [[] for _ in range(n + 1)]
    deg = [0] * (n + 1)
    for i in range(m):
        a, b = d[2 + 2 * i], d[3 + 2 * i]
        adj[a].append(b)
        deg[b] += 1
    h = [i for i in range(1, n + 1) if deg[i] == 0]
    heapq.heapify(h)
    out = []
    while h:
        u = heapq.heappop(h)
        out.append(u)
        for v in adj[u]:
            deg[v] -= 1
            if deg[v] == 0:
                heapq.heappush(h, v)
    return _ln(_arr(out) if len(out) == n else "IMPOSSIBLE")


def budget_pick(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, cap = d[0], d[1]
    best = [0] * (cap + 1)
    for i in range(n):
        c, v = d[2 + 2 * i], d[3 + 2 * i]
        for x in range(cap, c - 1, -1):
            best[x] = max(best[x], best[x - c] + v)
    return _ln(best[cap])


def growth_streak(inp: str) -> str:
    d = list(map(int, inp.split()))
    tails: list[int] = []
    for x in d[1:1 + d[0]]:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return _ln(len(tails))


def line_time(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m, a = d[0], d[1], d[2:2 + d[0]]
    lo, hi = 0, min(a) * m
    while lo < hi:
        mid = (lo + hi) // 2
        if sum(mid // x for x in a) >= m:
            hi = mid
        else:
            lo = mid + 1
    return _ln(lo)


def pipe_shapes(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    nxt = [0] * (n + 1)
    has_in = [False] * (n + 1)
    for i in range(m):
        a, b = d[2 + 2 * i], d[3 + 2 * i]
        nxt[a] = b
        has_in[b] = True
    seen = [False] * (n + 1)
    lines = 0
    for i in range(1, n + 1):
        if not has_in[i]:
            lines += 1
            u = i
            while u and not seen[u]:
                seen[u] = True
                u = nxt[u]
    rings = 0
    for i in range(1, n + 1):
        if not seen[i]:
            rings += 1
            u = i
            while not seen[u]:
                seen[u] = True
                u = nxt[u]
    return _ln(f"{lines} {rings}")


# ── Lv.4 ───────────────────────────────────────────────────────────────────────
def common_fragment(inp: str) -> str:
    rows = inp.split("\n")
    a, b = rows[0].strip(), rows[1].strip()
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else max(prev[j + 1], cur[j]))
        prev = cur
    return _ln(prev[-1])


def typo_cost(inp: str) -> str:
    rows = inp.split("\n")
    ins, dele, sub = map(int, rows[0].split())
    a, b = rows[1].strip(), rows[2].strip()
    prev = [j * ins for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [i * dele]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + dele, cur[j - 1] + ins, prev[j - 1] + (0 if ca == cb else sub)))
        prev = cur
    return _ln(prev[-1])


def autocomplete(inp: str) -> str:
    rows = inp.split()
    n, q = int(rows[0]), int(rows[1])
    trie: list[dict] = [{}]
    count = [0]
    for w in rows[2:2 + n]:
        u = 0
        for c in w:
            if c not in trie[u]:
                trie[u][c] = len(trie)
                trie.append({})
                count.append(0)
            u = trie[u][c]
            count[u] += 1
    out = []
    for w in rows[2 + n:2 + n + q]:
        u = 0
        for c in w:
            u = trie[u].get(c, -1)
            if u < 0:
                break
        out.append(count[u] if u >= 0 else 0)
    return _ln(*out)


def cable_cost(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    edges = sorted((d[4 + 3 * i], d[2 + 3 * i], d[3 + 3 * i]) for i in range(m))
    p = list(range(n + 1))

    def find(x):
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x
    total, used = 0, 0
    for c, a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            p[ra] = rb
            total += c
            used += 1
    return _ln(total if used == n - 1 else -1)


def keys_doors(inp: str) -> str:
    rows = inp.strip().split("\n")
    h, w = map(int, rows[0].split())
    g = rows[1:1 + h]
    sy, sx = next((y, x) for y in range(h) for x in range(w) if g[y][x] == "S")
    seen = {(sy, sx, 0)}
    q = deque([(sy, sx, 0, 0)])
    while q:
        y, x, k, d = q.popleft()
        if g[y][x] == "E":
            return _ln(d)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or g[ny][nx] == "#":
                continue
            c, nk = g[ny][nx], k
            if "A" <= c <= "D" and not k >> (ord(c) - 65) & 1:
                continue
            if "a" <= c <= "d":
                nk = k | 1 << (ord(c) - 97)
            if (ny, nx, nk) not in seen:
                seen.add((ny, nx, nk))
                q.append((ny, nx, nk, d + 1))
    return _ln(-1)


def stock_ranges(inp: str) -> str:
    d = inp.split()
    n, q = int(d[0]), int(d[1])
    a = [0] + list(map(int, d[2:2 + n]))
    t = [0] * (n + 1)
    for i in range(1, n + 1):
        t[i] += a[i]
        j = i + (i & -i)
        if j <= n:
            t[j] += t[i]
    pos, out = 2 + n, []

    def pre(i):
        s = 0
        while i:
            s += t[i]
            i -= i & -i
        return s
    for _ in range(q):
        op, x, y = d[pos], int(d[pos + 1]), int(d[pos + 2])
        pos += 3
        if op == "S":
            diff, a[x] = y - a[x], y
            while x <= n:
                t[x] += diff
                x += x & -x
        else:
            out.append(pre(y) - pre(x - 1))
    return _ln(*out)


def team_dinner(inp: str) -> str:
    d = list(map(int, inp.split()))
    n = d[0]
    v = [0] + d[1:1 + n]
    kids = [[] for _ in range(n + 1)]
    for i in range(2, n + 1):
        kids[d[n + i - 1]].append(i)
    order, st = [], [1]
    while st:
        u = st.pop()
        order.append(u)
        st.extend(kids[u])
    take, skip = [0] * (n + 1), [0] * (n + 1)
    for u in reversed(order):
        take[u] = v[u] + sum(skip[c] for c in kids[u])
        skip[u] = sum(max(take[c], skip[c]) for c in kids[u])
    return _ln(max(take[1], skip[1]))


# ── Lv.5 ───────────────────────────────────────────────────────────────────────
def range_add_max(inp: str) -> str:
    d = inp.split()
    n, q = int(d[0]), int(d[1])
    size = 1
    while size < n:
        size *= 2
    top = [0] * (2 * size)
    add = [0] * (2 * size)
    for i in range(n):
        top[size + i] = int(d[2 + i])
    for i in range(size - 1, 0, -1):
        top[i] = max(top[2 * i], top[2 * i + 1])

    def update(i, lo, hi, l, r, x):
        if r < lo or hi < l:
            return
        if l <= lo and hi <= r:
            top[i] += x
            add[i] += x
            return
        m = (lo + hi) // 2
        update(2 * i, lo, m, l, r, x)
        update(2 * i + 1, m + 1, hi, l, r, x)
        top[i] = max(top[2 * i], top[2 * i + 1]) + add[i]

    def query(i, lo, hi, l, r):
        if r < lo or hi < l:
            return -10**18
        if l <= lo and hi <= r:
            return top[i]
        m = (lo + hi) // 2
        return max(query(2 * i, lo, m, l, r), query(2 * i + 1, m + 1, hi, l, r)) + add[i]
    pos, out = 2 + n, []
    for _ in range(q):
        if d[pos] == "A":
            update(1, 0, size - 1, int(d[pos + 1]) - 1, int(d[pos + 2]) - 1, int(d[pos + 3]))
            pos += 4
        else:
            out.append(query(1, 0, size - 1, int(d[pos + 1]) - 1, int(d[pos + 2]) - 1))
            pos += 3
    return _ln(*out)


def delivery_tour(inp: str) -> str:
    d = list(map(int, inp.split()))
    n = d[0]
    c = [d[1 + i * n:1 + (i + 1) * n] for i in range(n)]
    inf = float("inf")
    dp = [[inf] * n for _ in range(1 << n)]
    dp[1][0] = 0
    for mask in range(1 << n):
        if not mask & 1:
            continue
        row = dp[mask]
        for u in range(n):
            if row[u] == inf:
                continue
            for v in range(n):
                if mask >> v & 1 or c[u][v] == 0:
                    continue
                w = row[u] + c[u][v]
                if w < dp[mask | 1 << v][v]:
                    dp[mask | 1 << v][v] = w
    full = (1 << n) - 1
    best = min((dp[full][u] + c[u][0] for u in range(1, n) if c[u][0] and dp[full][u] < inf), default=inf)
    return _ln(-1 if best == inf else best)


def dependency_cycles(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    adj = [[] for _ in range(n + 1)]
    for i in range(m):
        adj[d[2 + 2 * i]].append(d[3 + 2 * i])
    idx = [0] * (n + 1)
    low = [0] * (n + 1)
    on = [False] * (n + 1)
    st: list[int] = []
    counter = 0
    sizes = []
    for r in range(1, n + 1):
        if idx[r]:
            continue
        counter += 1
        idx[r] = low[r] = counter
        st.append(r)
        on[r] = True
        work = [(r, 0)]
        while work:
            u, i = work.pop()
            if i < len(adj[u]):
                work.append((u, i + 1))
                v = adj[u][i]
                if not idx[v]:
                    counter += 1
                    idx[v] = low[v] = counter
                    st.append(v)
                    on[v] = True
                    work.append((v, 0))
                elif on[v]:
                    low[u] = min(low[u], idx[v])
            else:
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[u])
                if low[u] == idx[u]:
                    size = 0
                    while True:
                        v = st.pop()
                        on[v] = False
                        size += 1
                        if v == u:
                            break
                    sizes.append(size)
    return _ln(f"{len(sizes)} {max(sizes)}")


def org_distance(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, q = d[0], d[1]
    kids = [[] for _ in range(n + 1)]
    log = 1
    while 1 << log < n:
        log += 1
    up = [[1] * (n + 1)]
    for i in range(2, n + 1):
        up[0][i] = d[i]
        kids[d[i]].append(i)
    depth = [0] * (n + 1)
    st = [1]
    while st:
        u = st.pop()
        for v in kids[u]:
            depth[v] = depth[u] + 1
            st.append(v)
    for k in range(1, log + 1):
        up.append([up[k - 1][up[k - 1][i]] for i in range(n + 1)])
    out = []
    pos = n + 1
    for _ in range(q):
        a, b = d[pos], d[pos + 1]
        pos += 2
        x, y = (a, b) if depth[a] >= depth[b] else (b, a)
        diff = depth[x] - depth[y]
        for k in range(log + 1):
            if diff >> k & 1:
                x = up[k][x]
        if x != y:
            for k in range(log, -1, -1):
                if up[k][x] != up[k][y]:
                    x, y = up[k][x], up[k][y]
            x = up[0][x]
        out.append(f"{x} {depth[a] + depth[b] - 2 * depth[x]}")
    return _ln(*out)


def max_throughput(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, m = d[0], d[1]
    cap: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for i in range(m):
        a, b, c = d[2 + 3 * i:5 + 3 * i]
        cap[a][b] += c
        cap[b][a] += 0
    total = 0
    while True:
        prev = {1: 0}
        q = deque([1])
        while q and n not in prev:
            u = q.popleft()
            for v, c in cap[u].items():
                if c > 0 and v not in prev:
                    prev[v] = u
                    q.append(v)
        if n not in prev:
            return _ln(total)
        f, v = 10**18, n
        while v != 1:
            f = min(f, cap[prev[v]][v])
            v = prev[v]
        v = n
        while v != 1:
            cap[prev[v]][v] -= f
            cap[v][prev[v]] += f
            v = prev[v]
        total += f


def billboard_area(inp: str) -> str:
    d = list(map(int, inp.split()))
    n = d[0]
    r = [d[1 + 4 * i:5 + 4 * i] for i in range(n)]
    xs = sorted({v for a in r for v in (a[0], a[2])})
    total = 0
    for x1, x2 in zip(xs, xs[1:]):
        spans = sorted((a[1], a[3]) for a in r if a[0] <= x1 and x2 <= a[2])
        cover, end = 0, None
        for lo, hi in spans:
            if end is None or lo > end:
                cover += hi - lo
                end = hi
            elif hi > end:
                cover += hi - end
                end = hi
        total += cover * (x2 - x1)
    return _ln(total)


def merge_files(inp: str) -> str:
    d = list(map(int, inp.split()))
    n, a = d[0], d[1:1 + d[0]]
    p = [0]
    for x in a:
        p.append(p[-1] + x)
    dp = [[0] * n for _ in range(n)]
    opt = [[i] * n for i in range(n)]
    for span in range(1, n):
        for i in range(n - span):
            j = i + span
            lo, hi = opt[i][j - 1], min(opt[i + 1][j], j - 1)
            best, at = None, lo
            for k in range(lo, hi + 1):
                c = dp[i][k] + dp[k + 1][j]
                if best is None or c < best:
                    best, at = c, k
            dp[i][j], opt[i][j] = best + p[j + 1] - p[i], at
    return _ln(dp[0][n - 1])
