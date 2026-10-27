# TRD 07 — 보안

- 작성: 에이전트 E(보안·품질 검증). 상태: **확정(1라운드 통합 반영)**. 날짜: 2026-10-27.
- 권위 순서: `docs/design/consolidation-round1.md`(C1~C8, T1~T27) > 이 문서 > `docs/design/proposals/agent-E-security-qa.md`(상세 위협 표·근거) > 다른 설계안.
- 관련 ADR: ADR-0007(코드 샌드박스), ADR-0012(인증·세션·격리), ADR-0008(인용), ADR-0009(실시간 면접), ADR-0010(배포·운영 Docker 보안 모드), ADR-0011(API 규약).
- 표기: **[구현]** = 이미 코드에 있음(`backend/app/...`), **[미구현]** = 이 문서가 정한 값을 앞으로 구현, **[잠정]** = 팀·법률 확인 대기(OQ), **(원문 외)** = PRD에 없는 보안 통제. PRD 기능 ID·수치·AC는 바꾸지 않는다.
- 검증은 `docs/trd/08-testing-and-operations.md`의 테스트 ID(T-…, AC-…, SBX-…, QG-…)로 한다. 이 문서의 값이 코드와 다르면 14절 "구현과의 차이"에 적고 코드는 고치지 않는다.

## 1. 원칙

1. **서버가 강제한다**(SEC-03): 역할·회사·소유자·상태·시간 판정은 서버만 한다. 화면 숨김은 안내일 뿐이다.
2. **기본은 닫힘(fail-closed)**: 설정이 위험하면 기동하지 않고, 샌드박스가 검증되지 않으면 실행하지 않는다.
3. **최소 권한**: `ai`·`runner` 프로세스는 DB 자격 증명이 없다(T1). Docker 소켓은 `runner`만 가진다.
4. **LLM·STT 출력도 신뢰하지 않는다**: 이력서·답변·코드와 같은 등급의 비신뢰 데이터로 다룬다.
5. **PRD 고정값은 코드 상수**(`app/core/constants.py` [구현]), 운영 값은 설정(`app/core/config.py` [구현]).

## 2. 위협 모델 요약

전체 위협 표(TH-01~33)와 행위자·자산 상세는 설계안 `agent-E-security-qa.md` 1.3~1.5절과 3.1절을 따른다. 여기서는 통합 후 구성(T1의 7개 프로세스)에 맞춘 신뢰 경계와 출시 차단 위협만 둔다.

| 경계 | 양쪽 | 통제 요약 |
|---|---|---|
| TB-1 | 브라우저 ↔ `caddy`/`app` | HTTPS, 쿠키 세션, CSRF 토큰 + Origin, 보안 헤더, WebRTC(DTLS-SRTP, 시그널링은 HTTPS) |
| TB-2 | `app`·`worker` ↔ `postgres` | 앱 롤은 테이블 소유자 아님, 불변 트리거, 회사 범위 쿼리 |
| TB-3 | `app`·`worker` ↔ `ai`·`ollama` | 사설·루프백 주소만(허용목록), `ai`는 DB 없음, 출력은 스키마·원문 대조 |
| TB-4 | `app`·`worker` ↔ `runner` | 루프백·내부망 + `X-Runner-Token`, 계약은 `{case_id, stdin}`만 |
| TB-5 | `runner` ↔ Docker 데몬 | 소켓은 `runner`만, 생성 직후 inspect 검증 |
| TB-6 | 샌드박스 컨테이너 내부 | 임의 코드. 3.4 강화 옵션 + 운영 강화 런타임(13절) |
| TB-7 | 서버 ↔ 외부 인터넷 | 운영은 이그레스 차단. 외부 AI API·공용 STUN 금지 |
| TB-8 | 운영자 CLI·DB 관리자 ↔ DB | CLI는 답변 원문 출력 없음, 모든 명령 감사 기록 |

| ID | 출시 차단(H) 위협 | 통제 절 | 대표 검증 |
|---|---|---|---|
| TH-01·02 | 다른 회사·다른 지원자 리소스 ID 직접 접근(IDOR) | 5 | AC-03, IDOR 매트릭스 |
| TH-04 | 지원자 응답에 총점·인용·메모·가중치·커스텀 질문 노출 | 5.6 | AC-19, QG-02 |
| TH-09 | 이력서·답변·LLM 출력·실행 출력을 통한 담당자 화면 XSS | 7, 4 | QG-11, XSS 픽스처 E2E |
| TH-12~14 | 샌드박스 탈출·네트워크 접근·자원 고갈 | 13 | SBX-01~14 |
| TH-15 | 비공개 테스트 유출(채점 경계, 문제 API, 코드 리뷰 프롬프트) | 13.1, 11 | SBX-11, AC-15, QG-14 |
| TH-16 | Docker 소켓 권한 집중 | 13.1 | 배포 검사(소켓 소유) |
| TH-21 | 인용 위조·원문에 없는 근거 | 11 | AC-16 |
| TH-24 | 외부 AI API·공용 STUN·CDN으로 데이터 유출 | 12 | AC-22, QG-06 |
| TH-32 | 평가 작업 중복으로 점수 이중 기록 | ADR-0006 | QG-04 |
| TH-33 | 질문은행을 통한 회사·지원자 정보 공유 | 11 | C2 DB CHECK 시험 |

## 3. 인증·세션·CSRF·쿠키 (ADR-0012)

### 3.1 비밀번호 (SEC-05)

| 항목 | 값 | 상태 |
|---|---|---|
| 알고리즘 | Argon2id, argon2-cffi 기본 = RFC 9106 저메모리(m=64 MiB, t=3, p=4, salt 16B, 출력 32B) | [구현] `auth/passwords.py` |
| 동시 해시 | 세마포어 4(메모리 상한 약 256 MiB, LLM·STT와 RAM 공유) | [구현] |
| 이벤트 루프 | `asyncio.to_thread`로 스레드풀에서 계산 | [구현] |
| 형식 | 8~128자, 비밀번호 = 이메일 금지, 조합 규칙 강제 없음(원문 외, USR-01 "형식 검사"의 해석) | [구현] `auth/router.py` |
| 재해시 | 로그인 성공 시 `check_needs_rehash`면 새 파라미터로 갱신 | [미구현] |
| 1회 검증 시간 | 이 장비에서 실측해 0.15~0.5초 밖이면 파라미터 조정(하한 OWASP m=19 MiB, t=2, p=1) | 실측 대기(08 8절) |

### 3.2 로그인과 계정 존재 비노출 (USR-01, USR-04)

- 실패 사유(없는 계정·비밀번호 불일치·역할 불일치·비활성)를 구분하지 않는다: 401, 본문 코드 `AUTH_FAILED`, 문구 "이메일 또는 비밀번호가 올바르지 않습니다." [구현]
- 없는 계정도 같은 파라미터의 더미 해시로 검증하고, 역할·상태 비교는 해시 검증 뒤에 한다 [구현]. 응답 시간 차이는 QG-10 타이밍 시험으로 확인한다.
- 로그인 요청의 역할은 `CANDIDATE`·`RECRUITER`만 허용한다. `OPERATOR` 행은 웹으로 로그인할 수 없다 [구현].
- 요청 본문은 JSON 스키마(`extra="forbid"`)만 받는다. 폼 인코딩 교차 사이트 POST는 422로 끝난다 [구현].

**무차별 대입 제한** (원문 외, SC-04)

| 키 | 창 | 상한 | 초과 시 | 상태 |
|---|---|---|---|---|
| `e:` + HMAC(정규화 이메일) | 15분 | 실패 5회 | 15분 동안 429 `TOO_MANY_ATTEMPTS` | [구현] `auth/throttle.py` |
| `i:` + 클라이언트 IP | 15분 | 실패 30회 | 15분 동안 429 | [구현] |

- 존재하지 않는 이메일에도 같은 카운터를 쓰므로 제한 동작으로 존재가 드러나지 않는다 [구현]. 성공 시 이메일 키만 지운다.
- 운영에서 클라이언트 IP는 리버스 프록시가 넘긴 값이어야 한다(uvicorn `--proxy-headers --forwarded-allow-ips=127.0.0.1`). 그렇지 않으면 모든 사용자가 같은 IP 키를 써서 30회 실패로 전체 로그인이 막힌다(14절 G-05).
- HMAC 키는 코드 상수가 아니라 비밀 설정값이어야 한다(14절 G-04).

**가입 중복 이메일** [잠정, OQ-34]: 409 `EMAIL_UNAVAILABLE` "사용할 수 없는 이메일입니다."[구현]. 가입은 중복을 알릴 수밖에 없으므로 IP당 1시간 10회(제안) 속도 제한으로 완화하고 잔여 위험으로 기록한다(속도 제한은 [미구현], 14절 G-06).

### 3.3 세션 (USR-06)

| 항목 | 값 | 상태 |
|---|---|---|
| 토큰 | `secrets.token_urlsafe(32)`(256비트). DB에는 SHA-256만(`sessions.token_hash`) | [구현] |
| 고정 공격 | 로그인 성공마다 새 토큰 발급, 쿠키의 기존 값은 쓰지 않음 | [구현] |
| 유휴 만료 | 마지막 요청 후 2시간(`SESSION_IDLE_SECONDS`), 60초 안의 재갱신은 생략 | [구현] `auth/sessions.py` |
| 절대 수명 | 두지 않음(PRD는 유휴 2시간만 규정, E의 12시간 제안은 미채택) | 결정 |
| 만료·비활성 | 조회 시 만료되었거나 사용자 상태가 ACTIVE가 아니면 행 삭제 후 401 | [구현] |
| 로그아웃 | 서버 행 삭제 + 쿠키 삭제, CSRF 필요 | [구현] |
| 전체 무효화 | 비밀번호 변경·탈퇴(USR-07)·담당자 비활성(CLI) 시 `revoke_all` | 함수 [구현], 호출부 [미구현] |
| 정리 | `session_gc` 주기 작업 1시간 | 시드 [구현] |
| 세션 행 내용 | user_id, role, company_id(로그인 시 복사), csrf_token, created/last_seen/expires | [구현] |

### 3.4 CSRF (ADR-0012)

- **동기화 토큰**: 세션마다 `secrets.token_urlsafe(32)`, 로그인 응답과 `GET /api/auth/me` 응답 본문 `csrf_token`으로 전달 [구현]. SPA는 POST·PUT·PATCH·DELETE에 `X-CSRF-Token`을 붙인다. 불일치는 403 `CSRF_REJECTED` [구현].
- **Origin 검사**: 상태 변경 요청에 `Origin`이 있으면 `trusted_origins`에 있어야 한다. 아니면 403 `ORIGIN_REJECTED` [구현]. 브라우저는 상태 변경 요청에 Origin을 보내므로 Origin이 없는 요청(비브라우저)은 통과시키되 인증된 요청은 토큰이 여전히 필요하다. 로그인·가입은 토큰 없이 Origin 검사만 한다 [구현].
- **WebRTC 시그널링**은 `app` 프로세스 안의 같은 의존성(`get_principal`)을 쓴다(T1). 교차 사이트 요청 하나로 진행 중 연결을 끊는 공격(D-06)을 막는다.
- 토큰 비교는 상수 시간(`hmac.compare_digest`)으로 한다(14절 G-03).
- 오류 코드 이름은 ADR-0011 오류 레지스트리가 최종 소유한다(리뷰에서 `CSRF_FAILED` 제안, 구현은 `CSRF_REJECTED`).

### 3.5 쿠키

| 속성 | 운영 | 개발(`http://localhost`) | 상태 |
|---|---|---|---|
| 이름 | `__Host-aihire_sid` | `aihire_sid` | 구현은 `aihire_sid` 고정(14절 G-02) |
| Secure | 필수(운영 기동 검사) | 꺼짐 허용 | [구현] |
| HttpOnly | 켬 | 켬 | [구현] |
| SameSite | Strict | Strict | [구현] |
| Path / Domain | `/` / 지정 안 함 | 같음 | [구현] |
| 만료 | 지정 안 함(브라우저 세션 쿠키), 유휴 판정은 서버 | 같음 | [구현] |

## 4. 보안 HTTP 헤더·CORS·TLS

| 헤더 | API 응답(`app`) | SPA HTML·정적 자산(`caddy`) | 원본 PDF 응답(T26) |
|---|---|---|---|
| Content-Security-Policy | `default-src 'none'; frame-ancestors 'none'` [구현] | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-src 'none'; frame-ancestors 'none'` | `sandbox` (+ `default-src 'none'`는 내장 뷰어 실측 후) |
| Permissions-Policy | `microphone=(self), camera=(self), display-capture=(), geolocation=(), payment=(), usb=()` [구현] | 같은 값 | 같은 값 |
| Strict-Transport-Security | — | `max-age=31536000`(HTTPS 운영만, includeSubDomains·preload는 사내 도메인 정책 확인 후) | caddy가 붙임 |
| X-Content-Type-Options | `nosniff` [구현] | `nosniff` | `nosniff` |
| Referrer-Policy | `no-referrer` [구현] | `no-referrer` | `no-referrer` |
| X-Frame-Options | `DENY` [구현] | `DENY` | `DENY` |
| Cross-Origin-Opener-Policy / -Resource-Policy | `same-origin` / `same-origin` [구현] | 같음 | 같음 |
| Cache-Control | `no-store` [구현] | `index.html` `no-cache`, 해시 자산 `immutable` | `no-store` |
| Content-Disposition | — | — | `inline; filename="resume.pdf"`(원본 파일명 미사용), 새 탭으로 열기 |

- `style-src`: CodeMirror 6·대화상자의 런타임 스타일이 막히면 편집기가 깨진다(D-12). 운영 빌드로 전 화면을 돌며 `securitypolicyviolation` 0건이면 위 값 유지, 위반이 있으면 서버가 `index.html`에 nonce를 넣고 `EditorView.cspNonce`를 쓴다. nonce 주입이 불가하면 `style-src 'self' 'unsafe-inline'`만 예외로 허용하고 기록한다(스크립트는 엄격 유지).
- PDF 응답의 `sandbox` CSP가 크롬·엣지 내장 PDF 뷰어를 막는지 실측한다(T-RES-08-view). 막히면 `Content-Disposition: attachment`로 대체하고 ADR-0011을 갱신한다.
- CSP는 `RTCPeerConnection`의 연결 대상을 제한하지 못한다. 그래서 ICE 서버 허용목록(8절)이 따로 필요하다.
- **CORS**: 허용하지 않는다. SPA와 API는 같은 출처(caddy)다. 개발은 Vite 프록시로 같은 출처를 만든다. CORS 미들웨어를 넣지 않는다.
- **Host 검사**: `TrustedHostMiddleware`로 허용 호스트만 받는다(DNS 리바인딩 방어) [미구현].
- **TLS**: caddy가 종단한다(사내 CA 인증서 또는 `tls internal`), TLS 1.2 이상, 80→443 리다이렉트. 개발은 `localhost` 보안 컨텍스트 예외(R-09).
- **요청 ID**: 클라이언트 `X-Request-ID`가 영숫자 64자 이하면 쓰고 아니면 새로 만든다 [구현].
- **운영 표면**(T25): 운영 프로필은 OpenAPI 문서(`/api/docs`, `/api/openapi.json`)를 만들지 않는다 [구현] `main.py`. 테스트 훅(`/api/test/*`, 가짜 시계, 상한 변경)은 운영 라우트 목록에 존재하지 않아야 한다(QG-01).

## 5. 인가·회사 격리 (SEC-03, ADR-0012)

### 5.1 역할 가드

- 비로그인 401 `UNAUTHENTICATED`, 역할 불일치 403 `ROLE_FORBIDDEN` [구현] `auth/deps.py`. 담당자가 지원자 기능을 쓰면 "담당자 계정으로는 지원자 기능을 사용할 수 없습니다."(USR-05).
- 담당자도 공개(OPEN) 공고 목록·상세 GET은 200(`PublicPosting`만), `POST .../applications`는 403(T21, AC-02).
- 담당자 컨텍스트의 `company_id`는 세션 값만 쓴다. 본문·쿼리의 company_id·candidate_id·role·score 류 값은 무시하고 스키마에서 거부한다 [구현 원칙].

### 5.2 라우트 정책 선언 [미구현]

모든 라우트는 정책 메타데이터를 갖는다: `role ∈ {public, candidate, recruiter, any_user, internal, test}`, `scope ∈ {none, owner, company}`, `resource`. 선언은 의존성(`require_candidate`/`require_recruiter`)과 함께 `openapi_extra={"x-policy": {...}}`로 단다. 라우트 인벤토리 테스트(QG-01)가 `app.routes`를 순회해 정책 없는 라우트·운영 프로필의 `test` 라우트가 있으면 실패시키고, IDOR 매트릭스(08 6절)의 기대값을 이 메타데이터에서 계산한다.

### 5.3 범위 헬퍼와 404 동일성

- 담당자 리소스: `get_for_company(Model, id, ctx.company_id)` / `select_for_company(Model, ctx)`, 지원자 리소스: `get_for_owner(Model, id, ctx.user_id)` [미구현]. 조회 한 번에 범위 조건을 넣는다(가져온 뒤 비교하지 않음 → 응답 시간 차이도 줄임).
- 없는 ID, 다른 회사, 다른 소유자는 모두 `errors.not_found()`의 같은 404 본문(`NOT_FOUND`, "요청한 정보를 찾을 수 없습니다.") [구현]. 상태 코드·본문·헤더가 같아야 하고, 거부된 변경 요청은 DB를 바꾸지 않는다.
- 하위 리소스 ID만 받는 경로(메모 `PATCH /api/recruiter/notes/{id}`, 이력서 추출 결과, 면접 turn 명령, 질문 음성)는 부모의 company_id·owner를 조인해 검사한다.
- 안전망: 회사 소유 테이블은 복합 FK `(id, company_id)`로 자식과 부모의 회사가 어긋날 수 없다 [구현] 마이그레이션. `CompanyScoped` 모델에 `with_loader_criteria` 자동 조건, 담당자 모듈의 원시 SQL(`text(`) 금지 정적 검사 [미구현].

### 5.4 DB 권한 [미구현]

| 롤 | 권한 | 비고 |
|---|---|---|
| `aihire_owner` | 스키마 소유, 마이그레이션 전용(Alembic) | 앱 프로세스는 이 자격 증명을 갖지 않음 |
| `aihire_app` | `app` 프로세스: DML. `coding_testcases`는 `PUBLIC` 행만 보이는 뷰로만 SELECT. `applications.decision_status` UPDATE 가능 | 테이블 소유자 아님 → TRUNCATE·트리거 비활성화 불가 |
| `aihire_worker` | 작업 큐·평가·채점·보관 삭제 DML, 비공개 테스트 SELECT. `decision_status` 칼럼 권한 없음(P-8) | |
| (없음) | `ai`, `runner` | DB 자격 증명 자체가 없음(T1) |

- `audit_logs`·`decision_history`·`consent_records`: 앱 롤 UPDATE·DELETE·TRUNCATE 회수 + 우회 없는 불변 트리거(`forbid_mutation_strict` [구현]). 내용 테이블의 `app.purge` 우회는 보관·탈퇴 삭제에만(T14) [구현].
- 개발 compose의 `aihire` 사용자는 `POSTGRES_USER`(superuser)다. 개발에서도 권한 테스트는 별도 롤로 돌린다(14절 G-09).

### 5.5 RLS

P0에서는 쓰지 않는다. `applications`·`recruiter_reports`·`recruiter_notes` 3개 테이블 시범 적용은 P1 선택 항목이다(ADR-0012).

### 5.6 지원자 응답 범위 (SEC-04, 6.6, AC-19)

- 지원자 응답은 `app/schemas/candidate/*`의 화이트리스트 스키마만 쓴다(`extra="forbid"`). 결과는 `candidate_feedback`(총점·인용·가중치 칼럼 없음)과 결정 상태만 읽고, 결정 전에는 `feedback` 키 자체가 없다.
- 공고 응답은 `PublicPosting`(제목·회사명·직무·요건·우대·업무·정원·마감·`coding_enabled`·`coding_time_limit_min`)만. `current_settings_id`(가중치·커스텀 질문)와 문제 ID는 넣지 않는다(D-08).
- 데이터채널 메시지도 키 허용목록을 갖는다. `question`은 `turn_id, no, followup, text, limit_s` 등만이고 꼬리질문 판단 사유·근거·출처는 넣지 않는다. `debug` 류 메시지는 운영 프로필에서 송신 시 예외(D-09).
- 검사 범위(QG-02)는 "지원자 세션이 받는 모든 HTTP 응답(오류 포함) + 데이터채널 메시지"다.

## 6. 파일 업로드·PDF 파싱 격리 (RES-02~04, T22)

| 단계 | 값 | 상태 |
|---|---|---|
| 크기 | 10 MiB = 10,485,760바이트(`RESUME_MAX_BYTES`, DB CHECK). caddy `request_body max_size 11MB` + 앱이 스트림으로 세다 초과 즉시 중단 → 413 `FILE_TOO_LARGE` | 상수·CHECK [구현], 나머지 [미구현] |
| 형식 | 확장자 `.pdf` AND 첫 1024바이트 안 `%PDF-` AND 실제 파싱 성공. 선언 Content-Type은 무시. 실패 415 `NOT_PDF` / 422 `PDF_UNREADABLE` | [구현] |
| 저장 | 서버 생성 UUID `storage_key`, `data_dir` 아래 웹 루트 밖, 파일 권한 0600, 실행 권한 없음. 원본 파일명은 저장 경로에 쓰지 않음. SHA-256 기록 | 스키마 [구현] |
| 파싱 위치 | 업로드 요청을 받은 프로세스가 `app/resumes/pdf_extract.py`를 **별도 인터프리터 하위 프로세스**(`python -I`)로 실행하고 JSON 한 줄만 받는다. 파서 예외·무한 루프·크래시가 API 프로세스로 번지지 않는다 | [구현] `resumes/router.py` |
| 파싱 상한 | 벽시계 20초(T22, 초과 시 kill), 50쪽(T22), 추출 텍스트 200,000자, 암호화 PDF 거부 [구현]. Linux 운영은 자식에 `RLIMIT_AS` 512 MiB 추가, Windows 개발은 시간 상한만 보장 | 메모리 상한 [미구현] |
| 라이브러리 | `pypdf`(BSD, 순수 Python) — 메모리 손상 계열 취약점 회피 | [구현] 의존성 |
| 스캔 PDF | 추출 글자 수 임계값 미만 → `NO_TEXT` → "직접 입력" 안내(RES-04). OCR 흉내 금지 | [미구현] |
| 구조화 | LLM 추출(T1) 값은 원문 부분 문자열 검증(ADR-0008 규칙)을 통과한 것만 채움(RES-03) | [미구현] |
| 속도 | 사용자당 동시 1건, 분당 5건(제안) | [미구현] |
| 제공 | 권한 API로만(정적 경로 금지), 4절 PDF 헤더, 감사 `RESUME_FILE_VIEW` | [미구현] |

## 7. XSS·출력 렌더링

- React 기본 이스케이프만 쓴다. `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function` 금지(ESLint `react/no-danger`·`no-eval`, semgrep).
- 일반 텍스트로만 그리는 값: 이력서 표준 항목, STT 확정 텍스트·자막, 텍스트 답변, LLM 출력(코멘트·총평·강점·개선점·지원자 피드백·꼬리질문·코드 리뷰), 제출 코드, [실행] 출력, 파일명, 코딩 문제 설명(`statement_md`도 HTML로 해석하지 않음), 동의 문구.
- 이력서 속 URL은 링크로 만들지 않는다(필요하면 `http`/`https`만).
- 인용 강조는 서버가 준 원문 범위(`quote_start`, `quote_end`)로만 한다. 클라이언트가 문자열을 다시 찾거나 HTML을 조립하지 않는다(ADR-0008).
- 제출 코드는 읽기 전용 CodeMirror 또는 `<pre>`. [실행] 결과는 stdout 64 KiB·stderr 16 KiB까지 응답, DB에는 앞 4 KiB만(`coding_runs.stdout_head`).
- 응답 JSON은 `application/json`, 사용자 입력을 HTML 응답에 반사하는 서버 렌더링 경로를 두지 않는다. 422 오류는 칸 위치·유형만 돌려주고 입력값을 되풀이하지 않는다 [구현] `core/errors.py`.

## 8. 실시간 면접 통제 (ADR-0009)

| 통제 | 확정값 | 근거 |
|---|---|---|
| 시그널링 | `POST /rtc/v1/sessions {application_id, purpose: precheck\|interview, sdp}`, `app` 프로세스, 쿠키 세션 + CSRF + Origin, 소유자 아니면 404 | D-04, D-06, T1 |
| 점검 연결 | `precheck`는 PREPARING만 요구(슬롯 불필요), 서버 전체 점검 연결 상한 4 | C4 |
| 면접 연결 | `interview`는 INTERVIEWING + 슬롯 보유. 지원 건당 활성 연결 1개, 새 연결 수락 시 이전 연결에 `session_superseded` 후 종료 | C4, SC-41 |
| ICE | 서버 `RTCConfiguration(iceServers=[])` 필수(실측: 기본값은 Google STUN, 첫 프레임 10.2초 → 빈 목록 0.17초). 브라우저는 `GET /api/system/rtc-config`(기본 `[]`). 설정 검증기가 공용 STUN·TURN 호스트를 거부. 원격 후보 주소는 사설·루프백만 수락(원문 외) | AC-22, R-C8 |
| 영상 | offer에 `m=video`가 있으면 거부. 카메라는 로컬 미리보기만, `MediaRecorder` 금지 | INT-08, 8.5 |
| 질문 음성 | 서버 WebRTC 하향 오디오 트랙(T7). 서버가 송출 종료 시각을 안다 | AC-09 |
| turn_id | 문항 제시 1회 = `interview_turns` 행 [구현 스키마]. 클라이언트 명령은 HTTPS `POST .../turns/{turn_id}/done` 등 한 경로, CSRF + 멱등 키. `turn_id`가 현재 것이 아니면 409 `STALE_TURN`. 확정은 음성 런타임이 한 번만(`interview_answers.turn_id UNIQUE`) | C3 |
| 서버 시계 | `opened_at = tts_ended_at + 0.3초`(`tts_playout_guard_seconds`), `deadline_at = opened_at + 60초(꼬리 45초)`, 경고 `deadline − 10초`, 네트워크 유예 **1.0초**(T9). 유예 뒤 도착한 오디오·명령 무시. 클라이언트 시각은 기록만 | INT-06, X-02 |
| 게이트 | `opened_at` 이전·`deadline + 유예` 이후 프레임은 서버가 폐기(실측: 게이트 이전 5.46초 발화 폐기). 클라이언트 마이크 off는 보조 | AC-09 |
| 재출제 상한 | 문항당 재출제 2회(`presentation_no ≤ 3`, DB CHECK [구현]). 횟수는 리포트 표시 | T11 |
| 텍스트 모드 | **서버 판정**: 재출제 상한 초과 또는 서버가 20초 이상 살아 있는 연결을 못 본 상태에서 클라이언트가 요청할 때만. turn·답변에 `input_mode=TEXT`, 2,000자 상한, 같은 서버 마감, 리포트 표시 | T12, INT-18 |
| 하트비트·슬롯 | 슬롯은 INTERVIEWING만 점유(T10). 면접 중 하트비트는 음성 런타임, CODING은 코딩 API(임시저장·실행·명시 하트비트 30초). 코딩 무응답 120초 후에만 INTERRUPTED | C7 |
| 코딩 시간 | `coding_attempts.deadline_at`(서버), 임시저장은 마감 뒤 409 `TIME_OVER`, 마감 + 3초 뒤 worker가 마지막 저장본 자동 제출(COD-05), 코드 64 KiB(DB CHECK [구현]), 늦게 온 저장은 `client_seq`로 마지막 쓰기만 반영 | COD-05 |
| 1회 응시 | 상태 전이는 허용 전이 표 + 트리거(`guard_application` [구현]). SUBMITTED·EXPIRED에서 모든 응시 API 409 | INT-12 |

## 9. 개인정보 최소화와 보관 (8.5) — 기간·문구는 **법률 미결**(X-13, OQ-04)

- **받지 않음**: 생년월일·성별·사진 칼럼 없음(스키마 테스트), 가입 스키마 `extra="forbid"` [구현]. 카메라 영상은 서버로 보내지 않는다.
- **보관(설정값, [잠정])**: 답변 음성 30일(`retention_audio_days`), 텍스트·리포트·코드·이력서 365일(`retention_content_days`) [구현 설정]. 기산점은 첫 결정일 또는 공고 마감일(OQ-11 [잠정]). 감사 로그 365일(OQ-33 [잠정]).
- **삭제**: `retention_purge` 작업이 `app.purge`로 내용 테이블만 지운다(T14). 파일은 `file_deletions` outbox로 지운다. 탈퇴(USR-07)는 OQ-12 임시안대로 개인정보·내용을 지우고 결정 이력·동의 기록·감사 로그는 ID만 남긴다. 탈퇴 시 전 세션 무효화.
- **백업에 남는 삭제 데이터**: 백업 보관 기간 동안 남는다. 백업 보관 기간도 법률 확인 대상이다(08 10절).
- **브라우저**: 이력서·답변을 localStorage·sessionStorage에 쓰지 않는다. 개인정보를 URL 쿼리에 넣지 않는다.
- **평가 입력 비식별화**: 11절.

## 10. 비밀정보·로그·감사

### 10.1 비밀정보

- `.env`, `.env.*`는 커밋하지 않는다(`.gitignore` [구현]), `.env.example`만 가짜 값으로 커밋. 첫 원격 푸시 전에 gitleaks로 전체 이력을 검사한다(QG-08).
- 비밀은 `SecretStr`로 repr·로그에 나오지 않는다 [구현 `runner_token`].
- 운영 기동 검사(fail-closed) [구현 일부]: `cookie_secure=true`, `runner_token` 기본값 금지. 추가할 검사(14절 G-08): DB 비밀번호가 개발 기본값이 아님, `trusted_origins`가 모두 `https://`, throttle HMAC 키 설정됨, test 훅 비활성.
- 운영 비밀 전달은 compose `secrets`(파일, 권한 0400)로 한다(환경변수는 `docker inspect`로 보임, ADR-0010).
- 내부 서비스: `runner`는 `X-Runner-Token` + 루프백·내부망, `ai`·`ollama`는 루프백·내부망 바인딩(Ollama API는 인증이 없다), 리버스 프록시는 내부 경로를 노출하지 않는다.

### 10.2 앱 로그 (SEC-08) [구현] `core/logging.py`, `core/middleware.py`

- JSON 한 줄, **허용 키만** 출력: `request_id, user_id, role, company_id, application_id, posting_id, question_id, job_id, job_kind, lane, status, status_code, method, path, latency_ms, event, error_code, attempt, run_no, model, count, seq, reason, duration_ms`. 그 밖의 키는 버린다.
- 메시지는 이메일·전화번호 패턴을 마스킹하고, 예외는 타입 이름만 남긴다. `sqlalchemy.engine`·`aiortc`·`aioice`·`httpx`·`httpcore`는 WARNING 이상만.
- 접근 로그는 메서드·경로·상태·지연·사용자 ID만(쿼리 문자열·본문 없음). uvicorn 자체 접근 로그는 쿼리 문자열을 찍으므로 `--no-access-log`로 끈다(14절 G-13).
- `reason`·`event`·`error_code`에는 정해진 코드 값만 넣는다(자유 문장 금지).
- 금지: 답변·자막·이력서 본문, 제출 코드, 프롬프트·LLM 출력 본문, 이름·이메일·전화번호, 비밀번호·토큰. `ai`는 작업명·토큰 수·지연·버전만 기록. `OLLAMA_DEBUG` 끔.
- 검증: 카나리아 E2E(QG-07) — 이름·이메일·전화·이력서·답변·코드에 고유 문자열을 넣고 전체 흐름 뒤 `app`·`worker`·`ai`·`runner`·`caddy`·`ollama`·Docker 로그와 `audit_logs.detail`·`jobs.payload`에서 0건.

### 10.3 감사 로그 (SEC-08)

- 기록 함수 `app/audit.py` [구현]: `occurred_at, actor_id, actor_role, company_id, action, target_type, target_id, detail(jsonb), request_id`. ID와 상태 값만, 원문·연락처 없음.
- 이벤트: `REPORT_VIEW`(리포트 조회 시에만), `RESUME_FILE_VIEW`, `NOTE_CREATE/UPDATE/DELETE`, `DECISION_SET`, `CONSENT_RECORD`, `RE_EVALUATE`, `SETTINGS_CHANGE`, `POSTING_CLOSE`, `ACCOUNT_WITHDRAW`, `DATA_PURGE` + (원문 외) `SANDBOX_STATE_CHANGE`, `OPERATOR_CLI`(계정 발급·비활성·재처리).
- 무결성: 우회 없는 불변 트리거 [구현] + 앱 롤 UPDATE·DELETE·TRUNCATE 회수 + 앱 롤이 소유자가 아님 [미구현]. 해시 체인은 동시 기록 시 체인 분기 문제(B R-15)로 MVP에서 쓰지 않고, 대신 일일 작업이 전날 행 수·최대 ID·행 내용 SHA-256을 DB 밖 파일에 남긴다(원문 외, P1 선택). DB superuser의 변조는 막지 못하고 탐지만 한다.
- `detail` 키는 이벤트별 허용목록으로 검사한다.

## 11. AI 고유 위험 통제

| 위험 | 통제 | 근거·검증 |
|---|---|---|
| 프롬프트 인젝션(점수 조작) | 비신뢰 입력은 호출마다 nonce가 붙은 구분자로 감싼다. 모델 제어 토큰(`<\|im_start\|>`, `<think>` 등) 제거. 출력은 JSON 스키마(추가 필드 금지)로 항목별 1~5 정수·인용·코멘트만. 총점·가중치·합불 필드는 스키마에 없다(P-5). `injection_suspected`는 내부 기록만(C가 화면 표시 철회) | 인젝션 회귀 세트(원문 외, P1): 지시문 덧붙인 변형 쌍의 항목 평균 변화 ≤ 0.5, 극단 변화 0건 |
| 인용 위조(EVL-02, AC-16) | ADR-0008 규칙: NFKC + 공백·제로폭 전부 제거 + 따옴표 통일 + **앞뒤** 문장부호만 무시. 내부 문장부호·숫자는 일치해야 한다. 대상은 같은 답변 단위의 `eval_text`만(질문 문장·다른 문항 제외). 최소 8자(`quote_min_chars`). 저장·표시는 원문 범위 `quote_start/quote_end`와 그 원문 문자열(LLM 문자열 저장 금지, 스키마 주석 [구현]) | 실측(`results-quote-rules.txt`): 실제 인용 47/48 통과, 위조 7/7 거부. 문장부호 전부 제거 규칙은 위조 7/7 통과 |
| 합불 추천 문구(P-8) | 코멘트·총평·강점·개선점·지원자 피드백·꼬리질문·코드 리뷰에 추천 표현 검사기. 인용 필드는 제외. 적중 시 1회 재생성, 재적중 시 문장 삭제 + 기록 | 가짜 LLM 표 테스트 |
| 평가 입력 개인정보(EVL-07) | 평가 입력에서 이름·이메일·연락처 필드 제외. 답변 속 성명 → `[지원자]`, 연락처 → `[연락처]`, 나이 표현 → `[나이]`, 1인칭 성별 자기언급 → `[성별]`(`eval_text`). 사진은 입력에 없음 | 반사실 시험: 이름·성별 표현만 다른 답변이 비식별화 뒤 바이트 단위로 같은 프롬프트 |
| 내부 정보의 지원자 방향 유출 | 꼬리질문 입력 = 현재 질문·답변·같은 지원자 이력서 요약(성명·연락처 제외)만. 지원자 피드백 입력 = 강점·개선점 요지와 점수 구간만(총점·인용·가중치·메모 없음), 출력은 답변 원문과 15자 이상 연속 일치 금지. 코드 리뷰 입력에 비공개 테스트 없음 | 프롬프트 캡처 카나리아(QG-14) |
| 점수 계산 | 항목·총점은 서버 Decimal 계산(T18), LLM의 합계 값은 쓰지 않는다 | AC-17 |
| 질문은행 공유(C2) | `source=AI`는 `company_id` 필수(DB CHECK [구현]), 선택 쿼리는 `company_id IS NULL OR = 공고 회사`. 이력서·꼬리질문은 은행에 넣지 않음 | Y사 지원자 질문 세트에 X사 카나리아 0건 |
| 편향 | 시선·표정·목소리 미사용(8.6), 텍스트만 평가. 골든셋 Kappa ≥ 0.6(P1, OQ-13·OQ-35) | 08 8절 |

## 12. 외부 AI API 부재 검증 (SEC-06, AC-22)

1. **엔드포인트 허용목록** [구현] `core/config.py`: `ai_service_url`·`ollama_url`·`runner_url` 호스트는 `localhost`, 루프백, 사설 대역, 점 없는 compose 서비스 이름만. 점이 있는 이름은 기동 시 해석해 모두 사설 주소여야 한다. 위반 시 기동 실패. STT·TTS·임베딩은 `ai` 서비스를 거치므로 같은 검사에 들어간다. ICE 서버 설정도 같은 검증기로 검사한다 [미구현].
2. **의존성 거부목록**: openai, anthropic, google-generativeai, google-genai, cohere, mistralai, langchain-openai 등이 잠금 파일·`package-lock.json`에 있으면 실패.
3. **코드·번들 URL 검사**: 소스와 `frontend/dist`에서 `http(s)://`를 뽑아 허용목록(사설 주소, XML 네임스페이스 등) 밖이면 실패. 외부 CDN·웹폰트·Monaco CDN 로더 금지.
4. **오프라인 설정**: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, 라이브러리 텔레메트리 끄기 변수, Ollama 외부 pull 금지·모델 digest 고정, aiortc `iceServers=[]`.
5. **이그레스 차단 실행 시험**(최종 판정): `offline-test` 프로필(모든 네트워크 `internal: true` + 질의를 기록하는 내부 DNS)에서 전체 흐름을 돌리고 외부 이름 질의·외부 연결 시도 0건, ICE 수집 1초 미만. 브라우저 쪽 요청 차단(Playwright `route`)은 보조다.

## 13. 코드 샌드박스 (ADR-0007)

### 13.1 구조와 계약

- `runner`만 Docker 소켓을 갖는다. `runner`는 DB 자격 증명이 없다(T1). `app`·`worker` → `runner`는 루프백(개발)·내부망(운영) + `X-Runner-Token`.
- 계약(C8): runner 요청은 `{job_id, mode: run|grade, language: python, code(≤64 KiB), cases: [{case_id, stdin}], profile}`뿐이다. **기대 출력은 runner에도 보내지 않는다.** 응답은 `{case_id, status, exit_code, stdout(≤64 KiB), stderr(≤16 KiB), truncated, wall_ms}`와 `sandbox_fingerprint`. 기대 출력 비교는 `worker`가 runner 밖에서 한다.
- 테스트 1건 = 컨테이너 1회 실행, 실행마다 새 컨테이너, 종료 후 삭제(실측 기동 약 0.45초, 20건 약 9초).
- [실행](COD-03)의 입력은 서버가 고른 공개 예시뿐이다. 클라이언트는 코드만 보낸다.

### 13.2 컨테이너 옵션 (확정값)

| 옵션 | 값 | 실측 |
|---|---|---|
| `--network` | `none` | 공인 IP·`host.docker.internal` 차단 |
| `--read-only` | 켬 | EROFS |
| `--tmpfs` | `/tmp:rw,noexec,nosuid,nodev,size=16m` | noexec 차단 |
| `--cap-drop` | `ALL`(`--cap-add` 없음) | CapEff 0 |
| `--security-opt` | `no-new-privileges`, seccomp는 Docker 기본 프로필(`unconfined` 금지) | 미실측(SBX-02) |
| `--user` | `10001:10001`(이미지의 전용 계정, 셸 nologin) | uid 10001 |
| `--pids-limit` | `64` | 63에서 차단 |
| `--memory` / `--memory-swap` | `256m` / `256m`(스왑 없음) | OOM 137 |
| `--cpus` / `--cpuset-cpus` | `1` / 운영 설정의 전용 코어(STT·TTS·API와 분리) | — |
| `--ulimit` | `nofile=64:64`, `fsize=8388608:8388608`, `core=0`, `cpu=<제한초+1>` | nofile·fsize 실측 |
| `--ipc` | `none` | 실측 플래그 |
| `--log-driver` | `none`(출력 폭주가 Docker 로그 파일로 디스크를 채우지 않게) | 스파이크에 없음 → 추가 |
| 마운트·장치 | 없음(바인드·볼륨·`docker.sock`·`--device`·`--gpus` 없음) | — |
| 금지 | `--privileged`, `--pid=host`, `--network=host`, `--userns=host` | — |
| 환경변수 | 이미지 ENV + `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`. 비밀 없음 | 비밀 키워드 없음 실측 |
| 기타 | `--hostname sandbox`, `--rm`, `--label app=sandbox,job=<id>` | — |
| 이미지 | `python:3.12-slim` 다이제스트 고정, setuid 비트 제거, pip 미사용, 진입점 = 읽기 전용 런처. 운영 반입은 `docker save/load` | 스파이크 Dockerfile 기반 |

**코드 전달**: 바인드 마운트를 쓰지 않는다. 런처가 stdin에서 길이 접두 프레임으로 코드를 버퍼링 없이 읽어 `/tmp/main.py`에 쓰고 `python3 -I -B /tmp/main.py`로 exec하며, 남은 stdin을 프로그램 입력으로 넘긴다(스파이크 진입점 `python -I -B -`는 코드가 stdin을 차지해 테스트 입력을 줄 수 없음).

**출력 상한**: runner는 attach 스트림에서 stdout 64 KiB·stderr 16 KiB까지만 읽고 넘으면 즉시 kill한다(스파이크는 전부 읽은 뒤 잘랐다). 벽시계 제한은 runner가 재고 `docker kill` + 강제 삭제한다.

### 13.3 실행 프로필

| 항목 | [실행] `run-v1` | 채점 `grade-v1` |
|---|---|---|
| 입력 | 공개 예시 | 공개 + 비공개, 테스트 1건 = 컨테이너 1회 |
| 벽시계 | 10초 | 문제의 `time_limit_ms`(100~20,000, DB CHECK [구현]) |
| 메모리 | 256 MiB | 문제의 `memory_mb`(32~512) |
| 지원자 반환 | 정상/비정상 + 잘린 출력·오류. "정상" = 제한 안에서 종료 코드 0 | 없음(6.6: 결정 후 "정답을 모두 맞힘/맞히지 못함"만) |
| 동시성 | 지원 건당 진행 중 1건(DB 부분 유니크 [구현]), 지원자당 10분 20회(제안), 전역 2 | 채점 큐 전역 2 |
| 상태 값 | OK, RUNTIME_ERROR, TIMEOUT, MEMORY_LIMIT, OUTPUT_LIMIT, SANDBOX_ERROR([구현] CHECK) | 같음 |
| SANDBOX_ERROR | 지원자에게 "실행 환경 오류"만 | 0점이 아니다. 재시도 후에도 실패하면 채점 대기(6.5의 0점은 미작성·컴파일 실패만) |

### 13.4 자체 점검 스위트 SBX-01~14

점검 코드는 지원자 코드와 **같은 실행 함수·같은 프로필**로 돌린다. "실측" 칸은 Docker Desktop(WSL2)·runc 스파이크 12/12 결과가 덮는 범위다.

| ID | 점검 | 기대 결과 | 실측 |
|---|---|---|---|
| SBX-01 | 생성 직후 inspect(**매 실행 전**) | NetworkMode=none, ReadonlyRootfs, CapDrop=ALL, CapAdd 없음, no-new-privileges, Privileged=false, PidsLimit=64, Memory=MemorySwap, 마운트·장치 없음(tmpfs 제외), LogConfig=none, 운영은 승인 런타임 | 미실측 |
| SBX-02 | 프로세스 신원 | uid·gid 10001, CapEff=0, NoNewPrivs=1, Seccomp=2 | uid·CapEff만 |
| SBX-03 | 네트워크 | 인터페이스 `lo`뿐, 공인 IP·게이트웨이 IP·`host.docker.internal`·DB·Ollama 포트 연결 실패, DNS 실패 | 공인 IP, 호스트 이름(DNS 실패) |
| SBX-04 | 파일시스템 | `/`·`/usr`·`/etc` 쓰기 실패, `/tmp` 실행 실패, 8 MiB 미만 파일 여러 개로 16 MiB 초과 시 ENOSPC | 읽기 전용·noexec. 크기는 fsize(8 MiB)가 먼저 걸려 tmpfs 상한 자체는 미검증 |
| SBX-05 | 프로세스 폭증 | 상한에서 생성 실패, 벽시계 안 종료, 그동안 runner·app 헬스 정상 | 실측(63) |
| SBX-06 | 메모리 | 512 MiB 시도 → MEMORY_LIMIT, 호스트 스왑 증가 없음 | 실측(137) |
| SBX-07 | CPU·대기 | 무한 루프와 긴 `sleep` 각각 TIMEOUT | 루프만 |
| SBX-08 | 출력 폭주 | OUTPUT_LIMIT, runner RSS 증가 ≤ 상한+여유, Docker data-root 증가 ≈ 0 | 절단만 |
| SBX-09 | 호스트 정보 | env = 이미지 ENV 목록 + 주입분, 비밀 키워드 없음, `/proc/self/mountinfo`에 호스트 경로(`/mnt/host`, `/run/desktop`, 프로젝트 경로) 없음, `docker.sock` 없음 | env만 |
| SBX-10 | 권한 상승 호출 | mount, 사용자 네임스페이스 생성, 다른 프로세스 ptrace, keyctl 거부 | 미실측 |
| SBX-11 | 비공개 테스트 부재 | 채점 중 컨테이너 파일·환경·인자에서 다른 테스트 입력과 모든 기대 출력 카나리아 미발견, 하네스 조작 코드가 통과로 세지지 않음 | 미실측 |
| SBX-12 | 정리 | 실행 후 `app=sandbox` 라벨 컨테이너 0개 | 미실측 |
| SBX-13 | 동시 실행 | 상한+1 동시 요청 → 초과분 대기 또는 429, 동시 컨테이너 ≤ 상한 | 미실측 |
| SBX-14 | 정상 동작 | Hello World·stdin 에코·예외 코드가 OK·OK·RUNTIME_ERROR | Hello World만 |

### 13.5 기능 플래그와 지문 — "검증 전에는 실행 기능을 노출하지 않는다"

1. `AIHIRE_CODE_EXECUTION_ENABLED` 기본 `false` [구현] `core/config.py`.
2. 유효 상태 = 플래그 true **AND** runner의 최근 SBX-01~14 전부 통과 **AND** 지문 일치 **AND** 점검 후 24시간 이내 **AND**(운영) 승인 런타임.
3. 지문 = runner 버전 + 샌드박스 이미지 다이제스트 + Docker 엔진 버전 + 런타임 이름 + seccomp 프로필 해시 + 제한값 해시. 하나라도 바뀌면 재점검 전까지 비활성.
4. runner는 DB가 없으므로 점검 결과를 자기 상태(메모리 + 로컬 상태 파일)로 갖고 `GET /v1/health → {status: VERIFIED|UNVERIFIED|FAILED, fingerprint, checked_at, runtime}`로 알린다. `app`은 이를 주기 조회하고 상태가 바뀌면 감사 `SANDBOX_STATE_CHANGE`를 남긴다. 상세는 내부 전용, 지원자 점검 API에는 가능/불가만.
5. runner는 기동 시·매일·설정 변경 시 SBX를 돌린다. 실패하면 작업 수락을 거부한다. 매 실행 전 SBX-01이 실패하면 그 실행을 중단하고 FAILED로 바꾼다.
6. 비활성일 때(T13, OQ-32 [잠정]): 코딩 켠 공고의 **면접 시작 차단**(INT-14와 같은 위치·안내), [실행] 503(코드명은 ADR-0011 레지스트리, B 기준 `RUNNER_UNAVAILABLE`), 이미 제출된 코드는 채점 대기(0점 아님), 공고 편집은 경고만. 응시 도중 비활성되면 [실행]만 막고 작성·제출은 계속.

### 13.6 개발 검증과 운영 출시 판정 (C1)

| 구분 | 런타임 | 의미 |
|---|---|---|
| 개발(Windows 10 + Docker Desktop/WSL2) | runc + 13.2 옵션 | **"개발 검증"**만. 컨테이너는 WSL2 VM 커널 위에서 돌고 VM은 Windows 드라이브에 접근할 수 있어 운영 근거가 아니다 |
| 운영(Linux) 1순위 | 샌드박스 컨테이너만 `--runtime=runsc`(gVisor) | 런타임은 컨테이너 단위라 app의 host 네트워크와 충돌하지 않는다 |
| 운영 최소 | 샌드박스 전용 rootless dockerd(소켓은 runner 사용자만 접근) + 13.2 옵션 | runsc 설치가 불가할 때 |
| 운영 최후 수단 | rootful runc + 13.2 옵션을 **샌드박스 전용 호스트**(DB·모델·다른 서비스 없음)에서만 | ADR-0010 3순위. runner가 원격 데몬으로 접속 |
| 운영 금지 | 공유 데몬에 userns-remap(app의 `--network=host`와 비호환), app·DB와 같은 호스트의 rootful runc 단독 | C1 |

- 강화 모드는 **샌드박스 전용 데몬/런타임에만** 적용한다. `app`(aiortc host 네트워크)과 나머지 서비스는 기본 데몬에서 돈다(ADR-0010).
- **출시 조건**: 운영 호스트에서 승인 런타임으로 SBX-01~14 전부 통과 + 지문 기록, 운영 호스트에서 다른 PC 브라우저로 S-1(WebRTC) 통과, 커널·이미지 패치 주기 문서화. 커널 취약점 탈출은 시험으로 증명할 수 없으므로 잔여 위험으로 남긴다.

## 14. 구현과의 차이 (코드는 수정하지 않았다)

| # | 위치 | 현재 구현 | 이 문서의 확정값 | 영향 |
|---|---|---|---|---|
| G-01 | `core/config.py` `answer_grace_seconds` | 1.5초 | 1.0초(T9) | AC-11 기대값 불일치. 설정 기본값 변경 필요 |
| G-02 | `config.cookie_name` | `aihire_sid`(운영도 같음) | 운영 `__Host-aihire_sid` | 운영에서 형제 서브도메인의 쿠키 덮어쓰기 방어 약화 |
| G-03 | `auth/deps.py` CSRF 비교 | `!=` 문자열 비교, `sessions.csrf_token` 평문 저장 | `hmac.compare_digest`. 평문 저장은 수용(쿠키 토큰 해시가 1차 비밀) | 이론적 타이밍 누출 |
| G-04 | `auth/throttle.py` `_PEPPER` | 코드 상수 | 비밀 설정값 | 상수는 공개라 DB의 이메일 키를 사전 대입으로 되돌릴 수 있음 |
| G-05 | `auth/router.py` `_client_ip` | `request.client.host` | 운영은 프록시 헤더 신뢰 설정 후 실제 IP | caddy 뒤에서 모든 사용자가 같은 IP 키 → 30회 실패로 전체 로그인 차단 |
| G-06 | `auth/router.py` 가입 | 속도 제한 없음 | IP당 1시간 10회(제안, OQ-34) | 중복 이메일 응답을 통한 계정 확인 대량화 |
| G-07 | `auth/passwords.py` | 재해시 없음 | `check_needs_rehash` | 파라미터 상향 시 기존 해시 유지 |
| G-08 | `config._check` 운영 검사 | `cookie_secure`, `runner_token`만 | + DB 비밀번호 비기본값, `trusted_origins` https, throttle 키, test 훅 비활성 | 위험 설정으로 운영 기동 가능 |
| G-09 | 마이그레이션·개발 compose | DB 롤·GRANT 없음, 앱이 `POSTGRES_USER`(superuser) 사용, `coding_testcases` 공개 뷰 없음 | 5.4 롤 3개, 앱 롤 비소유자, 공개 예시 뷰, 칼럼 권한 | 소유자는 `ALTER TABLE ... DISABLE TRIGGER`·TRUNCATE 가능 → 불변 보장이 앱 침해에 무력. app이 비공개 테스트를 읽을 수 있음 |
| G-10 | 마이그레이션 불변 트리거 | 행 단위 `BEFORE UPDATE OR DELETE`만 | + `BEFORE TRUNCATE` 문장 트리거 또는 권한 회수 | 감사·결정·동의 기록 TRUNCATE 가능 |
| G-11 | `auth/deps.py` 이후 | 라우트 정책 메타데이터·인벤토리·범위 헬퍼 없음 | 5.2·5.3 | QG-01·AC-03 시험 대상이 아직 없음(현재 라우트는 인증뿐) |
| G-12 | `main.py` | TrustedHost 없음 | 허용 호스트 목록 | DNS 리바인딩 방어 없음(개발 `localhost`) |
| G-13 | 기동 명령(미정) | uvicorn 기본 접근 로그 켜짐 | `--no-access-log`(앱 미들웨어가 쿼리 없는 접근 로그 담당) | 쿼리 문자열이 로그에 남을 수 있음 |
| G-14 | `auth/sessions.py` `revoke_all` | 호출부 없음 | 비밀번호 변경·탈퇴·비활성 시 호출 | 해당 기능 구현 시 함께 |
| G-16 | `resumes/router.py` PDF 파싱 | 하위 프로세스 + 20초 제한, 메모리 상한 없음, 업로드 요청 경로에서 동기 대기 | 운영 Linux는 자식 `RLIMIT_AS` 512 MiB. 설계안의 "worker 작업"과 달리 app이 직접 띄우는 방식은 수용(격리는 프로세스 경계로 충족) | 압축 폭탄형 PDF가 20초 안에 RAM을 크게 쓸 수 있음(LLM·STT와 RAM 공유) |
| G-15 | `core/errors.py` 코드명 | `CSRF_REJECTED`, `ORIGIN_REJECTED`, `UNAUTHENTICATED` | ADR-0011 레지스트리로 확정(리뷰 제안 `CSRF_FAILED`) | 프런트 고정 문구 매핑 |

일치 확인(차이 아님): OpenAPI 문서 운영 비활성(`main.py`), API 보안 헤더 값(`middleware.py`), 로그 허용목록·마스킹, 422 입력값 미반사, Argon2id·더미 해시·세마포어, 세션 해시 저장·60초 갱신·고정 공격 방지, 이메일·IP 이중 스로틀(존재 무관), 가입 `extra="forbid"`·OPERATOR 로그인 불가, AI 엔드포인트 허용목록, `code_execution_enabled=false`, 감사·결정·동의 기록의 우회 없는 트리거, 질문은행 회사 범위 CHECK, 재출제 상한 CHECK, 인용 원문 범위 칼럼.
