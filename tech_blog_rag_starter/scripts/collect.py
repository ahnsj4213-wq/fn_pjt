#!/usr/bin/env python3
"""Ethical, conservative tech blog collector. Dry-run discovers URLs; --collect saves articles.

Discovery order per company: official RSS/Atom feeds -> public listing pages -> sitemap XML.
Only URLs matching the company's article path pattern are candidates. Every request goes through
robots.txt (fail closed on network/TLS/5xx errors), a fixed delay, a timeout and a bounded retry.
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

try:
    # Use the operating system's certificate store (keeps TLS verification on). Needed when a local
    # antivirus/proxy re-signs HTTPS with a root that Windows trusts but certifi's bundle does not.
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - falls back to certifi
    truststore = None

import requests
import trafilatura
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"Accept": "text/html,application/xml,text/xml;q=0.9,*/*;q=0.5"}
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
ATOM = "{http://www.w3.org/2005/Atom}"
EXCLUDE_PATH = re.compile(r"/(?:tag|tags|category|categories|author|authors|page|search|feed|wp-json|about|recruit|careers)(?:/|$)", re.I)
LOG = logging.getLogger("collector")


def canonical(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", "", ""))


def decoded(r):
    """Response text with a correct charset: servers that omit it would otherwise be read as ISO-8859-1."""
    if "charset" in r.headers.get("Content-Type", "").lower():
        return r.text
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        r.encoding = r.apparent_encoding
        return r.text


class RobotsUnavailable(RuntimeError):
    pass


class PoliteClient:
    def __init__(self, base, allowed_hosts, delay, timeout, agent, retries):
        self.base = base
        self.hosts = set(allowed_hosts)
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({**HEADERS, "User-Agent": agent})
        self.agent = agent
        self.last_at = 0.0
        self.robots = RobotFileParser()
        self.sitemap_seeds = []
        self.robots_status = None
        robots_url = urljoin(base, "/robots.txt")
        res = self._request(robots_url)
        if isinstance(res, Exception):
            # Fail closed: an inaccessible robots policy is not permission to crawl.
            raise RobotsUnavailable(f"robots.txt 확인 실패: {robots_url}: {type(res).__name__}: {res}")
        if 200 <= res.status_code < 300 and res.text.strip():
            self.robots.parse(res.text.splitlines())
            self.robots_status = f"ok({res.status_code})"
            for line in res.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    self.sitemap_seeds.append(line.split(":", 1)[1].strip())
        elif res.status_code in (404, 410):
            # The server answered that no robots.txt exists. RFC 9309 §2.3.1.3: a 4xx means no crawl
            # restrictions. Recorded as "absent" so the report shows it; discovery stays on official
            # feeds and public listing pages from the config.
            self.robots.parse([])
            self.robots_status = f"absent({res.status_code})"
        else:
            # 401/403/429/5xx/empty body: the policy could not be read -> no crawling.
            raise RobotsUnavailable(f"robots.txt 확인 실패: {robots_url}: HTTP {res.status_code}")
        LOG.info("robots.txt %s: %s", robots_url, self.robots_status)

    def pause(self):
        remaining = self.delay - (time.monotonic() - self.last_at)
        if remaining > 0:
            time.sleep(remaining)
        self.last_at = time.monotonic()

    def _request(self, url):
        """One GET with a bounded retry for timeouts, connection errors, 429 and 5xx. Never loops forever."""
        last = None
        for attempt in range(self.retries + 1):
            self.pause()
            try:
                r = self.session.get(url, timeout=self.timeout, allow_redirects=False)
            except requests.exceptions.SSLError as exc:
                return exc  # a TLS failure does not get better by retrying
            except requests.RequestException as exc:
                last = exc
            else:
                if r.status_code not in (429, 500, 502, 503, 504):
                    return r
                last = r
                wait = r.headers.get("Retry-After", "")
                if wait.isdigit() and int(wait) > 60:
                    break  # the server asks for a long pause: give up on this URL
            if attempt < self.retries:
                time.sleep(self.delay * (2 ** attempt))
        return last

    def allowed(self, url):
        p = urlparse(url)
        return p.scheme == "https" and p.hostname in self.hosts and self.robots.can_fetch(self.agent, url)

    def get(self, url):
        if not self.allowed(url):
            LOG.warning("접근 거부 또는 다른 호스트: %s", url)
            return None
        r = self._request(url)
        if r is not None and not isinstance(r, Exception) and r.status_code in (301, 302, 303, 307, 308):
            next_url = urljoin(url, r.headers.get("Location", ""))
            if not self.allowed(next_url):
                LOG.warning("리다이렉트 거부: %s -> %s", url, next_url)
                return None
            r = self._request(next_url)
        if isinstance(r, Exception) or r is None:
            LOG.warning("요청 실패: %s (%s)", url, r)
            return None
        if r.status_code != 200:
            LOG.warning("HTTP %s: %s (건너뜀)", r.status_code, url)
            return None
        if len(r.content) > 5_000_000:
            LOG.warning("5MB 초과 응답 제외: %s", url)
            return None
        return r


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def feed_discover(client, feeds):
    """Official RSS/Atom feeds: url -> {title, published_at, content_html}."""
    items = {}
    for feed in feeds:
        r = client.get(feed)
        if r is None:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as exc:
            LOG.warning("피드 파싱 실패: %s (%s)", feed, exc)
            continue
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            enc = it.find(CONTENT_NS)
            items.setdefault(link, {"title": (it.findtext("title") or "").strip(),
                                    "published_at": parse_date(it.findtext("pubDate")),
                                    "content_html": enc.text if enc is not None else None})
        for it in root.iter(ATOM + "entry"):
            link_el = it.find(ATOM + "link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            content = it.find(ATOM + "content")
            items.setdefault(link.strip(), {"title": (it.findtext(ATOM + "title") or "").strip(),
                                            "published_at": parse_date(it.findtext(ATOM + "published") or it.findtext(ATOM + "updated")),
                                            "content_html": content.text if content is not None else None})
        LOG.info("피드 %s: 누적 %d건", feed, len(items))
    items.pop("", None)
    return items


def sitemap_discover(client, base, limit):
    seeds = client.sitemap_seeds + [urljoin(base, "/sitemap_index.xml"), urljoin(base, "/sitemap.xml")]
    pending = deque(dict.fromkeys(seeds))
    seen = set()
    links = []
    while pending and len(links) < limit and len(seen) < 60:
        url = pending.popleft()
        if url in seen:
            continue
        seen.add(url)
        r = client.get(url)
        if r is None or ("xml" not in r.headers.get("Content-Type", "").lower() and not r.text.lstrip().startswith("<")):
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        for loc in root.findall(".//s:loc", SITEMAP_NS) or root.findall(".//{*}loc"):
            u = (loc.text or "").strip()
            if not u:
                continue
            if urlparse(u).path.lower().endswith(".xml"):
                if len(seen) + len(pending) < 65:
                    pending.append(u)
            else:
                links.append(u)
                if len(links) >= limit:
                    break
    return links


def listing_discover(client, seeds, max_pages):
    """Public listing pages from the config (e.g. a category with ?page={n}); same-host links only."""
    links = []
    pages = 0
    for seed in seeds:
        numbered = "{n}" in seed
        for n in range(1, max_pages + 1 if numbered else 2):
            if pages >= max_pages:
                return links
            url = seed.format(n=n) if numbered else seed
            pages += 1
            r = client.get(url)
            if r is None or "html" not in r.headers.get("Content-Type", "").lower():
                break
            found = [canonical(urljoin(url, a["href"])) for a in BeautifulSoup(decoded(r), "html.parser").select("a[href]")]
            new = [h for h in dict.fromkeys(found) if h and client.allowed(h) and h not in links]
            if numbered and not new:
                break  # past the last page
            links += new
    return links


def is_article(url, pattern):
    path = urlparse(url).path
    if EXCLUDE_PATH.search(path) or path.lower().endswith((".png", ".jpg", ".pdf", ".xml", ".js", ".css")):
        return False
    return bool(re.search(pattern, path))


def clean_title(title, suffixes):
    title = re.sub(r"\s+", " ", title or "").strip()
    for s in suffixes:
        if title.endswith(s):
            title = title[: -len(s)].strip()
    return title


def text_of_html(html):
    text = trafilatura.extract(f"<html><body><article>{html}</article></body></html>", include_comments=False,
                               include_tables=True, favor_recall=True)
    return (text or BeautifulSoup(html, "html.parser").get_text("\n", strip=True)).strip()


def tech_score(text, keywords):
    low = text.lower()
    return sum(1 for k in keywords if k.lower() in low)


def extract_article(client, url, company, settings, feed_item):
    r = client.get(url)  # always fetched: proves the source URL is live and gives the page metadata
    if r is None or "html" not in r.headers.get("Content-Type", "").lower():
        return None, "not_html_or_blocked"
    html = decoded(r)
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("meta", property="og:title") or {}).get("content")
    title = title or (feed_item or {}).get("title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    title = clean_title(title, company.get("title_suffixes", []))
    if not title:
        return None, "no_title"
    if any(term.lower() in title.lower() for term in settings["exclude_title_terms"]):
        return None, "excluded_title"
    text = (trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True) or "").strip()
    method = "html"
    if feed_item and feed_item.get("content_html"):
        feed_text = text_of_html(feed_item["content_html"])
        if len(feed_text) > len(text) * 1.1:  # the feed carries the full body (the page extraction lost parts)
            text, method = feed_text, "feed"
    text = strip_trailing_promo(text)
    if len(text) < settings["min_body_characters"]:
        return None, "short_or_empty_body"
    score = tech_score(title + "\n" + text, settings["keywords"])
    if score < settings["min_tech_keywords"]:
        return None, f"not_technical(score={score})"
    date = (feed_item or {}).get("published_at")
    for key, val in (("property", "article:published_time"), ("name", "date"), ("itemprop", "datePublished")):
        if date:
            break
        meta = soup.find("meta", attrs={key: val})
        if meta and meta.get("content"):
            date = parse_date(meta["content"])
    return make_doc(company, title, url, date, text, method, score), None


def strip_trailing_promo(text):
    """Drop recruiting / follow-us lines at the very end of a post (not the article itself)."""
    lines = text.splitlines()
    while lines and len(lines) > 3 and re.search(r"채용|지원하기|구독하기|함께하실", lines[-1]) and len(lines[-1]) < 200:
        lines.pop()
    return "\n".join(lines).strip()


def language_of(text):
    letters = re.findall(r"[A-Za-z가-힣]", text)
    hangul = sum(1 for ch in letters if "가" <= ch <= "힣")
    return "ko" if letters and hangul / len(letters) >= 0.2 else "en"


def make_doc(company, title, url, date, text, method, score, collected_at=None):
    return {
        "document_id": hashlib.sha256((company["company_id"] + "|" + canonical(url)).encode()).hexdigest()[:24],
        "company_id": company["company_id"],
        "company_name": company["name"],
        "document_type": "tech_blog",
        "title": title,
        "source_url": url,
        "published_at": date,
        "collected_at": collected_at or datetime.now(timezone.utc).isoformat(),
        "language": language_of(text),
        "is_synthetic": False,
        "extraction_method": method,
        "tech_keyword_score": score,
        "content_length": len(text),
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "content": text,
    }


def remove_repeated_lines(docs, min_docs=3, ratio=0.3):
    """Lines that appear in many posts of the same blog are boilerplate (menus, footers, share text)."""
    if len(docs) < min_docs:
        return docs
    freq = Counter()
    for d in docs:
        freq.update({ln.strip() for ln in d["content"].splitlines() if ln.strip()})
    limit = max(min_docs, int(len(docs) * ratio))
    # Short lines ("}", "마치며") repeat by nature: only sentence-length lines count as boilerplate.
    common = {ln for ln, c in freq.items() if c >= limit and 15 <= len(ln) < 300}
    if common:
        LOG.info("공통 반복 줄 %d개 제거: %s", len(common), [c[:40] for c in list(common)[:5]])
    out = []
    for d in docs:
        text = "\n".join(ln for ln in d["content"].splitlines() if ln.strip() not in common).strip()
        out.append({**d, "content": text, "content_length": len(text), "content_sha256": hashlib.sha256(text.encode()).hexdigest()})
    return out


def main():
    ap = argparse.ArgumentParser(description="기업 기술 블로그 후보 탐색 및 수집")
    ap.add_argument("--collect", action="store_true", help="원문 수집 실행 (기본: URL 후보 탐색만)")
    ap.add_argument("--company", choices=["woowahan", "kurly", "toss"], help="특정 기업만 실행")
    ap.add_argument("--limit", type=int, help="기업별 문서 수 목표 덮어쓰기 (1~20)")
    args = ap.parse_args()
    if args.limit is not None and not (1 <= args.limit <= 20):
        ap.error("--limit은 1~20")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text(encoding="utf-8"))
    st = cfg["settings"]
    (ROOT / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "reports").mkdir(parents=True, exist_ok=True)
    LOG.info("TLS 인증서: %s", "운영체제 저장소(truststore)" if truststore else "certifi 번들")
    result = ROOT / "data" / "reports" / "summary.json"
    previous = {}
    if result.exists():
        try:
            previous = {s["company"]: s for s in json.loads(result.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError):
            previous = {}
    summary = {}
    for comp in cfg["companies"]:
        if args.company and args.company != comp["company_id"]:
            continue
        cid = comp["company_id"]
        target = args.limit or comp["target"]
        LOG.info("[%s] 시작 (목표 %d건)", cid, target)
        try:
            client = PoliteClient(comp["base_url"], comp["allowed_hosts"], comp.get("delay_seconds", st["delay_seconds"]), st["timeout_seconds"],
                                  st["user_agent"], st["max_retries"])
        except RobotsUnavailable as exc:
            LOG.error("[%s] %s", cid, exc)
            summary[cid] = {"company": cid, "status": "robots_unavailable", "error": str(exc), "saved": 0, "target": target}
            continue
        feed_items = feed_discover(client, comp.get("feeds", []))
        all_urls = list(feed_items)
        listing_urls = listing_discover(client, comp.get("listing_urls", []), st["max_listing_pages_per_company"])
        all_urls += listing_urls
        if comp.get("use_sitemap", True):
            all_urls += sitemap_discover(client, comp["base_url"], st["max_sitemap_urls_per_company"])
        by_key = {}
        for u in all_urls:
            key = canonical(u)
            if key and key not in by_key and client.allowed(u) and is_article(u, comp["article_path"]):
                by_key[key] = u
        feed_by_key = {canonical(u): v for u, v in feed_items.items() if canonical(u)}
        if comp.get("require_listing"):
            # The feed mixes categories (e.g. design); only posts on the listed category pages are candidates.
            listed = {canonical(u) for u in listing_urls}
            by_key = {k: u for k, u in by_key.items() if k in listed}
        candidates = list(by_key.items())[: st["max_candidates_per_company"]]
        report = ROOT / "data" / "reports" / f"{cid}_candidates.csv"
        with report.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["company_id", "order", "source_url", "from_feed", "review_status"])
            for i, (key, u) in enumerate(candidates, 1):
                w.writerow([cid, i, u, key in feed_by_key, "pending"])
        LOG.info("[%s] 후보 URL %d개 (robots %s) -> %s", cid, len(candidates), client.robots_status, report)
        if not args.collect:
            summary[cid] = {"company": cid, "status": "discovered_only", "robots": client.robots_status,
                            "candidates": len(candidates), "saved": 0, "target": target}
            continue
        output = ROOT / "data" / "raw" / f"{cid}.jsonl"
        existing = {}
        if output.exists():
            for line in output.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    existing[canonical(d["source_url"])] = d
        rejected = []
        hashes = {d["content_sha256"] for d in existing.values()}
        for key, url in candidates:
            if len(existing) >= target:
                break
            if key in existing:
                continue
            doc, reason = extract_article(client, url, comp, st, feed_by_key.get(key))
            if doc is None:
                rejected.append({"source_url": url, "reason": reason})
                LOG.info("[%s] 제외 (%s): %s", cid, reason, url)
                continue
            if doc["content_sha256"] in hashes:
                rejected.append({"source_url": url, "reason": "duplicate_content"})
                continue
            hashes.add(doc["content_sha256"])
            existing[key] = doc
            LOG.info("[%s] 수집 %d/%d: %s", cid, len(existing), target, doc["title"][:55])
        docs = remove_repeated_lines(list(existing.values()))
        kept, seen_hash = [], set()
        for d in docs:
            if d["content_length"] < st["min_body_characters"] or d["content_sha256"] in seen_hash:
                rejected.append({"source_url": d["source_url"], "reason": "short_or_duplicate_after_cleanup"})
                continue
            seen_hash.add(d["content_sha256"])
            kept.append(d)
        with output.open("w", encoding="utf-8") as f:
            for d in kept[:target]:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        rej_file = ROOT / "data" / "reports" / f"{cid}_rejected.json"
        if rejected or not rej_file.exists():  # a re-run that tried nothing new keeps the earlier rejection log
            rej_file.write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = min(len(kept), target)
        summary[cid] = {"company": cid, "status": "target_met" if saved >= target else "shortfall", "robots": client.robots_status,
                        "saved": saved, "target": target, "rejected": len(rejected), "candidates": len(candidates)}
    merged = [summary.get(c["company_id"]) or previous.get(c["company_id"]) for c in cfg["companies"]]
    merged = [m for m in merged if m]
    result.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("실행 결과: %s", result)
    print(json.dumps(list(summary.values()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
