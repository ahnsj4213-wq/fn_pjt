"""BM25 keyword search over company_knowledge (ADR-0030), run beside the pgvector search and fused with it in knowledge.py.

Why: the embedding model ranks a chunk by meaning and misses a chunk that names the exact technology ("Kafka Streams",
"OpenSearch") when the rest of its text is about something else. BM25 finds chunks by the words they share with the query,
weighted by how rare the word is (IDF), how often the chunk uses it (TF, saturating at k1) and how long the chunk is (b).

    score(q, d) = sum over distinct query terms t:  idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
    idf(t)      = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))      (never negative)

Tokens (no morphological analyser: no new dependency, measured enough in ADR-0030):
  - latin/number runs lower-cased ("PostgreSQL" -> postgresql, "c++", "node.js"); a dotted or hyphenated one also gives its parts
  - a Hangul run with one trailing particle removed (카프카를 -> 카프카), plus its character bigrams, so a compound or an
    inflected word still matches part of another (스트리밍처리 ~ 스트리밍)
  - a Hangul spelling of a technology also gives the latin name (카프카 -> kafka), from a short fixed list

The index is built from the database per pool (one company, or the shared pool of the other companies' non-blog documents),
kept in this process and rebuilt when the pool's rows change (count, text length, newest row). Every search still filters by
company, posting and excluded documents at scoring time, so a chunk outside the caller's scope is never returned.
"""
import math
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text

from app.core.config import get_settings

K1 = 1.2
B = 0.75
MAX_POOLS = 64  # companies kept in memory per process (each a few hundred chunks)

_LATIN = re.compile(r"[a-z0-9][a-z0-9+#._\-]*")
_HANGUL = re.compile(r"[가-힣]+")
_JOSA = ("에서는", "으로는", "에서", "으로", "에게", "까지", "부터", "보다", "처럼", "이나", "이라", "라는", "하는", "했던", "하고",
         "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "도", "의", "만", "며")
# Hangul spellings of technology names that blogs and answers use interchangeably with the latin name.
TECH_ALIASES = {
    "카프카": "kafka", "쿠버네티스": "kubernetes", "레디스": "redis", "스프링": "spring", "스프링부트": "spring",
    "엘라스틱서치": "elasticsearch", "오픈서치": "opensearch", "파이썬": "python", "자바": "java", "코틀린": "kotlin",
    "도커": "docker", "포스트그레스": "postgresql", "포스트그레스큐엘": "postgresql", "마이에스큐엘": "mysql", "몽고디비": "mongodb",
    "에어플로우": "airflow", "스파크": "spark", "하둡": "hadoop", "리액트": "react", "타입스크립트": "typescript",
    "자바스크립트": "javascript", "그래파나": "grafana", "프로메테우스": "prometheus", "테라폼": "terraform", "깃허브": "github",
    "젠킨스": "jenkins", "아르고": "argo", "엔진엑스": "nginx", "그래프큐엘": "graphql", "웹소켓": "websocket",
}


def _stem(word: str) -> str:
    for j in _JOSA:
        if len(word) > len(j) + 1 and word.endswith(j):
            return word[: -len(j)]
    return word


def tokenize(body: str) -> list[str]:
    """The terms of `body`, repeated as often as they occur (TF counts them)."""
    s = unicodedata.normalize("NFKC", body or "").lower()
    out: list[str] = []
    for tok in _LATIN.findall(s):
        tok = tok.strip("._-")
        if not tok:
            continue
        out.append(tok)
        if any(ch in tok for ch in "._-"):
            out.extend(p for p in re.split(r"[._\-]+", tok) if len(p) >= 2 and p != tok)
    for run in _HANGUL.findall(s):
        w = _stem(run)
        if len(w) < 2:
            continue
        out.append(w)
        if w in TECH_ALIASES:
            out.append(TECH_ALIASES[w])
        if len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


@dataclass
class Doc:
    id: uuid.UUID
    company_id: uuid.UUID
    posting_id: uuid.UUID | None
    doc_id: uuid.UUID | None
    doc_type: str
    title: str
    text: str
    tf: Counter
    length: int


class Index:
    """BM25 statistics of one pool of chunks."""

    def __init__(self, docs: list[Doc]) -> None:
        self.docs = docs
        self.n = len(docs)
        self.avgdl = (sum(d.length for d in docs) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for d in docs:
            df.update(d.tf.keys())
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.postings: dict[str, list[int]] = {}
        for i, d in enumerate(docs):
            for t in d.tf:
                self.postings.setdefault(t, []).append(i)

    def score(self, query: str, allow, min_coverage: float = 0.0) -> list[tuple[Doc, float]]:
        """Every doc `allow` accepts with a positive score, best first. `min_coverage`: the share of the query's IDF weight the doc
        must hold (sum of idf of the query terms it contains / sum of idf of the query terms the pool knows), so a chunk that
        shares one common word with a long query does not get in; the pgvector list has its similarity floor for the same reason."""
        terms = set(tokenize(query))
        acc: dict[int, float] = {}
        held: dict[int, float] = {}
        total = sum(self.idf[t] for t in terms if t in self.idf)
        for t in terms:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                d = self.docs[i]
                f = d.tf[t]
                acc[i] = acc.get(i, 0.0) + idf * f * (K1 + 1) / (f + K1 * (1 - B + B * d.length / (self.avgdl or 1)))
                held[i] = held.get(i, 0.0) + idf
        floor = min_coverage * total
        ranked = sorted(((self.docs[i], s) for i, s in acc.items() if s > 0 and held[i] >= floor and allow(self.docs[i])),
                        key=lambda x: -x[1])
        return ranked


_cache: dict[tuple, tuple[tuple, Index]] = {}

_POOL_SQL = {
    "company": "FROM company_knowledge WHERE company_id = :c",
    "shared": "FROM company_knowledge WHERE origin <> 'TECH_BLOG'",
}


async def _index(db, pool: str, company_id: uuid.UUID) -> Index:
    """The pool's index, rebuilt only when its rows changed (two short reads, no transaction held across them)."""
    where = _POOL_SQL[pool]
    params = {"c": company_id} if pool == "company" else {}
    sig = tuple((await db.execute(text(
        f"SELECT count(*), coalesce(sum(length(text)), 0), max(created_at) {where}"), params)).one())
    key = (pool, company_id if pool == "company" else None)
    hit = _cache.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    rows = (await db.execute(text(f"SELECT id, company_id, posting_id, doc_id, doc_type, title, text {where}"), params)).all()
    docs = []
    for i, c, p, d, dt, title, body in rows:
        tf = Counter(tokenize(f"{title}\n{body}"))
        docs.append(Doc(i, c, p, d, dt or "", title, body, tf, sum(tf.values())))
    index = Index(docs)
    if len(_cache) >= MAX_POOLS and key not in _cache:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (sig, index)
    return index


async def search(db, *, company_id: uuid.UUID, posting_id: uuid.UUID | None, query: str, n: int, exclude_docs=(),
                 shared: bool = False, company_wide_only: bool = False) -> list[tuple[Doc, float]]:
    """The n best chunks by BM25 in the same scope as the vector search of knowledge.py:
    own      this company, documents of all its postings or of this posting (`company_wide_only`: all its postings only)
    shared   every OTHER company's documents except TECH_BLOG ones (they are never lent)"""
    if company_id is None:
        raise ValueError("knowledge search needs a company")
    excluded = set(exclude_docs)
    if shared:
        index = await _index(db, "shared", company_id)

        def allow(d: Doc) -> bool:
            return d.company_id != company_id and d.doc_id not in excluded
    else:
        index = await _index(db, "company", company_id)

        def allow(d: Doc) -> bool:
            if d.company_id != company_id or d.doc_id in excluded:
                return False
            return d.posting_id is None or (not company_wide_only and d.posting_id == posting_id)
    return index.score(query, allow, get_settings().rag_bm25_min_coverage)[:n]


def clear_cache() -> None:
    _cache.clear()
