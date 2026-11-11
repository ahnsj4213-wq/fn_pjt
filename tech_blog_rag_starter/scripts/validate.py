#!/usr/bin/env python3
"""Validate collected JSONL outputs, report company counts/quality issues and write collection_report.md.

    python scripts/validate.py               # file checks only (no network)
    python scripts/validate.py --check-urls  # also re-requests every source URL (robots.txt honoured, 2 s apart)

Exit code 0 only when every document passes AND every company reached its target.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
REPORTS = ROOT / 'data' / 'reports'
REQUIRED = ('document_id', 'company_id', 'company_name', 'title', 'source_url', 'document_type', 'content',
            'content_length', 'content_sha256')
MIN_BODY = 1000


def check_urls(docs, cfg):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from collect import PoliteClient, RobotsUnavailable  # same client: truststore TLS, robots.txt, delay, retry
    st = cfg['settings']
    comps = {c['company_id']: c for c in cfg['companies']}
    status = {}
    for cid in sorted({d['company_id'] for d in docs}):
        c = comps[cid]
        try:
            client = PoliteClient(c['base_url'], c['allowed_hosts'], c.get('delay_seconds', st['delay_seconds']),
                                  st['timeout_seconds'], st['user_agent'], st['max_retries'])
        except RobotsUnavailable as exc:
            for d in docs:
                if d['company_id'] == cid:
                    status[d['source_url']] = f'unchecked ({exc})'
            continue
        for d in docs:
            if d['company_id'] == cid:
                status[d['source_url']] = 'ok(200)' if client.get(d['source_url']) is not None else 'FAILED'
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-urls', action='store_true', help='출처 URL을 다시 요청해 200인지 확인')
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / 'config' / 'companies.yaml').read_text(encoding='utf-8'))
    targets = {c['company_id']: c['target'] for c in cfg['companies']}
    names = {c['company_id']: c['name'] for c in cfg['companies']}

    counts = Counter()
    errors = []
    docs = []
    seen_url, seen_hash = {}, {}
    for file in sorted(RAW.glob('*.jsonl')):
        for lineno, line in enumerate(file.read_text(encoding='utf-8').splitlines(), 1):
            where = f'{file.name}:{lineno}'
            try:
                doc = json.loads(line)
                for key in REQUIRED:
                    if doc.get(key) in (None, ''):
                        raise ValueError(f'missing {key}')
                if 'published_at' not in doc:
                    raise ValueError('missing published_at key (null is allowed)')
                if doc['company_id'] != file.stem:
                    raise ValueError(f'company_id {doc["company_id"]} in {file.name}')
                if doc.get('is_synthetic') is not False:
                    raise ValueError('is_synthetic must be false')
                if doc['document_type'] != 'tech_blog':
                    raise ValueError(f'document_type {doc["document_type"]}')
                if not doc['source_url'].startswith('https://'):
                    raise ValueError('source_url is not https')
                if doc['source_url'] in seen_url:
                    raise ValueError(f'duplicate URL (also {seen_url[doc["source_url"]]})')
                seen_url[doc['source_url']] = where
                if len(doc['content']) < MIN_BODY:
                    raise ValueError('too short')
                if doc['content_length'] != len(doc['content']):
                    raise ValueError('content_length mismatch')
                if hashlib.sha256(doc['content'].encode()).hexdigest() != doc['content_sha256']:
                    raise ValueError('content checksum mismatch')
                if doc['content_sha256'] in seen_hash:
                    raise ValueError(f'duplicate content (also {seen_hash[doc["content_sha256"]]})')
                seen_hash[doc['content_sha256']] = where
                counts[doc['company_id']] += 1
                docs.append(doc)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append(f'{where}: {exc}')

    rejected = {}
    for cid in targets:
        f = REPORTS / f'{cid}_rejected.json'
        rejected[cid] = json.loads(f.read_text(encoding='utf-8')) if f.exists() else []
    summary = json.loads((REPORTS / 'summary.json').read_text(encoding='utf-8')) if (REPORTS / 'summary.json').exists() else []
    summary = {s['company']: s for s in summary}
    url_status = check_urls(docs, cfg) if args.check_urls else {}
    url_failed = [u for u, s in url_status.items() if s != 'ok(200)']

    shortfall = {cid: t - counts[cid] for cid, t in targets.items() if counts[cid] < t}
    print('회사별 수집 건수:', dict(counts))
    print('총 수집 건수:', sum(counts.values()), f'/ 목표 {sum(targets.values())}건')
    print('검증 오류:', len(errors))
    for e in errors:
        print('-', e)
    if args.check_urls:
        print('출처 URL 확인:', f'{len(url_status) - len(url_failed)}/{len(url_status)} 정상')
        for u in url_failed:
            print('-', u, url_status[u])
    if shortfall:
        print('목표 미달:', shortfall)

    write_report(docs, counts, targets, names, errors, rejected, summary, url_status, shortfall)
    if errors or shortfall or url_failed:
        sys.exit(1)
    print('검증 통과')


def write_report(docs, counts, targets, names, errors, rejected, summary, url_status, shortfall):
    now = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z')
    out = [f'# 기술 블로그 수집 결과 보고서', '', f'생성: {now} (scripts/validate.py)', '',
           '## 요약', '', '| 기업 | 목표 | 실제 확보 | 검증 통과 | robots.txt | 추출 제외 | 상태 |', '|---|---:|---:|---:|---|---:|---|']
    by_company = defaultdict(list)
    for d in docs:
        by_company[d['company_id']].append(d)
    bad = Counter(e.split('.jsonl')[0] for e in errors)
    for cid, t in targets.items():
        s = summary.get(cid, {})
        state = '달성' if counts[cid] >= t and not bad[cid] else ('미달' if counts[cid] < t else '오류')
        out.append(f'| {names[cid]} ({cid}) | {t} | {counts[cid] + bad[cid]} | {counts[cid]} | {s.get("robots", s.get("status", "-"))} '
                   f'| {len(rejected[cid])} | {state} |')
    out += ['', f'- 총 검증 통과 문서: **{sum(counts.values())} / {sum(targets.values())}**',
            f'- 검증 오류: {len(errors)}건' + (''.join(f'\n  - {e}' for e in errors)),
            f'- 출처 URL 재확인: ' + (f'{sum(1 for s in url_status.values() if s == "ok(200)")}/{len(url_status)} 정상 (HTTP 200)'
                                   if url_status else '실행 안 함 (`--check-urls`)')]
    if shortfall:
        out.append(f'- **목표 미달**: ' + ', '.join(f'{names[c]} {n}건 부족' for c, n in shortfall.items()))
    out += ['', '## 수집 실패·제외 사유', '']
    for cid in targets:
        s = summary.get(cid, {})
        reasons = Counter(r['reason'] for r in rejected[cid])
        line = f'- {names[cid]}: '
        if s.get('status') == 'robots_unavailable':
            line += f'수집 불가 — {s.get("error")}'
        else:
            line += ', '.join(f'{k} {v}건' for k, v in reasons.items()) or '제외 없음'
        out.append(line)
        for r in rejected[cid]:
            out.append(f'  - `{r["reason"]}` {r["source_url"]}')
    out += ['', '## 회사별 문서 목록', '']
    for cid in targets:
        out += [f'### {names[cid]} ({counts[cid]}건)', '', '| # | 제목 | 게시일 | 본문 길이 | URL 확인 |', '|---:|---|---|---:|---|']
        for i, d in enumerate(by_company[cid], 1):
            title = d['title'].replace('|', '\\|')
            out.append(f'| {i} | [{title}]({d["source_url"]}) | {(d.get("published_at") or "null")[:10]} | {d["content_length"]:,} '
                       f'| {url_status.get(d["source_url"], "-")} |')
        out.append('')
    (REPORTS / 'collection_report.md').write_text('\n'.join(out) + '\n', encoding='utf-8')
    print('보고서:', REPORTS / 'collection_report.md')


if __name__ == '__main__':
    main()
