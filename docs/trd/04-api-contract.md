# TRD 04 — API 계약

- 작성: 에이전트 B. 기준: `docs/design/consolidation-round1.md`(C3·C4·C7·T8~T13·T21~T26), ADR-0011(규약), ADR-0006(상태·큐), ADR-0012(인증·격리, E).
- **구현된 절은 코드가 원천이다.** 이 문서는 다음 구현의 경로·필드·오류 코드를 그대로 적었다(2026-10-27 기준).
  - `app/auth/router.py`, `app/auth/deps.py`
  - `app/postings/router.py`·`schemas.py`·`service.py`
  - `app/consent/router.py`
  - `app/resumes/router.py`·`schemas.py`
  - `app/applications/router.py`
  - `app/core/errors.py`·`middleware.py`
  
  구현과 다르게 해야 한다고 보는 점은 **7절 "변경 제안"**에만 적었다. 표에서 "(구현)"이 붙은 행은 이미 코드가 있는 엔드포인트다.
- 아직 구현되지 않은 절(면접·코딩·담당자 지원자 관리·내부 서비스)은 이 문서를 기준으로 구현한다. 오류 코드 이름은 구현된 것과 같은 체계(2절)를 쓴다.
- 데이터 모델은 TRD 03에 있다. 데이터채널(서버→브라우저 이벤트) 메시지 스키마는 TRD 06(`06-webrtc-interview.md`, C)에 있다.

## 1. 공통 규약

### 1.1 기본

| 항목 | 규약 |
|---|---|
| 출처 | SPA와 API는 같은 출처(리버스 프록시). CORS 없음. 경로 접두사 `/api` |
| 형식 | JSON(UTF-8). 업로드만 `multipart/form-data`(필드 `file`), PDF 보기만 `application/pdf` |
| 인증 | 서버 세션 쿠키(설정 `cookie_name`, 기본 `aihire_sid`, `HttpOnly; SameSite=Strict; Path=/`, 운영은 `Secure`). DB에는 토큰의 SHA-256만. 유휴 2시간 슬라이딩, 60초 단위 갱신(USR-06) |
| CSRF | 세션이 있는 POST·PUT·PATCH·DELETE는 `X-CSRF-Token` 헤더가 세션 토큰과 같아야 한다. 아니면 403 `CSRF_REJECTED`. 토큰은 로그인 응답과 `GET /api/auth/me`의 `csrf_token`. 세션이 없으면 401이 먼저다 |
| Origin | 상태 변경 요청에 `Origin`이 있으면 `trusted_origins`에 있어야 한다. 아니면 403 `ORIGIN_REJECTED`. 가입·로그인 포함 |
| 역할 | `/api/candidate/*`·`/api/consent/*`는 `require_candidate`, `/api/recruiter/*`는 `require_recruiter`를 쓴다. 다르면 403 `ROLE_FORBIDDEN`. 공개 공고·직무 목록은 `get_principal`(모든 로그인 역할, T21) |
| 범위 | 회사는 세션의 `company_id`로만 정한다(`get_for_company`), 지원자는 세션 사용자로만 정한다(`get_for_owner`). 요청 본문의 회사·사용자 ID는 받지 않는다(`extra="forbid"`). 범위 밖 리소스는 없는 리소스와 같은 404 `NOT_FOUND`(SEC-03). 잘못된 UUID도 404 |
| 요청 ID | 응답 헤더 `X-Request-ID`(클라이언트가 64자 이하 영숫자로 보내면 그 값). 오류 본문의 `request_id`와 같다 |
| 시각 | ISO 8601 UTC. 공고 마감은 입력·표시가 KST 날짜(`deadline_date`)이고, 저장은 그날 23:59:59 KST(`deadline_at`, `app/core/timeutil.py`)다. 면접·코딩 등 시간에 민감한 응답은 `server_now`를 함께 주고, 확정은 서버가 한다 |
| 점수 | 소수 둘째 자리 문자열(예: `"87.35"`, OQ-10, T18). 표시값(첫째 자리)은 7절 CP-6 |
| 목록 | 구현된 목록(공개 공고·담당자 공고·지원자 지원 건)은 배열을 그대로 돌려준다. 페이지네이션은 담당자 지원자 목록(RC-11)부터 `?page=1&size=20`(최대 50) → `{items, total, page, size}` |
| 멱등 | `Idempotency-Key` 헤더는 쓰지 않는다. 유일 제약·조건부 갱신·턴 상태로 자연 멱등을 만든다(ADR-0011) |
| 문서 | OpenAPI(`/api/openapi.json`, `/api/docs`)는 `dev`·`test`에서만 연다(T25). 태그 `common`·`candidate`·`recruiter` |
| 보안 헤더 | 미들웨어 기본값(라우트가 덮어쓸 수 있음): `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy`·`Cross-Origin-Resource-Policy: same-origin`, `Permissions-Policy: microphone=(self), camera=(self), display-capture=(), geolocation=(), payment=(), usb=()`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` |

### 1.2 오류 응답 (`app/core/errors.py`)

```json
{"error": {"code": "INVALID_STATE", "message": "진행 중인 공고만 마감할 수 있습니다.", "request_id": "a1b2c3d4e5f60718",
           "current": "DRAFT"}}
```

- 입력 검증 실패는 422 `VALIDATION`이고 `fields: [{"loc": ["body", "weights", "logic"], "type": "greater_than_equal"}]` 형태다. **제출한 값은 되돌려 보내지 않는다.** 이력서 내용 오류는 `loc`이 `["content", …]`로 시작한다.
- 추가 필드는 코드별로 정해진 것만 둔다(2절의 "추가 필드" 열).
- DB 오류 변환:
  - SQLSTATE `AP003`(설정 잠금 트리거) → 409 `SETTINGS_LOCKED`
  - `AP001`(불허 전이) → 409 `INVALID_STATE`
  - `AP002`(불변 행 변경) → 500 `INTERNAL`(코드 결함, 경보)
  - 유일 제약 위반 → 엔드포인트별 409

### 1.3 상한값 (T22)

| 항목 | 값 | 넘으면 |
|---|---|---|
| 이력서 PDF | 10 MiB = 10,485,760바이트(`RESUME_MAX_BYTES`), 50쪽, 파싱 20초(하위 프로세스 `python -I pdf_extract.py`), 추출 텍스트 20만 자, 텍스트 30자 미만이면 스캔 PDF로 본다 | 413 `FILE_TOO_LARGE`, 422 `PDF_TOO_MANY_PAGES`·`PDF_ENCRYPTED`·`PDF_UNREADABLE` |
| 이력서 내용 | 이름 1~50자, 학력 20·경력 30·프로젝트 30·자격증 30건, 기술 100개, 설명 3,000자 | 422 `VALIDATION` |
| 공고 | 제목 200자, 요건·우대·업무 5,000자, 정원 1~1000, 코딩 제한시간 10~180분(OQ-17) | 422 `VALIDATION` |
| 커스텀 질문 | 5~500자, 1개(X-09) | 422 `CUSTOM_QUESTION_TOO_SHORT` / `VALIDATION` |
| 텍스트 답변(INT-18) | 2,000자 | 422 `VALIDATION` |
| 코드 | 65,536바이트(DB CHECK) | 413 `CODE_TOO_LARGE` |
| [실행] | 출력 각 64 KiB에서 자름(`truncated`), 지원 건당 동시 1건, 10분 20회 | 429 `RUN_BUSY` |
| 메모 | 1~2,000자 | 422 `VALIDATION` |
| 로그인 실패 | 15분 창, 이메일 키 5회·IP 키 30회 → 15분 잠금 | 429 `TOO_MANY_ATTEMPTS` |
| 가입 필드 | 이름 1~50자, 이메일 254자 이하, 비밀번호 8~128자·이메일과 다름·확인 일치, 연락처 `010-0000-0000` | 422 `VALIDATION` |

### 1.4 PDF 원본 보기 (T26, RES-08)

PDF 원본은 권한 API로만 준다. 응답 헤더는 다음과 같다.
- `Content-Type: application/pdf`
- `Content-Disposition: inline; filename="resume.pdf"`(원본 파일명은 쓰지 않는다)
- `Content-Security-Policy: sandbox`
- `X-Content-Type-Options: nosniff`
- `Cache-Control: no-store`

화면은 새 탭으로 연다. 담당자의 RC-13 파일 경로와 지원자 본인 원본(CA-6) 모두에 적용한다(둘 다 미구현).

## 2. 오류 코드 레지스트리

원천은 코드의 `AppError` 호출이다. 앞으로 `app/core/errors.py`에 enum으로 모아 OpenAPI로 내보낸다(7절 CP-1). "구현"은 이미 코드가 내는 코드다.

| 코드 | HTTP | 의미 | 추가 필드 | 사용자 문구 | 상태 |
|---|---|---|---|---|---|
| `UNAUTHENTICATED` | 401 | 세션 없음·만료 | — | 로그인이 필요합니다. | 구현 |
| `AUTH_FAILED` | 401 | 로그인 실패(계정 없음·비밀번호·역할·비활성을 구분하지 않음, USR-04) | — | 이메일 또는 비밀번호가 올바르지 않습니다. | 구현 |
| `TOO_MANY_ATTEMPTS` | 429 | 로그인 실패 제한 | — | 로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요. | 구현 |
| `ROLE_FORBIDDEN` | 403 | 역할 불일치(USR-05) | — | 담당자 계정으로는 지원자 기능을 사용할 수 없습니다. / 담당자만 사용할 수 있는 기능입니다. | 구현 |
| `CSRF_REJECTED` | 403 | CSRF 토큰 불일치 | — | 요청을 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요. | 구현 |
| `ORIGIN_REJECTED` | 403 | 허용되지 않은 Origin | — | 허용되지 않은 출처의 요청입니다. | 구현 |
| `FORBIDDEN`·`METHOD_NOT_ALLOWED`·`HTTP_ERROR` | 403·405·기타 | 프레임워크 기본 오류 | — | 요청을 처리할 수 없습니다. | 구현 |
| `NOT_FOUND` | 404 | 없음 또는 범위 밖 | — | 요청한 정보를 찾을 수 없습니다. | 구현 |
| `VALIDATION` | 422 | 입력 형식 오류 | `fields` | 입력값을 확인해 주세요. | 구현 |
| `EMAIL_UNAVAILABLE` | 409 | 가입 이메일 중복(OQ-34) | — | 사용할 수 없는 이메일입니다. | 구현 |
| `INVALID_STATE` | 409 | 현재 상태에서 할 수 없는 요청 | `current` | 상황별(예: 임시저장 상태의 공고만 게시할 수 있습니다.) | 구현(공고) |
| `VERSION_CONFLICT` | 409 | 공고 `row_version` 불일치 | `current_row_version` | 다른 곳에서 공고가 수정되었습니다. 새로고침 후 다시 시도해 주세요. | 구현 |
| `POSTING_CLOSED` | 409 | 마감 공고에 동의·지원·수정·시작·재개(7.4) | — | 마감된 공고에는 지원할 수 없습니다. / 마감된 공고는 수정할 수 없습니다. | 구현 |
| `DEADLINE_IN_PAST` | 422 | 마감일이 오늘 이전 | — | 마감일은 오늘 이후여야 합니다. | 구현 |
| `INCOMPLETE_POSTING` | 422 | 게시 필수값 누락 | `missing` | 게시에 필요한 항목을 채워 주세요. | 구현 |
| `WEIGHTS_CODING_MISMATCH` | 422 | 코딩 여부와 가중치 항목 불일치 | — | 코딩테스트를 켠 공고는 코딩테스트 가중치가 필요합니다. 등 | 구현 |
| `WEIGHT_SUM_NOT_100` | 422 | 가중치 합 ≠ 100(ADM-05, AC-21) | `total` | 가중치의 합이 100이어야 저장됩니다. | 구현 |
| `CUSTOM_QUESTION_TOO_SHORT` | 422 | 커스텀 질문 5자 미만 | — | 커스텀 질문은 5자 이상이어야 합니다. | 구현 |
| `SETTINGS_LOCKED` | 409 | 첫 응시 시작 뒤 코딩 설정·가중치 변경(JOB-06, AC-21) | — | 첫 응시가 시작된 공고는 코딩테스트 설정과 평가 가중치를 바꿀 수 없습니다. | 구현 |
| `HAS_APPLICATIONS` | 409 | 지원자가 있는 공고 삭제(JOB-04) | — | 지원자가 있는 공고는 삭제할 수 없고 마감만 할 수 있습니다. | 구현 |
| `CONSENT_UNAVAILABLE` | 503 | 동의 문서 미등록 | — | 동의 문서를 불러올 수 없습니다. | 구현 |
| `CONSENT_VERSION_STALE` | 409 | 동의 문구 버전 불일치 | `current` | 동의 문구가 바뀌었습니다. 다시 확인해 주세요. | 구현 |
| `CONSENT_REQUIRED` | 409 | 유효한 필수 동의 기록 없이 지원(SEC-02, AC-05) | — | 필수 항목에 모두 동의해야 지원할 수 있습니다. | 구현 |
| `ALREADY_APPLIED` | 409 | 같은 공고 재지원(JOB-02, AC-04) | — | 이미 지원한 공고입니다. | 구현 |
| `RESUME_MIN_FIELDS` | 422 | 성명 + (학력 또는 경력 1건) 미충족(RES-06) | — | 성명과 학력 또는 경력을 1건 이상 입력해 주세요. | 구현 |
| `FILE_TOO_LARGE` | 413 | 10 MiB 초과 | — | 파일 크기는 10MB 이하여야 합니다. | 구현 |
| `NOT_PDF` | 415 | 확장자 `.pdf`가 아니거나 첫 1024바이트에 `%PDF-` 없음 | — | PDF 파일만 올릴 수 있습니다. | 구현 |
| `PDF_ENCRYPTED` | 422 | 암호화 PDF | — | 암호가 걸린 PDF는 올릴 수 없습니다. | 구현 |
| `PDF_TOO_MANY_PAGES` | 422 | 50쪽 초과 | — | 50쪽 이하의 PDF만 올릴 수 있습니다. | 구현 |
| `PDF_UNREADABLE` | 422 | 손상·파싱 실패·20초 초과 | — | PDF 파일이 손상되었거나 열 수 없습니다. 다른 파일을 올려 주세요. | 구현 |
| `REAUTH_FAILED` | 403 | 탈퇴 비밀번호 재확인 실패 | — | 비밀번호가 올바르지 않습니다. | 예정 |
| `TOO_MANY_REQUESTS` | 429 | 업로드·가입 빈도 제한 | — | 요청이 많습니다. 잠시 후 다시 시도해 주세요. | 예정 |
| `PRECHECK_REQUIRED` | 409 | 서버에 점검 통과 기록 없음(INT-01·17, AC-08) | — | 환경 점검을 먼저 완료해 주세요. | 예정 |
| `PRECHECK_BUSY` | 429 | 점검 연결 상한 4(C4) | — | 점검 연결이 많습니다. 잠시 후 다시 시도해 주세요. | 예정 |
| `QUESTIONS_NOT_READY` | 409 | 질문 구성 중 | `retry_after_sec` | 질문을 준비하고 있습니다. | 예정 |
| `CAPACITY_FULL` | 409 | 동시 응시 상한(INT-13), 상태는 READY | — | 이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요. | 예정 |
| `AI_UNAVAILABLE` | 503 | LLM 또는 STT 불가(INT-14, OQ-08) | — | AI 서버를 사용할 수 없어 지금은 면접을 시작할 수 없습니다. 잠시 후 다시 시도해 주세요. | 예정 |
| `CODE_EXECUTION_UNAVAILABLE` | 503 | 샌드박스 미검증·runner 불가(T13) | — | 지금은 코드 실행 환경을 사용할 수 없습니다. | 예정 |
| `SDP_INVALID` | 422 | 비디오 포함·오디오 없음 등(INT-08) | — | 연결 정보를 처리할 수 없습니다. | 예정 |
| `STALE_TURN` | 409 | 현재 턴이 아닌 `turn_id`(C3) | — | 이전 질문에 대한 요청입니다. 화면을 새로 불러옵니다. | 예정 |
| `TURN_CLOSED` | 409 | 이미 확정된 턴에 텍스트 답변 | `end_reason` | 답변 시간이 끝났습니다. | 예정 |
| `TEXT_MODE_NOT_ALLOWED` | 409 | 텍스트 모드 조건 미충족(T12) | — | 지금은 텍스트 답변으로 바꿀 수 없습니다. | 예정 |
| `TIME_OVER` | 409 | 코딩 마감 + 유예 경과 | — | 제한시간이 끝났습니다. 마지막으로 저장된 코드가 제출됩니다. | 예정 |
| `ALREADY_SUBMITTED` | 409 | 최종 제출 이미 있음 | `kind` | 이미 제출되었습니다. | 예정 |
| `CODE_TOO_LARGE` | 413 | 코드 64 KiB 초과 | — | 코드가 너무 깁니다. | 예정 |
| `RUN_BUSY` | 429 | 실행 중·빈도 초과 | — | 실행 요청이 많습니다. 잠시 후 다시 실행해 주세요. | 예정 |
| `EVAL_NOT_DONE` | 409 | 평가 완료 전 결정(7.3) | — | 평가가 완료된 뒤 결정할 수 있습니다. | 예정 |
| `DECISION_CONFLICT` | 409 | 결정 `expected_version` 불일치 | `current_version` | 다른 담당자가 먼저 변경했습니다. 최신 내용을 불러옵니다. | 예정 |
| `REPORT_NOT_READY` | 409 | 리포트 없음 | — | 평가가 아직 끝나지 않았습니다. | 예정 |
| `NOT_ON_HOLD` | 409 | 보류가 아닌 건 재평가(ADM-06) | — | 평가 보류 상태에서만 재평가할 수 있습니다. | 예정 |
| `NOT_AUTHOR` | 403 | 다른 담당자의 메모 수정·삭제 | — | 작성자만 수정할 수 있습니다. | 예정 |
| `ACTIVE_ATTEMPT` | 409 | 응시 중 탈퇴(OQ-12 잠정) | — | 응시가 끝난 뒤 탈퇴할 수 있습니다. | 예정 |
| `INTERNAL` | 500 | 예상하지 못한 오류(원인 비노출) | — | 일시적인 오류가 발생했습니다. | 예정 |

## 3. 공통·공고·동의·이력서·지원 (구현됨)

표기: `{pid}` 공고, `{aid}` 지원 건 ID. **감사**는 `audit_logs.action`이다.

### 3.1 인증·시스템

| 메서드·경로 | 역할 | 요청 → 응답 | 오류 | 감사 |
|---|---|---|---|---|
| `POST /api/auth/signup` (구현) | 공개 | `{name, email, password, password_confirm, phone}`(그 밖의 필드는 422) → 201 `{user_id}`. 역할은 항상 CANDIDATE(USR-03), 이메일은 소문자로 저장 | 422 `VALIDATION`, 409 `EMAIL_UNAVAILABLE`, 403 `ORIGIN_REJECTED` | — |
| `POST /api/auth/login` (구현) | 공개 | `{role: "CANDIDATE"\|"RECRUITER", email, password}` → 200 `{user: {id, role, name, company_id, company_name}, csrf_token}` + `Set-Cookie`. 매번 새 세션. 없는 계정도 더미 해시로 검증 | 401 `AUTH_FAILED`, 429 `TOO_MANY_ATTEMPTS`, 422, 403 `ORIGIN_REJECTED` | — |
| `POST /api/auth/logout` (구현) | 로그인 | → 204, 세션 행·쿠키 삭제 | 401, 403 `CSRF_REJECTED`·`ORIGIN_REJECTED` | — |
| `GET /api/auth/me` (구현) | 로그인 | → 200 `{user, csrf_token}` | 401 | — |
| `GET /api/healthz` (구현) | 공개 | → `{status: "ok"}` | — | — |
| `GET /api/system/ai-health` | 로그인 | → `{llm, stt, tts, code_execution: "UP"\|"DOWN", server_now}`(10초 캐시). LLM·STT DOWN이면 시작 차단, TTS DOWN이면 경고(OQ-08), 코딩 공고는 code_execution도 확인(T13) | 401 | — |

### 3.2 공고 열람 (모든 로그인 역할, T21)

| 메서드·경로 | 요청 → 응답 | 오류 |
|---|---|---|
| `GET /api/job-families` (구현) | → `[{code, label}]`(BACKEND·FRONTEND·DATA·AI_ML·DEVOPS·MOBILE·QA·PM) | 401 |
| `GET /api/postings?q=&job_family=` (구현) | → `[PostingPublicOut]`. OPEN·마감 전·삭제 안 됨, 모든 회사, 마감 임박 순. `q`(100자 이하)는 제목·업무·요건 부분 일치(JOB-08). 지원자는 `has_applied`가 채워진다 | 401, 422 |
| `GET /api/postings/{pid}` (구현) | → `PostingPublicOut`. DRAFT·삭제는 404. CLOSED는 그 공고에 지원한 지원자에게만 보이고 그 밖에는 404 | 404 |

`PostingPublicOut` = `{id, title, company_name, company_intro, job_family, job_family_label, requirements, preferred, duties, headcount, deadline_date, deadline_at, is_open, coding_enabled, coding_language, coding_time_limit_min, has_applied}`. **가중치·커스텀 질문·코딩 문제 ID·제목은 없다**(SEC-04, 면접 전 노출 방지). 담당자는 `has_applied`가 항상 false이고, [지원하기]를 누르면 지원 API가 403 `ROLE_FORBIDDEN`을 준다(AC-02).

### 3.3 동의·이력서·지원 (지원자)

| 메서드·경로 | 요청 → 응답 | 오류 | 감사 |
|---|---|---|---|
| `GET /api/consent/current` (구현) | → `{version, is_draft, body_md, items: [{code, label, required}]}`. 가장 최근 문서 | 503 `CONSENT_UNAVAILABLE` | — |
| `POST /api/candidate/postings/{pid}/consents` (구현) | `{version, answers: {code: bool}}`(모든 항목 코드가 있어야 함) → 201 `{consent_id, all_required_agreed, version, agreed_at}`. 거부도 기록한다(SEC-02). 진행 가능 여부는 `all_required_agreed`로 판단하고, false면 화면이 공고 상세로 돌아간다 | 404, 409 `POSTING_CLOSED`, 409 `CONSENT_VERSION_STALE`(`current`), 422 `VALIDATION` | CONSENT_RECORD |
| `POST /api/candidate/resume-files` (구현) | multipart `file` → 201 `ResumeFileOut`. 크기·확장자·매직바이트·파싱을 동기로 확인. 텍스트가 30자 이상이면 `extract_resume` 작업 등록 + `PENDING`, 아니면 `NO_TEXT`(RES-04 직접 입력 안내). 저장 이름은 서버가 만든 UUID | 413 `FILE_TOO_LARGE`, 415 `NOT_PDF`, 422 `PDF_ENCRYPTED`·`PDF_TOO_MANY_PAGES`·`PDF_UNREADABLE` | — |
| `GET /api/candidate/resume-files/{file_id}` (구현) | → `ResumeFileOut`(추출 상태 폴링, 2초 간격). `fields`는 원문 부분 문자열로 검증된 값만(RES-03) | 404(소유자 아님 포함) | — |
| `GET /api/candidate/resume` (구현) | → `{resume: null \| {content: ResumeContent, source: PDF\|MANUAL, file_id, updated_at}}` | — | — |
| `PUT /api/candidate/resume` (구현) | `{content: ResumeContent, file_id?}` → `{ok: true}`(등록본 갱신, RES-07) | 422 `RESUME_MIN_FIELDS`·`VALIDATION`, 404(파일) | — |
| `POST /api/candidate/postings/{pid}/applications` (구현) | `{consent_id, resume: ResumeContent, resume_file_id?, update_registered: false}` → 201 `{application_id}`. 같은 트랜잭션에서 `resume_snapshot`·`resume_snapshot_text`(성명·연락처 제외 평문, QST-03 근거) 고정. 등록본은 `update_registered`가 true일 때만 갱신 | 403 `ROLE_FORBIDDEN`(담당자), 404, 409 `POSTING_CLOSED`, 409 `CONSENT_REQUIRED`, 409 `ALREADY_APPLIED`, 422 `RESUME_MIN_FIELDS`·`VALIDATION` | — |
| `GET /api/candidate/applications` (구현) | → `[CandidateApplicationOut]`(지원일 역순) | — | — |
| `GET /api/candidate/applications/{aid}` (구현) | → `CandidateApplicationOut` | 404 | — |
| `GET /api/candidate/applications/{aid}/result` (구현) | → `CandidateResultOut`(5절) | 404 | — |
| `DELETE /api/candidate/me` | `{password}` → 202, 전 세션·쿠키 삭제, TRD 03 8절의 탈퇴 처리 | 403 `REAUTH_FAILED`, 409 `ACTIVE_ATTEMPT` | ACCOUNT_WITHDRAW |
| `GET /api/candidate/resume-file` | 등록본 원본 PDF(1.4) | 404 | — |

데이터 형식은 다음과 같다.
- `ResumeContent` = `{name, education[{school, major?, degree?, period?}], career[{company, role?, period?, description?}], projects[{title, role?, period?, description?}], skills[str], certificates[{name, issuer?, date?}]}`. 각 항목은 `extra="forbid"`이고, 최소 조건 미달이면 `RESUME_MIN_FIELDS`다.
- `ResumeFileOut` = `{file_id, page_count, text_extractable, extraction_status: PENDING|DONE|NO_TEXT|FAILED, fields: ResumeContent|null, manual_input_required}`.

## 4. 담당자 (회사 범위, 다른 회사는 404)

### 4.1 공고·평가 설정 (구현됨)

| 메서드·경로 | 요청 → 응답 | 오류 | 감사 |
|---|---|---|---|
| `GET /api/recruiter/postings?status=DRAFT\|OPEN\|CLOSED` (구현) | → `[PostingSummaryOut]`. 탭은 `effective_status`로 거른다(OPEN인데 마감이 지나면 CLOSED, JOB-07). 최근 수정 순 | 422 | — |
| `POST /api/recruiter/postings` (구현) | `PostingIn` → 201 `PostingDetailOut`(DRAFT). 기본 가중치 설정 v1을 함께 만든다(6.4) | 422 `VALIDATION` | — |
| `GET /api/recruiter/postings/{pid}` (구현) | → `PostingDetailOut` | 404(삭제 포함) | — |
| `PUT /api/recruiter/postings/{pid}` (구현) | `PostingIn`(+ `row_version` 필수) → 200 `PostingDetailOut`. 잠금 전에 `coding_enabled`가 바뀌면 그 기본 가중치로 새 설정 버전을 만들고(커스텀 질문 유지) `weights_reset: true` | 409 `VERSION_CONFLICT`(`current_row_version`), 409 `POSTING_CLOSED`, 409 `SETTINGS_LOCKED`, 422 `DEADLINE_IN_PAST`, 422 `VALIDATION` | — |
| `GET /api/recruiter/postings/{pid}/preview` (구현) | → `PostingPublicOut`(지원자에게 보이는 모습, JOB-03) | 404 | — |
| `POST /api/recruiter/postings/{pid}/publish` (구현) | → `PostingDetailOut`(DRAFT→OPEN) | 409 `INVALID_STATE`(`current`), 422 `INCOMPLETE_POSTING`(`missing`), 422 `DEADLINE_IN_PAST`, 422 `WEIGHTS_CODING_MISMATCH` | — |
| `POST /api/recruiter/postings/{pid}/close` (구현) | → `PostingDetailOut`(OPEN→CLOSED, MANUAL). 같은 트랜잭션에서 READY·PREPARING·INTERRUPTED 지원 건을 EXPIRED로 바꾼다(코딩 중 끊긴 건은 초안 AUTO_EXPIRED 제출, 응답이 있으면 평가 등록, `lifecycle.expire_unfinished`) | 409 `INVALID_STATE` | POSTING_CLOSE |
| `DELETE /api/recruiter/postings/{pid}` (구현) | → 204(`deleted_at`) | 409 `HAS_APPLICATIONS` | POSTING_DELETE |
| `GET /api/recruiter/postings/{pid}/evaluation-settings` (구현) | → `EvalSettingsOut` | 404 | — |
| `PUT /api/recruiter/postings/{pid}/evaluation-settings` (구현) | `{weights: {expertise, logic, specificity, delivery, coding?}, custom_question?}` → `EvalSettingsOut`(새 버전 행 + `current_settings_id`, 공고 행 `FOR UPDATE`). 빈 커스텀 질문은 NULL(자동 채움, QST-04) | 422 `WEIGHTS_CODING_MISMATCH`, 422 `WEIGHT_SUM_NOT_100`(`total`), 422 `CUSTOM_QUESTION_TOO_SHORT`, 409 `SETTINGS_LOCKED` | SETTINGS_CHANGE(`version`) |
| `POST /api/recruiter/postings/{pid}/evaluation-settings/reset` (구현) | → `EvalSettingsOut`(기본 가중치, 커스텀 질문 없음. ADM-05 [초기화]) | 409 `SETTINGS_LOCKED` | SETTINGS_CHANGE(`reset`) |
| `GET /api/recruiter/coding-problems` (구현) | → `[{id, title, statement_md, language, public_examples: [{input, output}]}]`(활성 문제, OQ-14) | — | — |

데이터 형식은 다음과 같다.
- `PostingIn` = `{title?, job_family?(코드), requirements?, preferred?, duties?, headcount?, deadline_date?(KST 날짜), coding_enabled, coding_problem_id?, coding_time_limit_min?, row_version?}`. 임시저장 중에는 모두 선택이고, 완결성은 게시 때 검사한다.
- `PostingSummaryOut` = `{id, title, job_family, status, effective_status, deadline_date, headcount, coding_enabled, applicant_count, completed_count, updated_at}`.
- `PostingDetailOut` = Summary + `{requirements, preferred, duties, coding_problem_id, coding_language, coding_time_limit_min, settings_locked, row_version, evaluation_settings: EvalSettingsOut, weights_reset}`.
- `EvalSettingsOut` = `{version, weights, custom_question, rubric_version, locked, coding_enabled}`.

### 4.2 지원자 관리 (미구현, 이 표대로 구현)

| 메서드·경로 | 요청 → 응답 | 오류 | 감사 |
|---|---|---|---|
| `GET /api/recruiter/dashboard` | → `{open_postings, awaiting_review}`(awaiting_review = 평가 DONE + 결정 UNDECIDED, ADM-01) | — | — |
| `GET /api/recruiter/applications?posting_id=&decision=&eval_status=&attempt_status=&sort=applied_at\|total_score&page&size` | → `{items: [{id, candidate_name, posting{id, title}, applied_at, attempt_status, answered_main_count, eval_status, total_score\|null, decision}], total, page, size}`. 총점은 평가 DONE일 때만(ADM-02) | 422 | — |
| `GET /api/recruiter/applications/{aid}` | → `{id, candidate{name, email, phone}, posting{id, title}, attempt_status, interrupted_from, eval_status, decision, decision_version, answered_main_count, text_mode_used, can_decide, can_re_evaluate, applied_at, submitted_at}` | 404 | — |
| `GET /api/recruiter/applications/{aid}/resume` · `/resume-file` | 지원 시점 스냅샷 / 원본 PDF(1.4, RES-08) | 404 | RESUME_FILE_VIEW(파일) |
| `GET /api/recruiter/applications/{aid}/report` | → `RecruiterReport`(4.3). 리포트 탭을 열 때만 호출 | 409 `REPORT_NOT_READY` | REPORT_VIEW |
| `GET /api/recruiter/applications/{aid}/coding-result` | → `{enabled, submission{kind, submitted_at, code, compile_ok, grading_status, passed, total, score}, tests[{ordinal, visibility, passed, outcome, time_ms, input, expected_output}], code_review}`. 비공개 테스트는 담당자에게만 공개(6.6). 코드 리뷰는 점수 미반영(COD-07) | 404 | — |
| `POST /api/recruiter/applications/{aid}/decision` | `{decision: "PASS"\|"FAIL", expected_version}`(version = 결정 이력 행 수) → 200 `{decision, version, decided_at}`. 같은 값이면 변경 없이 200 | 409 `EVAL_NOT_DONE`, 409 `DECISION_CONFLICT`(`current_version`) | DECISION_SET(`from`, `to`) |
| `GET /api/recruiter/applications/{aid}/decision-history` | → `[{from, to, decided_by{id, name}, decided_at}]`(ADM-04, AC-20) | 404 | — |
| `POST /api/recruiter/applications/{aid}/re-evaluate` | → 202 `{eval_status: "PENDING"}`(ON_HOLD→PENDING, `register_evaluation(trigger="RECRUITER")`) | 409 `NOT_ON_HOLD` | RE_EVALUATE |
| `GET·POST /api/recruiter/applications/{aid}/notes` | 목록(삭제 제외) / `{body}` → 201 `{id, author{id, name}, body, created_at}`(ADM-07) | 422 | NOTE_CREATE |
| `PATCH·DELETE /api/recruiter/notes/{nid}` | `{body}` → 200 / 204(`deleted_at`) | 403 `NOT_AUTHOR`, 404 | NOTE_UPDATE·NOTE_DELETE |
| `GET /api/recruiter/stats?from=YYYY-MM&to=YYYY-MM` | → `{monthly_applicants[{month, count}], avg_score_by_job_family[{job_family, avg, n}], pass_rate_by_posting[{posting_id, title, decided, passed}], pass_rate_by_job_family[…]}`. 합격률은 결정 완료 건 기준(ADM-08) | 422 | — |

### 4.3 `RecruiterReport`

```json
{"application_id": "…", "total_score": "78.40",
 "criteria": [{"key": "EXPERTISE", "score": "80.00", "weight": 30, "insufficient": 1}, "…"],
 "coding": {"score": "75.00", "weight": 20},
 "insufficient_count": 3, "no_answer_count": 1, "answered_main_count": 10,
 "summary": "…", "strengths": ["…"], "improvements": ["…"],
 "questions": [{"seq": 1, "slot_type": "PERSONALITY", "question_text": "…", "reasked": 0,
   "question_scores": {"EXPERTISE": "3.50"},
   "followup": {"asked": true, "skip_cause": null},
   "answers": [{"role": "MAIN", "input_mode": "VOICE", "end_reason": "BUTTON", "no_answer": false, "stt_failed": false,
                "display_text": "…(eval_text)", "transcript": "…",
                "items": [{"key": "EXPERTISE", "score": 4, "evidence": "OK",
                           "quote": {"text": "…", "start": 12, "end": 40}, "comment": "…"}]}]}],
 "proctor": {"leave_count": 0, "away_ms": 0},
 "versions": {"model": "qwen3.5:4b", "model_digest": "…", "prompt": "…", "rubric": "rubric-v1", "scoring": "score-v1",
              "weights_version": 2}}
```

- 인용 `start`·`end`는 `display_text`(= `eval_text`) 기준 원문 범위이고, `quote.text`는 그 범위의 원문이다(ADR-0008).
- `evidence` 값은 OK·INSUFFICIENT·NO_ANSWER다. 근거 부족과 답변 없음은 따로 센다(T19).
- 화면은 ADM-03 고정 문구를 붙인다. 합불 추천이나 순위 필드는 없다(P-8).

## 5. 면접·코딩 (지원자, 미구현)

경로 접두 `…` = `/api/candidate/applications/{aid}`. 모든 경로는 본인 범위다.
- 클라이언트→서버 명령은 HTTPS로 보낸다(T8, CSRF 적용).
- 서버→브라우저 통지(질문 제시와 `turn_id`, `mic_open`, 자막, 확정, 다음 단계, 상태 변경, 연결 인계)는 데이터채널로 보낸다(TRD 06).

### 5.1 면접

| 메서드·경로 | 상태 조건 | 요청 → 응답 | 오류 |
|---|---|---|---|
| `POST …/interview/prepare` | READY(PREPARING이면 200) | → `{attempt_status: "PREPARING", questions_ready}`. `compose_questions` 등록 | 409 `POSTING_CLOSED`·`INVALID_STATE` |
| `POST …/interview/leave-prepare` | PREPARING(READY면 200) | → `{attempt_status: "READY"}` | 409 `INVALID_STATE` |
| `GET …/interview/guide` | 모든 상태 | → `{main_count: 10, main_answer_sec: 60, followup_answer_sec: 45, followup_max_total: 4, warning_sec: 10, coding{enabled, time_limit_min, language}}`(INT-03) | 404 |
| `POST …/precheck/media` | PREPARING | `{mic_ok, camera_ok, speaker_ok?}`(지원자 신고) → 204. 마이크·카메라 모두 true면 `precheck_media_ok_at` | 409 `INVALID_STATE` |
| `POST …/rtc/offer` | `PRECHECK`: PREPARING, 슬롯 불필요, 서버 전체 점검 연결 4 이하. `INTERVIEW`: INTERVIEWING + 이 지원 건의 유효 슬롯(C4) | `{purpose: "PRECHECK"\|"INTERVIEW", sdp, type: "offer"}` → `{sdp, type: "answer", rtc_session_id}`. 비트리클, 서버 `iceServers=[]`, 지원 건당 연결 1개(새 연결이 이전 연결을 닫음), 오디오만(INT-08). 점검 통과(`precheck_webrtc_ok_at`)는 음성 런타임이 발화 인식 뒤 기록 | 409 `INVALID_STATE`(`current`), 429 `PRECHECK_BUSY`, 422 `SDP_INVALID` |
| `POST …/interview/start` | PREPARING | → `InterviewState`(5.2). TRD 03 6절의 시작 트랜잭션. 점검 기록은 30분 이내만 인정. TTS DOWN이면 `warnings: ["TTS_UNAVAILABLE"]` | 409 `PRECHECK_REQUIRED`, 409 `QUESTIONS_NOT_READY`(`retry_after_sec`), 409 `CAPACITY_FULL`(그 뒤 READY), 409 `POSTING_CLOSED`, 503 `AI_UNAVAILABLE`, 503 `CODE_EXECUTION_UNAVAILABLE`(코딩 공고, T13), 409 `INVALID_STATE` |
| `GET …/interview/state` | 모든 상태 | → `InterviewState`. 재진입·이벤트 누락 복구의 기준(INT-11) | 404 |
| `POST …/interview/resume` | INTERRUPTED(이미 INTERVIEWING·CODING이면 200, 멱등) | → `InterviewState`. 음성 재개는 슬롯 재확보(유예 중이면 자기 슬롯), 코딩 재개는 슬롯 불필요. 끊긴 문항은 처음부터 다시(OQ-21). 클라이언트 순서는 **resume → offer** | 409 `CAPACITY_FULL`·`POSTING_CLOSED`·`INVALID_STATE` |
| `POST …/interview/text-mode` | INTERVIEWING 또는 INTERRUPTED(면접 중 끊김) | → `InterviewState`(`mode: "TEXT"`). **서버 판정**(T12): 현재 문항 제시가 상한 3회에 도달한 뒤 다시 끊겼거나, 서버가 20초 이상 살아 있는 연결을 못 본 경우에만 허용. `text_mode_since` 기록 | 409 `TEXT_MODE_NOT_ALLOWED`, 409 `CAPACITY_FULL` |
| `POST …/interview/turns/{turn_id}/done` | INTERVIEWING | [답변 완료] → 202 `{turn_id, state: "FINALIZING"}`. 같은 턴이 이미 FINALIZING·CLOSED면 200 `{turn_id, state, end_reason}`(멱등). 마감 + 유예 1.0초(T9) 뒤 도착하면 시간 만료로 확정 | 409 `STALE_TURN` |
| `POST …/interview/turns/{turn_id}/text-answer` | 텍스트 모드, 턴 ANSWERING | `{text}`(2,000자 이하, 빈 문자열은 '답변 없음') → 202 `{turn_id, state}`. 마감은 음성과 같은 60·45초(D 이견 7, 팀 확인) | 409 `STALE_TURN`·`TURN_CLOSED`, 422 |
| `POST …/heartbeat` | PREPARING, 텍스트 모드 INTERVIEWING, CODING | → 204(30초 주기, C7). 음성 INTERVIEWING은 런타임이 갱신 | 409 `INVALID_STATE` |
| `POST …/interview/proctor-events` | INTERVIEWING·CODING | `{kind: "LEAVE"\|"RETURN", occurred_at, away_ms?}` → 204(INT-16, P2) | — |

### 5.2 `InterviewState`

```json
{"attempt_status": "INTERVIEWING", "interrupted_from": null, "mode": "VOICE", "server_now": "2026-10-27T01:00:00.000Z",
 "total_main": 10, "answered_main": 2, "next_after_interview": "CODING", "warnings": [],
 "turn": {"turn_id": "…", "main_no": 3, "is_followup": false, "presentation_no": 1, "phase": "ANSWERING",
          "question_text": "…", "limit_sec": 60, "opened_at": "…", "deadline_at": "…"},
 "slot_grace_until": null, "coding_deadline_at": null}
```

질문 출처·근거·꼬리질문 판단 사유는 넣지 않는다(D-09).

### 5.3 코딩 (모든 호출이 하트비트를 겸함, C7)

| 메서드·경로 | 상태 조건 | 요청 → 응답 | 오류 |
|---|---|---|---|
| `GET …/coding` | CODING | → `{problem{title, statement_md, examples[{input, output}]}, language: "python", languages: ["python"], deadline_at, server_now, draft{code, revision, saved_at}, run_available}`. 예시는 PUBLIC만 | 409 `INVALID_STATE` |
| `PUT …/coding/draft` | CODING, 마감 + 유예 전 | `{code, revision}`(클라이언트 단조 증가) → `{revision, saved_at, applied}`. 저장된 번호보다 클 때만 반영(D-11) | 409 `TIME_OVER`, 413 `CODE_TOO_LARGE` |
| `POST …/coding/runs` | CODING, 마감 전 | `{code}` → `{run_id, verdict: NORMAL\|ABNORMAL\|ENV_ERROR, outcome, stdout, stderr, truncated, duration_ms}`. **동기 실행**(벽시계 10초), 공개 예시만. NORMAL = 제한 안 종료 코드 0(E-Q1). 점수 미반영(COD-03) | 429 `RUN_BUSY`, 503 `CODE_EXECUTION_UNAVAILABLE`, 409 `TIME_OVER` |
| `GET …/coding/runs/{run_id}` | 본인 | → 같은 형식(새로고침 복구) | 404 |
| `POST …/coding/submit` | CODING | `{code, revision, confirmed: true}` → `{attempt_status: "SUBMITTED"}`. 제출 행 + `grade_coding` + 평가 등록, 점수 없음 | 409 `ALREADY_SUBMITTED`(`kind`), 409 `TIME_OVER`, 422 |

공고가 마감되면 prepare·start·resume·text-mode는 409 `POSTING_CLOSED`다. 진행 중인 INTERVIEWING·CODING은 계속된다(OQ-27).

### 5.4 내부 서비스 (Caddy 미노출, 사설망·루프백만, SEC-06)

| 대상 | 경로 | 호출자 | 요약 | 상세 |
|---|---|---|---|---|
| ai | `GET /v1/health` | app·worker | llm·stt·tts·embed 상태와 모델 digest | TRD 05 |
| ai | `POST /v1/llm/{task}` 외 stt·tts·embed | app(꼬리질문 RT, 음성), worker | 공통 봉투. `INFRA_ERROR`·`PREEMPTED`는 작업 시도로 세지 않는다(ADR-0006) | TRD 05 |
| runner | `GET /v1/health` | app·worker | `{sandbox_verified, fingerprint, runtime}`. false면 [실행] 503, 코딩 공고 시작 차단(T13) | TRD 07 |
| runner | `POST /v1/run` | app([실행]), worker(`grade_coding`) | `X-Runner-Token`. `{job_id, mode: run\|grade, language, code, cases[{case_id, stdin}], profile}` → 케이스별 결과. **기대 출력은 보내지 않는다.** 테스트 1건 = 컨테이너 1개, 비교는 worker(C8) | TRD 07 |

운영자는 웹 API가 없다(OQ-19). `python -m app.cli`(`create-company`, `create-recruiter`, `seed-demo`, `requeue-evaluation` 등)로 처리하고 감사(`actor_role=OPERATOR_CLI`)를 남긴다.

## 6. 지원자 응답 스키마 (SEC-04, RPT-03, AC-19)

- 지원자 세션이 받는 **모든** 응답은 지원자 전용 모델로만 만든다. 담당자 테이블(리포트·메모·가중치)은 읽지 않는다(`applications/router.py` 머리말).
  - 대상: `/api/candidate/*`, `/api/postings*`, `/api/consent/*`, `/api/auth/me`, 오류, 시그널링 응답. 데이터채널은 TRD 06.
- `CandidateApplicationOut`(구현) = `{id, posting{id, title, company_name, coding_enabled}, applied_at, attempt_status, status_label, evaluation_label, decision, answered_main_count, can_start}`.
  - 평가 보류(ON_HOLD)는 "평가 진행 중"으로 보인다(재응시 요구 없음).
- `CandidateResultOut`(구현) = `{application_id, decision, status_label, feedback}`.
  - `feedback`은 결정이 PASS·FAIL이고 지원자 피드백 행이 있을 때만 채워지고, 아니면 `null`이다(X-04, 6.6).
  - `feedback` = `{criteria{expertise, logic, specificity, delivery}(100점 만점 문자열), strengths[], improvements[], coding: ALL_PASSED|NOT_ALL_PASSED|null(OQ-24), notice: "이 피드백은 AI가 만든 참고 자료입니다"}`(RPT-04).
- **금지 키**: 재귀 검사, 하나라도 있으면 실패. QG-02, AC-19.
  - `total`, `total_score`, `weights`, `weight`, `w_*`, `quote*`, `evidence*`, `insufficient*`, `no_answer_count`, `comment`, `rubric*`, `model*`, `prompt*`, `manifest*`, `run_id`, `score_coding`, `notes`, `memo`, `hidden*`, `expected_output`, `testcase*`, `grounding`, `source`(이력서 `source` 제외), `bank_question_id`, `skip_cause`, `custom_question`, `coding_problem_id`, `eval_settings_id`, `decided_by`, `company_id`(로그인 응답의 담당자 본인 값 제외).
  - 메모·가중치·인용·비공개 테스트·커스텀 질문에 넣은 카나리아도 0건이어야 한다.

## 7. 변경 제안 (구현과 다르게 해야 한다고 보는 점)

| # | 대상(구현) | 현재 | 제안 | 근거 |
|---|---|---|---|---|
| CP-1 | `app/core/errors.py` | 오류 코드가 호출 지점의 문자열로 흩어져 있다 | 2절 표를 enum으로 모아 OpenAPI에 내보내고, 등록되지 않은 코드를 쓰면 테스트가 실패하게 한다 | D-10, 프런트 고정 문구 분기 |
| CP-2 | `applications/router.py` 지원 | 공고를 잠금 없이 읽은 뒤 지원 행을 INSERT한다 | 공고 행을 `FOR SHARE`(삭제는 `FOR UPDATE`)로 읽는다 | 삭제(JOB-04 "지원자가 있으면 삭제 불가")와 동시 지원이 겹치면 삭제된 공고에 지원 건이 생긴다. 재현: 지원 트랜잭션 INSERT 직전 정지 → 삭제 커밋 → 지원 커밋 |
| CP-3 | 같은 곳 | `resume_file_id`가 업로드 파일(purpose UPLOAD)을 그대로 가리킨다 | 지원 때 purpose `APPLICATION` 사본 행(파일 복사)을 만들어 연결한다(TRD 03 2.3) | 등록본·업로드 정리와 지원 건 보관 기간(365일, 8.5)이 서로 묶인다. 탈퇴 외 경로로 업로드 파일이 지워지면 RES-08 원본 보기가 깨진다 |
| CP-4 | 같은 곳 | 409 `ALREADY_APPLIED`에 기존 지원 건 ID가 없다 | 추가 필드 `application_id`(본인 것) | 응답 유실 후 재시도한 클라이언트가 지원 현황으로 이동할 수 있게(자연 멱등, ADR-0011) |
| CP-5 | `CandidateApplicationOut` | 한국어 `status_label`·`evaluation_label`만 있고 상태 코드가 부족하다 | 코드 `public_eval_status: NONE\|IN_PROGRESS\|DONE`, `interrupted_from`, `can_resume`, `coding{time_limit_min}` 추가. 라벨은 당분간 유지. 결정 전 DONE 문구는 7.3 용어 '검토 전'으로 | D-15·D-21. 문구를 바꿀 때마다 API가 바뀌는 것을 피한다 |
| CP-6 | `CandidateResultOut`, 담당자 점수 | 점수 문자열만 있다 | `{score, display}`(display = 소수 첫째 자리 HALF_UP, 서버 계산) | OQ-10 "화면은 소수 첫째 자리". JS 반올림 오차를 피한다 |
| CP-7 | 업로드·가입 | 빈도 제한이 없다 | 업로드 사용자당 동시 1건·분당 5건, 가입 IP 제한 → 429 `TOO_MANY_REQUESTS`(`auth_throttle` 재사용) | E SC-25, OQ-34 |
| CP-8 | `GET /api/postings`, `GET /api/candidate/applications` | 페이지네이션이 없다 | 공고 수가 늘면 `page`·`size` 추가(응답 형식 `{items, total, page, size}`로 변경) | 지금 규모(시연)에서는 문제없음. 바꿀 때 D 타입을 함께 바꾼다 |
| CP-9 | `postings/router.py` 게시 | 감사 로그가 없다(마감·삭제는 있음) | `POSTING_PUBLISH` 감사 추가 | ADM-10(P2) 공고 이력을 감사 로그로 대신할 수 있게(추적표 비고) |
| CP-10 | `consent/router.py` | `answers`에 빠진 항목이 있으면 `fields` 없이 422 | `fields: [{loc: ["body", "answers", code], type: "missing"}]` | 1.2 형식 통일 |
