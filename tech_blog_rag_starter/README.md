# 국내 기업 기술 블로그 RAG 수집기 — 1차 60건

## 대상과 근거
- 컬리 (20건): https://helloworld.kurly.com/
- 토스 (20건): https://toss.tech/
- 카카오 (20건): https://tech.kakao.com/ — 우아한형제들 대체
- ~~우아한형제들~~: https://techblog.woowahan.com/ — 2026-11-11 Cloudflare가 수집기 요청에 403을 반환해 제외(`enabled: false`). 우회하지 않음.
- 목록 출처: https://github.com/mabyoungg/awesome-korean-tech-blogs

이 저장소는 **블로그 URL의 모음집**이며, 게시글 본문 데이터셋은 아닙니다.

## 실행 (Windows PowerShell / Python 3.11+)
```powershell
cd tech_blog_rag_starter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/collect.py                  # URL 후보 확인만
python scripts/collect.py --company kurly --collect --limit 3  # 3건 시험
python scripts/collect.py --collect        # 기업별 최대 20건
python scripts/validate.py                # 파일 검증 + data/reports/collection_report.md 생성
python scripts/validate.py --check-urls   # 출처 URL도 다시 요청해 200 확인
python -m pytest tests -q                 # 오프라인 단위 테스트 (네트워크 없음)
```

Linux/macOS에서는 `source .venv/bin/activate`를 사용합니다. 초기 탐색·수집에 시간이 걸릴 수 있습니다.

## 주요 산출물
- `data/reports/<company>_candidates.csv`: 후보 URL 리스트 (수작업 검수 가능)
- `data/reports/<company>_rejected.json`: 제외 이유
- `data/reports/summary.json`: 수집 목표 대비 결과
- `data/raw/<company>.jsonl`: 수집한 본문 + 원문 URL + 메타데이터

## 수집 방식 (2026-11-11 수정)
- **TLS**: `truststore`로 운영체제 인증서 저장소를 씁니다(검증은 켠 채). 백신(Avast 등)이 HTTPS를 검사하며 자체 루트 인증서로
  다시 서명하면 Python 기본 `certifi` 번들로는 모든 요청이 `CERTIFICATE_VERIFY_FAILED`가 되어 `robots_unavailable`로 끝났습니다.
- **인코딩**: 응답에 charset이 없으면 UTF-8로 읽습니다(컬리 페이지가 ISO-8859-1로 읽혀 글자가 깨지던 문제).
- **robots.txt**: 2xx는 규칙대로 따르고, 404/410(파일 없음)은 RFC 9309에 따라 제한 없음으로 보되 `absent(404)`로 기록합니다.
  네트워크·TLS 오류, 401/403/429/5xx는 정책을 확인하지 못한 것이므로 수집하지 않습니다(fail closed).
- **탐색 순서**: 공식 RSS/Atom → 설정의 공개 목록 페이지(`listing_urls`, `?page={n}`) → sitemap. 회사별 `article_path` 정규식에 맞는 글 URL만 후보.
  - 우아한형제들: RSS(`/feed/?paged=1~3`) + `post-sitemap.xml`
  - 컬리: `sitemap-index.xml` → `sitemap-0.xml` (RSS·`/sitemap.xml`은 403)
  - 카카오: robots.txt 전체 허용 + `sitemap.xml`(`/posts/N`). RSS는 요약만 있고 본문은 스크립트로 그려지므로,
    페이지 HTML에 함께 실린 글 데이터(`__NUXT_DATA__`)에서 본문을 읽음(`embedded_data: nuxt`). `events` 카테고리·영어판(`languages: [ko]`) 제외
  - 토스: robots.txt·sitemap 없음 → 공식 `rss.xml` + 공개 `/category/engineering?page=N`만 사용, 엔지니어링 목록에 있는 글만(`require_listing`)
- **본문**: trafilatura(정밀 모드). 피드 본문이 페이지 추출보다 10% 넘게 길면 피드 본문 사용. 끝부분 채용·구독 안내 줄, 같은 블로그 글의 30% 이상에
  반복되는 15자 이상 줄(메뉴·푸터·공유 문구)은 제거. 기술 키워드가 `min_tech_keywords`(4)개 미만인 글은 `not_technical`로 제외.
- **요청 예절**: 요청 간격 2초(우아한형제들 5초), 타임아웃 20초, 일시 오류(타임아웃·429·5xx)는 최대 2회 재시도 후 포기. 차단(403)은 우회하지 않습니다.
- **회사 구분**: 회사마다 파일 하나(`data/raw/<company_id>.jsonl`). 검증 시 `company_id`=파일명, `company_name`=설정값,
  `source_url` 호스트=그 회사 블로그인지 확인해 RAG 검색에서 타사 문서가 섞이지 않게 함.
- **재실행**: 기존 JSONL을 읽어 같은 URL(정규화)·같은 본문 해시는 다시 저장하지 않습니다.

## 수집 원칙과 주의사항
1. 이 파일에는 **실제 60개 문서가 포함되어 있지 않습니다**. 본문은 사용자의 실행 환경에서 직접 가져옵니다.
2. `robots.txt`를 읽을 수 없으면 해당 사이트 수집을 하지 않습니다. 이용약관과 저작권 정책도 별도로 확인해야 합니다. robots.txt 허용 자체가 법적 이용 허가는 아닙니다.
3. 기본 요청 간 간격 2초, 429/503 응답 시 즉시 중단/건너뛰기, 타 도메인 리다이렉트 차단.
4. RSS/사이트맵과 공개 목록 HTML 링크에 기반한 범용 수집기입니다. JS로만 목록을 만드는 사이트에서는 후보를 충분히 못 찾을 수 있으므로 사이트별 어댑터가 추가로 필요할 수 있습니다.
5. `--collect` 없이 실행하면 후보만 출력합니다. 단, 후보 발견을 위해 공개 페이지와 sitemap에 HTTP 요청은 합니다.
6. 후보 랭킹은 키워드 기반 휴리스틱입니다. 기술 내용이 풍부하지 않은 글이 들어갈 수 있어 **60건 품질 검수**가 필요합니다.
7. 사이트가 차단하면 우회하지 않습니다. 수동 승인 URL 목록 또는 허가받은 콘텐츠로 교체합니다.
8. 20개가 부족한 회사는 부족 건수를 보고하며, 타 회사 글로 보충하지 않습니다.
9. 원문을 외부에 재배포하거나 생성 결과에 통째로 인용하지 않습니다. 원문 URL과 출처 표기를 유지합니다.

## 다음 단계: 기존 PostgreSQL / pgvector 연동
- 기존 스키마 파악 후 `company_id` 매핑 및 `source_url` 유니크 처리.
- 500~1000 토큰 청크, 100~150 토큰 overlap부터 실험(실제 토크나이저 적용).
- `bge-m3` 등 로컬 임베딩 모델 사용 및 vector dimension 확인.
- `company_id` 필터 → pgvector 검색 → 근거 원문/청크 ID 반환.
- 질의 평가셋(회사별 10질문), Hit@5, 출처 정확성, 타 기업 문서 혼입 0건 확인.

## JSONL 예시 필드
`document_id`, `company_id`, `company_name`, `document_type`(`tech_blog`), `title`, `source_url`, `published_at`(확인 못 하면 null), `collected_at`, `language`, `is_synthetic`(false), `extraction_method`(html/feed), `tech_keyword_score`, `content_length`, `content_sha256`, `content`.

## 알려진 한계
- `robots.txt`는 받아오지만 사이트별 별도 이용약관의 자동 해석은 하지 않습니다.
- URL 발견에 성공해도 짧거나 JavaScript 렌더링이 필요한 본문은 제외될 수 있습니다.
- 중복 URL/본문 해시는 검사하지만 의미상 중복 여부는 별도 검토가 필요합니다.
- 수집은 완료를 보장하지 않습니다. 보고서의 `target_met` 여부를 확인해야 합니다.
