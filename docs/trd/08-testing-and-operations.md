# TRD 08 — 테스트 전략과 운영

- 작성: 에이전트 E(보안·품질 검증). 상태: **확정(1라운드 통합 반영)**. 날짜: 2026-10-27.
- 권위 순서: `docs/design/consolidation-round1.md` > 이 문서 > `docs/design/proposals/agent-E-security-qa.md`(3.5~3.7). 보안 통제의 값은 `docs/trd/07-security.md`를 따른다.
- 원격 CI가 없다(OQ-20). 모든 게이트는 로컬 스크립트로 돌리고, 추적표(`docs/requirements-traceability.md`)의 **"검증 완료"는 이 문서의 테스트 ID가 실제로 실행되어 통과한 기록이 있을 때만** 쓴다.
- 실측값과 목표값을 섞지 않는다. 이 장비(Windows 10, GPU 6GB)에서 할 수 없는 검증은 8절에 "미실행"으로 따로 둔다.
- 설치·서버·Docker 실행은 이 문서를 쓰면서 하지 않았다. 스크립트 이름은 구현 단계에서 만들 계획 이름이다.

## 1. 원칙

1. **PRD 수치는 테스트가 고정한다**: `app/core/constants.py`의 값은 상수 테스트로 PRD와 대조한다(값을 바꾸려면 PRD 근거가 필요).
2. **결정적 테스트 우선**: LLM·STT·시간이 끼는 로직은 가짜 LLM·가짜 시계로 결정적으로 시험하고, 실모델·실시간 시험은 nightly에서 임계값으로 판정한다.
3. **보안 회귀는 생성형으로**: IDOR·응답 누출·로그 누출은 사람이 사례를 고르지 않고 라우트 목록·카나리아로 전수 생성한다.
4. **개발 검증과 운영 판정을 나눈다**: 샌드박스·WebRTC·부하는 운영 호스트에서 다시 돈 결과만 출시 판정에 쓴다.

## 2. 테스트 계층·도구·공통 장치

| 계층 | 도구·마커 | 대상 | 필요 환경 |
|---|---|---|---|
| 단위 | pytest 9.1.1, pytest-asyncio 1.4.1 (마커 없음) | 점수 계산, 인용 규칙, 비식별화, 정책 판정, 턴 시계·프레임 게이트, 로그 필터, 설정 검증 | 없음 |
| API 통합 | pytest `-m integration` + httpx ASGI 클라이언트 | 인증·세션·CSRF, IDOR 매트릭스, 상태 전이, 작업 큐, 응답 필드 검사 | 개발 compose PostgreSQL(`127.0.0.1:55432`), 가짜 AI |
| 샌드박스 통합 | pytest `-m docker` | runner 계약, SBX-01~14, 채점 G-b | Docker(개발: Docker Desktop·runc / 운영 판정: 운영 호스트·승인 런타임) |
| 실모델 통합 | pytest `-m models` | 이력서 질문 근거(AC-07), 인용 1차 일치율, STT·TTS 지연 | Ollama `qwen3.5:4b`, faster-whisper, Supertonic |
| WebRTC 통합 | pytest + aiortc 테스트 클라이언트 | 게이트(AC-09), 자막·확정(AC-10), 재연결(AC-13) | 합성 음성 WAV |
| 프런트 단위·컴포넌트 | Vitest 5, Testing Library, MSW(D 3.13) | 리듀서 전이표 T1~T28, 폼 검증, 결과 화면 | Node |
| E2E | Playwright 1.64(Chromium + `msedge` 채널), `--use-fake-device-for-media-stream`, `--use-fake-ui-for-media-stream`, `--use-file-for-fake-audio-capture` | 사용자 흐름, 화면 문구, 헤더·CSP 위반, 오프라인 | 전체 스택 test 프로필 |
| 접근성 | @axe-core/playwright | 주요 화면 serious 이상 0건 | E2E와 같음 |
| 수동 | 체크리스트 | 사람 발화, 청취 품질, 다른 PC·외부망 | 시연 장비 |

**공통 장치**

| 장치 | 내용 | 규칙 |
|---|---|---|
| 가짜 LLM | 작업별 대본 응답·지연·오류 주입, **받은 프롬프트 캡처** | `ai` 클라이언트 인터페이스의 test 구현. 운영 빌드에 없음 |
| 가짜 시계 | 서버 시각 주입점(턴 마감, 코딩 마감, 세션 만료, 보관 기산) | env=test에서만 활성 |
| 카나리아 | 테스트마다 `CANARY-<용도>-<난수>`를 메모·가중치·커스텀 질문·인용·비공개 테스트·이름·이메일·전화·이력서·답변·코드에 넣는다 | 나오면 안 되는 곳(지원자 응답, 데이터채널, 로그, 프롬프트)을 전수 검색 |
| PDF 픽스처 | reportlab(개발 의존성)으로 테스트가 직접 생성: 정상·스캔(이미지만)·암호화·잘린 파일·10 MiB 경계·50쪽 초과·압축 폭탄형·위장 파일 | 외부 다운로드 없음 |
| 합성 음성 | 로컬 TTS(Supertonic)로 생성한 WAV, `%noloop` 사용. 결과 WAV는 커밋하지 않음(`.gitignore`) | 실제 마이크 조건은 수동 시험 |
| test 훅 | 서버 측 WebRTC 강제 끊기, 상한 변경, 시드 계정·공고·문제 | env=test 라우트에만. 운영 라우트 목록에 있으면 QG-01 실패 |

## 3. 테스트 ID 규칙

- `T-<요구사항ID>[-<접미사>]`: 단위·통합 테스트(예: `T-USR-04`, `T-USR-04-timing`, `T-SEC-03-idor`).
- `AC-xx`: 인수 기준 테스트(5절). 한 AC는 여러 테스트 함수로 이루어질 수 있고, 모두 통과해야 그 AC가 통과다.
- `SBX-xx`: 샌드박스 자체 점검(07 13.4). runner 기동 시에도 같은 코드가 돈다.
- `QG-xx`: 품질 게이트(7절). 여러 테스트 ID의 묶음 + 정적 검사.
- 연결 방법: pytest 사용자 마커 `@pytest.mark.req("T-USR-04", "AC-02")`, Playwright는 테스트 제목 앞에 `[AC-02]`. 수집 스크립트(`tools/trace_report.py`, 계획)가 마커·제목에서 ID를 뽑아 `var/reports/trace-<날짜>.json`(테스트 ID → 테스트 이름 → 결과)을 만든다. 추적표의 상태 갱신은 이 파일을 근거로 한다.

## 4. 테스트 ID와 테스트 함수의 대응

**4.1 구현된 테스트**(`backend/tests/`, 마커 `integration`, 개발 compose PostgreSQL 필요, 2026-10-27 기준 19개 통과 보고). AC 표의 절차 중 일부만 덮으므로 해당 AC는 아직 "검증 완료"가 아니다.

| 테스트 함수 | 테스트 ID | 덮는 범위 / 남은 부분 |
|---|---|---|
| `test_auth.py::test_ac01_signup_requires_fields_and_has_no_birth_or_gender` | AC-01, T-USR-02 | API 절차 ①~④. 남음: 스키마 조회 ⑤, 화면 ⑥ |
| `test_auth.py::test_signup_duplicate_email_generic_message` | T-USR-02-dup(OQ-34) | 일반 문구. 남음: 가입 속도 제한(07 G-06) |
| `test_auth.py::test_usr04_login_failures_are_indistinguishable` | T-USR-04, QG-10 일부 | 상태·본문 동일성. 남음: 타이밍 시험(nightly) |
| `test_auth.py::test_ac02_recruiter_logs_in_only_with_issued_account` | AC-02, T-USR-03 | 절차 ①②. |
| `test_auth.py::test_login_sets_httponly_cookie_and_session_hash_only` | T-USR-06-cookie, T-SEC-05 | 쿠키 속성, 해시만 저장 |
| `test_auth.py::test_csrf_and_origin_required_for_state_changes` | T-SEC-03-csrf, QG-09 일부 | 토큰·Origin 거부 |
| `test_auth.py::test_usr06_idle_two_hours_expires_session` | T-USR-06 | 2시간 유휴 만료 |
| `test_auth.py::test_login_throttle_applies_to_unknown_emails_too` | T-USR-04-throttle | 존재 무관 동일 429 |
| `test_auth.py::test_security_headers_present` | T-SEC-05-headers, QG-09 일부 | API 응답 헤더. 남음: caddy·PDF 응답, CSP 위반 E2E |
| `test_postings_apply.py::test_job03_draft_publish_and_tabs` | T-JOB-03, T-JOB-07 | |
| `test_postings_apply.py::test_coding_toggle_resets_weights_to_coding_defaults` | T-JOB-05, T-ADM-05 | |
| `test_postings_apply.py::test_ac21_weights_sum_and_coding_item_rules` | AC-21 일부 | 합 100·코딩 항목. 남음: 첫 응시 뒤 잠금·경합(면접 시작 구현 후) |
| `test_postings_apply.py::test_ac02_recruiter_sees_posting_but_cannot_apply` | AC-02 ③④, T-USR-05 | T21 반영 |
| `test_postings_apply.py::test_ac03_other_company_posting_is_indistinguishable_from_missing` | AC-03 일부 | 공고 1종. 남음: 6절 생성형 매트릭스 전체 |
| `test_postings_apply.py::test_ac04_duplicate_application_rejected_even_concurrently` | AC-04 | 순차·동시 |
| `test_postings_apply.py::test_ac05_required_consent_blocks_application_and_is_recorded` | AC-05 API 부분 | 남음: E2E [동의하지 않음] |
| `test_postings_apply.py::test_res06_minimum_resume_and_snapshot_is_frozen` | T-RES-06, T-RES-07 | |
| `test_postings_apply.py::test_job04_delete_only_without_applicants` | T-JOB-04 | |
| `test_postings_apply.py::test_ac06_upload_rules` | AC-06 일부, T-RES-02, T-RES-04 | PNG 위장·10 MiB 초과·손상·스캔 PDF. 남음: 경계 정확값(10,485,760), 암호화, 51쪽, 압축 폭탄형 20초, E2E 수정 |

**4.2 계획**: 아래는 아직 없는 테스트의 파일 배치 계획이며, 구현하면서 실제 함수 이름을 4.1로 옮긴다. 코드 주석이 가리키는 `tests/test_constants.py`(PRD 고정값)와 `tests/test_schema.py`(금지 칼럼 부재)도 포함한다.

| 테스트 ID 묶음 | 파일(계획, `backend/` 기준) | 마커 | 상태 |
|---|---|---|---|
| T-RULE-6.1·6.2·6.4 상수, 금지 칼럼(USR-02, 8.5) | `tests/test_constants.py`, `tests/test_schema.py` | 단위 / integration | 미작성 |
| T-USR-01~07 나머지, AC-01·02 화면, QG-10 타이밍 | `tests/test_auth.py`(확장), `tests/unit/test_passwords.py`, `frontend/e2e/auth.spec.ts` | integration | 일부 구현(4.1) |
| T-SEC-03, AC-03, QG-01 | `tests/integration/test_route_inventory.py`, `tests/integration/test_idor_matrix.py` | integration | 미작성 |
| T-SEC-04, AC-19, QG-02 | `tests/integration/test_candidate_exposure.py`, `frontend/e2e/result.spec.ts` | integration | 미작성 |
| T-SEC-05, QG-09 | `tests/integration/test_headers_cookies.py`, `frontend/e2e/csp.spec.ts` | integration | 미작성 |
| T-SEC-06, AC-22, QG-06 | `backend/tests/test_offline_static.py`, `frontend/scripts/check-dist-urls.mjs`, `tools/offline/run.sh` + `frontend/e2e/global-teardown.ts` (compose 대신 네트워크 네임스페이스) | 단위 / 오프라인 | 작성·통과 |
| T-SEC-08, QG-07 | `tests/unit/test_logging.py`, `tests/integration/test_canary_logs.py` | 단위 / integration | 미작성 |
| T-JOB-* 나머지, AC-21 잠금 | `tests/test_postings_apply.py`(확장) | integration | 일부 구현(4.1) |
| T-RES-* 나머지, AC-06 나머지 | `tests/test_postings_apply.py` 또는 `tests/test_resume_upload.py`, PDF 생성은 `tests/fixtures.py` | integration | 일부 구현(4.1) |
| T-QST-*, AC-07 | `tests/unit/test_question_compose.py`, `tests/models/test_resume_questions.py` | 단위 / models | 미작성 |
| T-INT-*, AC-08~14 | `tests/unit/test_turn_clock.py`, `tests/rtc/test_gate_and_captions.py`, `tests/integration/test_interview_api.py`, `frontend/e2e/interview.spec.ts` | 단위 / integration / rtc | 미작성 |
| T-COD-*, AC-15, SBX-01~14, QG-05 | `runner/tests/test_sbx.py`, `tests/integration/test_coding.py` | docker / integration | 미작성 |
| T-EVL-*, AC-16~18, QG-03·04 | `tests/unit/test_quotes.py`, `tests/unit/test_scoring.py`, `tests/integration/test_eval_jobs.py` | 단위 / integration | 미작성 |
| T-ADM-*, AC-20 | `tests/integration/test_recruiter.py` | integration | 미작성 |
| T-STATE-7.1~7.4 | `tests/integration/test_state_machine.py` | integration | 미작성 |
| T-NFR-8.2 | `tools/latency_report.py`(측정, 합격 판정 아님) | nightly | 미작성 |

## 5. 인수 기준 AC-01~AC-22 테스트 설계

자동화: ◎ 완전 자동 / ○ 자동 + 일부 수동 / △ 반자동(실모델·측정) / × 수동. 기대값은 통합 결정(C·T 번호)을 반영했다.

| AC | 계층 | 사전 조건 | 절차 | 기대 결과 | 자동화 |
|---|---|---|---|---|---|
| AC-01 | integration + E2E | 빈 DB | ① 정상 가입 ② 필수 칸 하나씩 빼기 ③ 연락처 경계(`01012345678`, `010-1234-567`) ④ 본문에 birth_date·gender·role 추가 ⑤ 스키마 조회 ⑥ 가입 화면 DOM | ① 201 ②③ 422 칸 위치(입력값 미반사) ④ 422, 담당자 생성 없음 ⑤ 생년월일·성별·사진 칼럼 없음 ⑥ 해당 입력란 없음 | ◎ |
| AC-02 | integration + E2E | CLI로 X사 담당자 X1 발급 | ① 가입 API로 담당자 생성 시도 ② X1 로그인(역할 담당자 / 지원자 선택) ③ X1이 공개 공고 상세 열기(T21) ④ [지원하기] 클릭 ⑤ X1 세션으로 `/api/candidate/*` 전 경로 | ① 지원자만 생성 ② 담당자 200, 지원자 선택은 USR-04와 같은 401 ③ 200(`PublicPosting`) ④ 403 `ROLE_FORBIDDEN` + "담당자 계정으로는 지원자 기능을 사용할 수 없습니다." 지원 행 없음 ⑤ 전부 403 | ◎ |
| AC-03 | integration(IDOR 매트릭스) | 6절 픽스처 | 6절 생성 테스트 전체 | 다른 회사 리소스 전부 404(없는 ID와 본문·헤더 동일), 거부된 변경 뒤 DB 불변, 목록·통계에 다른 회사 카나리아 0건 | ◎ |
| AC-04 | integration + DB | 지원자 A, OPEN 공고 | ① 순차 2회 지원 ② 같은 요청 10개 동시 ③ DB 직접 중복 INSERT | ① 두 번째 409 `ALREADY_APPLIED` ② 성공 1건, 행 1개 ③ 유일 제약 위반 | ◎ |
| AC-05 | integration + E2E | 동의 문서 `consent-v0-draft` | ① 동의 없이 지원 ② 필수 항목 하나 거부 ③ 전부 동의 ④ E2E [동의하지 않음] | ① 4xx ② 거부, 단계 진행 없음 ③ 문구 버전·항목별 여부·서버 시각·사용자 기록 + 감사 `CONSENT_RECORD` ④ 공고 상세로 이동 | ◎ |
| AC-06 | integration + E2E | PDF 픽스처(2절) | 정상, PNG를 `.pdf`로 바꾼 파일, 잘린 파일, 10,485,760 / 10,485,761바이트, 암호화, 이미지만 있는 PDF, 51쪽, 압축 폭탄형, 경로 조작 파일명 업로드 → 정상 PDF 추출 결과 수정·저장 | 사유별 코드(413/415/422)와 화면 문구, 폭탄형·51쪽은 20초 안에 거부(앱 응답 지연 없음), 이미지 PDF는 "직접 입력" 안내, 저장 이름은 UUID, 표준 항목은 원문 부분 문자열만 채워지고 수정 가능 | ◎ |
| AC-07 | 단위 + models | 이력서 픽스처 10건 | ① 근거 문장이 스냅샷에 없는 질문 → 은행 대체(OQ-37) ② 실모델로 픽스처마다 본질문 구성 | ① `BANK_FALLBACK` ② 이력서 질문 2개 모두 근거가 스냅샷 부분 문자열(코드 판정), 질문 본문이 근거 내용어 포함 + 표본 사람 확인 | △ |
| AC-08 | integration + E2E(가짜 장치) | 지원 완료, PREPARING | 마이크 거부·카메라 거부·장치 없음·WebRTC 실패(서버 offer 거부)를 하나씩 / 점검 연결이 슬롯 없이 PREPARING에서 성립하는지(C4) / 점검 연결 5개 동시 / 점검 기록 없이 start 직접 호출 | 각 경우 [면접 시작] 비활성과 안내, 점검 연결 4개까지 성립·5번째 거부, start 409 `PRECHECK_REQUIRED`. 카메라 결과는 클라이언트 신고값(07 RE-14 수용) | ○ |
| AC-09 | 단위 + rtc | 합성 WAV: 질문 송출 중 "사과", 마이크 열림 뒤 "바나나" | ① 단위: `tts_ended_at + 0.3초` 이전 타임스탬프 프레임 공급 ② rtc: 서버 TTS 트랙 송출 중부터 클라이언트 송신 ③ 수동: 음성·자막 동시 제시 | ① 폐기 ② 확정 텍스트에 "사과" 없음, "바나나" 있음 ③ 확인 | ○ |
| AC-10 | rtc | 중간에 5초 침묵이 있는 WAV | 송신 중 `caption_partial` 수신 시각 기록 → 침묵 중 확정 여부 → `POST .../turns/{turn_id}/done` | 침묵만으로 확정 안 됨, 부분 자막 수신(지연은 8.2 대비 기록만), 완료 후 `answer_finalized` 확정 텍스트 | ◎ |
| AC-11 | 단위(가짜 시계) + E2E | 가짜 시계 | 본질문 60초·꼬리 45초 경과, 남은 10초, 무발화, 마감 + 유예 1.0초(T9) 뒤 오디오·완료 명령, 클라이언트 시각 조작 | 경고 이벤트 1회, 만료 시 그때까지 내용으로 확정, 무발화 '답변 없음', 유예 뒤 입력 무시, 클라이언트 시각 영향 없음. 실시간 60초 1회는 nightly. (구현 기본값 1.5초는 07 G-01) | ◎ |
| AC-12 | 단위(속성 기반) + E2E | 가짜 LLM이 매번 꼬리질문 요구 | 무작위 답변 시퀀스 1,000개로 정책 실행 / 판단 5초 초과 주입 / E2E 진행률 | 본질문당 ≤1, 전체 ≤4(DB CHECK와 일치), 5초 초과 시 건너뜀, 화면 "3 / 10"과 같은 번호의 "꼬리질문" | ◎ |
| AC-13 | integration + rtc + E2E | Q1~Q3 확정 | Q4 답변 중 강제 끊기·새로고침 → 상태 조회 → 재진입 / 끊기기 전 turn의 `done` 재전송 / 같은 문항 3번 끊기 | 현재 Q4 새 `turn_id`로 처음부터, Q1~Q3 답변 해시 불변, 이전 turn 명령 409 `STALE_TURN`(C3), 재출제 2회 뒤에는 서버가 텍스트 모드 허용(T11·T12), 리포트에 재출제 횟수 | ◎ |
| AC-14 | integration(동시성) | 상한 2 | 지원자 3명 동시 start / 1명 CODING 진입 후 재요청 / 하트비트 끊긴 INTERRUPTED 120초 뒤 | 정확히 2건 성공, 세 번째는 INT-13 문구 그대로·상태 READY, CODING 진입 시 슬롯 반환(T10)되어 시작 가능, 유예 뒤 슬롯 회수 | ◎ |
| AC-15 | integration + docker | SBX 통과, 코딩 켠·끈 공고 | ① 끈 공고 코딩 API ② 면접 중 코딩 API ③ [실행] 정상·예외 코드 ④ 응시 흐름의 지원자 응답 전체에서 비공개 테스트 카나리아 검색 ⑤ 파일·환경을 훑어 카나리아를 출력하는 코드로 채점, 하네스 조작 코드 ⑥ 가짜 시계로 마감 경과(브라우저 닫힘) ⑦ 플래그 끔·SBX 실패 | ① 404/409 ② 409 ③ "정상 작동"/"비정상 작동" + 출력 ④ 0건 ⑤ 비공개 통과 0, runner 요청에 기대 출력 없음(C8) ⑥ 마지막 저장본 자동 제출·채점 ⑦ 코딩 켠 공고 면접 시작 차단(T13), [실행] 503, 제출 코드는 채점 대기 | ◎ |
| AC-16 | 단위 + 가짜 LLM | ADR-0008 규칙 | 인용 표: 정확 일치, LLM이 넣은 공백, 앞뒤 문장부호, 질문 문장 인용, 다른 문항 인용, 8자 미만, **위조 7쌍**(3.5배→35배, C→C++, C→C#, 2~3명→23명, 1:1→11, 10%→10, 2,3명→23명) / 가짜 LLM: 위조→위조, 위조→정상 / 무응답·짧은 답변 | 표대로 판정(위조 7쌍 전부 거부), 위조 2회는 '근거 부족' 1점, 위조→정상은 채택, 저장 인용 = `eval_text[quote_start:quote_end]`, 무응답·짧은 답변은 LLM 호출 없음, 모든 OK 점수에 인용 | ◎ |
| AC-17 | 단위 + integration | 손계산 픽스처(꼬리 평균, 근거 부족·답변 없음 따로 계산, 코딩 켬/끔 기본 가중치) | 서버 계산값과 테스트 쪽 독립 오라클(`fractions.Fraction`) 비교, 반올림 경계(x.xx5), 무작위 1,000건, 가짜 LLM이 total을 보내는 경우 | 항목 점수 소수 둘째 자리 HALF_UP 저장 → 저장값으로 총점(T18)이 오라클과 일치, LLM total은 형식 오류 | ◎ |
| AC-18 | integration(중지·재기동) | 가짜 또는 실제 LLM | ① LLM 중지 상태로 제출 ② app·worker 재기동 ③ LLM 기동 ④ 평가 중 worker 강제 종료 ⑤ 같은 작업을 worker 2개가 잡도록 임대 강제 만료 | ① PENDING, 시도 횟수 0 유지(OQ-23, T16) ② 작업 보존 ③ 조작 없이 DONE ④ 임대 만료 뒤 재실행 ⑤ 리포트 1건(펜싱), 지원자 화면에 재응시 요구 없음 | ◎ |
| AC-19 | integration + E2E | 메모·가중치·커스텀 질문·인용·총점에 카나리아, 결정 전·후 | 지원자 세션이 받는 **모든 HTTP 응답(오류 포함)과 데이터채널 메시지** 수집 | 결정 전 점수 키 없음, 결정 후 키 집합 = 허용목록(항목별 점수, 강점·개선점, 코딩 "모두 맞힘/못맞힘", OQ-24), 금지 키(total, weight, quote, memo, note, hidden, rubric, reason, anchor…) 재귀 0건, 카나리아 0건, 공고 응답은 `PublicPosting`만 | ◎ |
| AC-20 | integration | DONE 건, RUNNING 건 | RUNNING 건 결정 / 합격 저장 → 불합격 변경 → 검토 전 복귀 시도 / Y사 담당자 결정 / worker 롤로 `decision_status` 갱신 | 409 `EVAL_NOT_DONE` / 이력 2행(누가·언제·이전→이후)·감사 `DECISION_SET`·지원자 화면 즉시 반영, 검토 전 복귀 거부(T24) / 404 / 권한 오류(P-8) | ◎ |
| AC-21 | integration | 코딩 켠·끈 공고 | 가중치 합 99·101·100, 음수, 끈 공고에 코딩 항목 / 첫 INTERVIEWING 진입(OQ-05) 뒤 가중치·코딩 설정 변경 / 진입과 변경 동시 | 100만 저장(DB CHECK 이중), 422 / 409 `SETTINGS_LOCKED`(트리거 AP003) / 둘 중 하나만 성공, 진입 뒤 변경 반영 0건 | ◎ |
| AC-22 | 오프라인 통합 + 정적 + 수동 | 이미지·모델·브라우저 사전 반입 | `offline-test` 프로필(내부망 + 질의 기록 DNS)에서 지원→이력서→점검→면접(합성 음성)→코딩→평가→리포트→결정→결과 E2E / 07 12절 정적 검사 / 시연 장비 네트워크를 실제로 끊고 수동 리허설 | 전 흐름 성공, 외부 이름 질의·외부 연결 0건, ICE 수집 1초 미만, 정적 위반 0건 | ○ |

## 6. IDOR 매트릭스 생성 방식 (AC-03, QG-01)

**픽스처**: 회사 X·Y, 담당자 X1·X2(X사)·Y1(Y사), 지원자 A(X사 공고와 Y사 공고 **모두** 지원)·B, 공고 X-OPEN·X-DRAFT·Y-OPEN, 각 지원 건의 면접 turn·답변·리포트·메모·결정·코딩 제출·이력서 파일·추출 결과.

**생성 절차**

1. `create_app(env="test")`의 `app.routes`에서 경로·메서드·정책 메타데이터(07 5.2)를 수집한다. 정책이 없으면 그 자체로 실패.
2. 경로 매개변수마다 "소유 리소스 ID", "다른 회사/다른 지원자 리소스 ID", "존재하지 않는 UUID"를 채운다.
3. 행위자 6종(비로그인, A, B, X1, X2, Y1) × 라우트 × ID 종류로 매개변수화하고, 상태 변경 메서드는 유효한 CSRF 토큰·Origin을 붙인다(인가만 시험하기 위해).
4. 기대값은 아래 규칙표로 계산한다. 상태 조건(예: CODING이 아님)으로 409가 정당한 경우는 "2xx 또는 409"를 허용하되 다른 회사·다른 소유자는 409가 아니라 404여야 한다.

| 정책 | 비로그인 | 소유 지원자 | 다른 지원자 | 같은 회사 담당자 | 다른 회사 담당자 |
|---|---|---|---|---|---|
| candidate / owner | 401 | 2xx·409 | 404 | 403 | 403 |
| recruiter / company | 401 | 403 | 403 | 2xx·409 | 404 |
| any_user / 공개 공고(OPEN) | 401 | 200 | 200 | 200(T21) | 200 |
| any_user / 공개 API로 DRAFT 공고 | 401 | 404 | 404 | 404(담당자는 담당자 API 사용) | 404 |
| recruiter / company 집계(통계) | 401 | 403 | 403 | 200, X사 집계만 | 200, Y사 집계만 |
| public(가입·로그인·헬스) | 2xx·4xx(입력) | — | — | — | — |
| internal(runner·ai) | 외부 경로 없음, 직접 호출 시 토큰 없으면 401 | | | | |
| test | 운영 프로필 라우트 목록에 없음 | | | | |

**판정 항목**

- 상태 코드가 기대값과 같다.
- 404 응답의 본문·헤더가 "존재하지 않는 UUID"의 404와 같다(`request_id` 값만 다름).
- 거부된 변경 요청 전후로 관련 테이블 스냅샷(행 수·해시)이 같다.
- 목록·검색·통계 응답에 다른 회사 카나리아 제목·이름이 없다.
- 포함 경로: WebRTC 시그널링 `POST /rtc/v1/sessions`, turn 명령 `.../turns/{turn_id}/*`, 메모 `PATCH·DELETE /api/recruiter/notes/{id}`, 이력서 추출 결과, 원본 PDF, `GET /api/system/rtc-config`.
- 출력: `var/reports/idor-<날짜>.csv`(행위자·라우트·ID 종류·기대·실제). 실패 셀이 하나라도 있으면 QG-01 실패.

## 7. 품질 게이트

### 7.1 실행 단계 (원격 CI 없음 → 로컬 스크립트)

| 단계 | 명령(계획) | 시점 | 소요 목표 |
|---|---|---|---|
| S0 | `pre-commit`(gitleaks, ruff, ESLint 변경분) | 커밋마다 | < 30초 |
| S1 | `scripts/check.ps1 -Stage fast` | 변경마다(구현 중 수시) | < 3분 |
| S2 | `scripts/check.ps1 -Stage full` | 기능 완료·단계 종료·추적표 갱신 전 | < 20분 |
| S3 | `scripts/check.ps1 -Stage nightly` | 매일(개발 장비 야간) | < 2시간 |
| S4 | `scripts/check.sh release` | 출시 전, **운영 호스트**에서 | 반나절 |

Linux 운영 호스트용 `check.sh`는 같은 단계 정의를 쓴다. 결과는 `var/reports/`에 날짜별로 남기고, 실패한 게이트가 있으면 해당 요구사항을 "검증 완료"로 올리지 않는다.

### 7.2 게이트 목록

| ID | 게이트 | 기계적 확인 | 단계 |
|---|---|---|---|
| QG-01 | 권한·격리 | 정책 미선언 라우트 0, 운영 프로필 라우트에 `/api/test/*`·`/api/docs`·`/api/openapi.json` 0, IDOR 매트릭스 전 셀 통과, semgrep: 담당자 모듈에서 범위 헬퍼 밖 ORM 조회·`text(` 0 | S1(인벤토리·정적), S2(매트릭스) |
| QG-02 | 지원자 응답 누출 | 지원자 세션의 모든 HTTP 응답·데이터채널 메시지: 키 허용목록 정확 일치 + 금지 키 재귀 검사 + 카나리아 0, 지원자 OpenAPI 스키마에도 같은 검사 | S2 |
| QG-03 | 점수·인용 | AC-16·17, 속성 테스트, 독립 오라클. 선택: mutmut으로 점수 모듈 변이 생존 확인 | S1 |
| QG-04 | 작업 중복·장애 복구 | AC-18, 펜싱(늦은 worker 반영 0행), 제출 트랜잭션 중간 예외 주입 시 상태·작업 모두 없음, 중복 요청 멱등 | S2 |
| QG-05 | 샌드박스 경계 | SBX-01~14 전부 통과, 플래그 기본 false, 비활성 시 503·시작 차단, app·worker 컨테이너에 Docker 소켓 없음, runner 요청에 기대 출력 키 없음 | S2(개발 검증), S3(매일), **S4(운영 런타임)** |
| QG-06 | 외부 API 부재 | 엔드포인트 허용목록 단위 테스트, 의존성 거부목록, 소스·번들 URL 검사, AC-22 이그레스 차단 E2E | S1(정적), S3·S4(E2E) |
| QG-07 | 로그 비밀정보 | 카나리아 E2E 후 모든 서비스 로그·`audit_logs.detail`·`jobs.payload` 0건, 로그 필터 단위 테스트, 설정 repr에 비밀 없음, 운영 기동 명령에 `--no-access-log` | S1(단위), S3(E2E) |
| QG-08 | 비밀정보 저장소 | gitleaks 0(첫 푸시 전 전체 이력 포함), 추적 파일에 `.env` 없음, 운영 프로필 기본 비밀로 기동 실패 | S0, S4 |
| QG-09 | 헤더·쿠키·CORS | 07 4절 표의 응답 종류별 헤더 값, 외부 Origin 사전 요청에 ACAO 없음, CSRF 토큰 없음·Origin 불일치 403, 쿠키 속성, 운영 빌드 전 화면 `securitypolicyviolation` 0 | S2, S3(E2E) |
| QG-10 | 인증·세션 | USR-04 응답 동일성(상태·본문·헤더), 스로틀(존재 무관 동일 429), 세션 회전·로그아웃 후 401·가짜 시계 2시간 만료. 타이밍 시험: 없는 계정·틀린 비밀번호·역할 불일치 각 200회 무작위 순서, 중앙값 차이 < 큰 쪽 중앙값의 10%(제안) | S2, 타이밍은 S3 |
| QG-11 | 정적 분석 | ruff(S 규칙 포함)·semgrep(프로젝트 규칙)·ESLint(`react/no-danger`, `no-eval`, 모듈 경계)·hadolint 경고 0(예외는 사유 주석) | S1 |
| QG-12 | 의존성 취약점 | pip-audit·npm audit·trivy(이미지) 높음 이상 0 또는 예외 기록, 잠금 파일 해시 고정 | 주 1회(온라인 개발 장비), S4 |
| QG-13 | 수집 제한·비식별 | 금지 칼럼 없음, 평가 프롬프트 캡처에 이름·이메일·전화 카나리아 없음, 반사실 바이트 동일 시험 | S1 |
| QG-14 | AI 정보 흐름 | 꼬리질문·지원자 피드백·코드 리뷰 프롬프트 캡처에 점수·메모·가중치·비공개 테스트 카나리아 없음, 추천 문구 검사기 표, 질문은행에 이력서·꼬리질문 없음, Y사 질문 세트에 X사 카나리아 없음 | S1·S2 |

### 7.3 도구와 오프라인 한계

| 도구 | 오프라인 | 비고 |
|---|---|---|
| ruff, semgrep(로컬 규칙 파일, `--metrics=off`), ESLint, hadolint, gitleaks, mypy/pyright | 가능 | semgrep 레지스트리 규칙은 다운로드가 필요하므로 프로젝트 규칙만 쓴다 |
| pip-audit, npm audit | 불가(원격 DB 조회) | 인터넷 되는 개발 장비에서만 |
| osv-scanner, trivy | DB 사전 다운로드 시 가능 | DB 반입 날짜를 결과에 기록 |
| pytest, Vitest, Playwright | 가능 | 브라우저 바이너리·패키지는 사전 반입 |

오프라인 운영에서는 취약점 DB가 반입 시점에 고정된다. 그래서 (1) 릴리스마다 인터넷 되는 장비에서 SBOM(CycloneDX) 기준으로 다시 검사해 결과를 릴리스 기록에 남기고, (2) 운영 서버에서는 패키지를 직접 갱신하지 않고 검사한 이미지 교체로만 바꾼다.

## 8. 실측 항목과 미실행 항목

**8.1 실측 완료(조정자 스파이크, 2026-10-26, `prototypes/`)** — 결과는 해당 ADR·TRD가 인용한다.

| 항목 | 결과 요지 | 근거 파일 |
|---|---|---|
| 샌드박스 강화 옵션(Docker Desktop·runc) | 12/12 차단, 기동 약 0.45초 → **개발 검증만** | `results-sandbox-escape.txt` |
| 인용 정규화 규칙 | 공백 제거 + 앞뒤 문장부호: 실제 47/48, 위조 0/7 | `results-quote-rules.txt` |
| aiortc ICE 기본값 | 공용 STUN 사용(10.2초) → `iceServers=[]` 0.17초 | `results-rtc-loopback.txt` |
| 브라우저↔aiortc 게이트·자막 | 게이트 이전 발화 폐기, 자막 1.46~1.70초 | `README.md`(S-1) |
| LLM·Ollama·STT·TTS | 지연·VRAM·재적재·취소 | `results-llm-*.txt`, `results-ollama-scheduling.txt`, `results-stt-*.txt`, `results-tts-supertonic.txt` |

**8.2 이 장비에서 실측 예정**(구현과 함께, 결과는 측정값으로 기록)

| 항목 | 방법 | 판정 기준 |
|---|---|---|
| Argon2id 1회 검증 시간·동시 4개 메모리 | 단위 벤치 | 0.15~0.5초(07 3.1) |
| SBX 미실측 항목(01, 02 일부, 03 IP, 04 tmpfs, 07 sleep, 08 RSS·디스크, 09 mountinfo, 10~14) | `runner/tests/test_sbx.py` | 07 13.4 기대값 |
| S-10 LLM 생성 중 GPU STT 지연 | 600토큰 스트리밍 중 turbo 13.5초 구간 | 자막 p95 ≤ 2초(8.2) |
| S-3 장시간 상주 | 임베딩·평가·RT 혼합 1시간 | 재적재 0회 |
| 동시 응시 2명 모의 부하 | 합성 음성 2세션 + 평가 worker + 채점 | M2·M3·M4(C 3.2) 측정, 상한 확정(OQ-15) |
| WebRTC 30분 연결·재연결 | aiortc 클라이언트 2세션 | 끊김 0, 재연결 성공 |
| 리포트 생성 시간(EVL-05 포함) | 14답변 제출→DONE | 3분/10분(8.2) |
| CSP `securitypolicyviolation` | 운영 빌드 전 화면 순회 | 0건 또는 nonce 적용(07 4절) |
| PDF 응답 `CSP: sandbox`와 내장 뷰어 | 크롬·엣지 | 렌더되면 유지, 아니면 attachment |
| USR-04 타이밍 | QG-10 | 10% 미만 |
| 백업·복원 리허설 | 10절, 개발 compose | 복원 후 스모크 통과 |

**8.3 장비 한계로 이 장비에서 실행하지 않는 항목**(출시 판정 전에 운영 환경에서 실행, 그 전에는 "미검증"으로 표시)

| 항목 | 이유 | 실행 위치·시점 |
|---|---|---|
| 운영 Linux 샌드박스(runsc 또는 샌드박스 전용 rootless 데몬)에서 SBX-01~14 | 이 장비는 Docker Desktop(WSL2)·runc, gVisor 없음 | 운영 호스트, S4 |
| 운영 호스트에서 다른 PC 브라우저로 S-1 | 단일 장비, 운영은 host 네트워크·방화벽 | 운영 호스트 + 사내망 PC, S4 |
| GPU 장시간(수 시간) 안정성·발열 | 데스크톱 겸용 장비, 상시 0.4GB 점유 | 운영 GPU 서버 |
| 다중 사용자 부하(여러 PC, 상한 3 이상) | 단일 장비 모의 부하만 가능(R-19) | 운영 환경 시범 |
| 외부망 응시·TURN(OQ-18) | NAT 너머 환경 없음 | 범위 밖, 구성안만 |
| 사람이 실제 마이크로 말하는 E2E | 자동화 불가(R-20) | 시연 전 수동 체크리스트 |
| 헤드리스 Linux VRAM 예산 | 운영 서버 미보유 | 운영 GPU 서버 |

## 9. 관측성

- **로그**: 모든 프로세스가 stdout JSON 한 줄(07 10.2 허용 키). Docker `local` 드라이버 10MB×5 순환. 샌드박스 컨테이너만 `--log-driver none`.
- **지연 이벤트**(값은 `latency_ms`·`duration_ms` 필드, 집계는 `tools/latency_report.py`):

| 이벤트 | 구간 | 대응 목표(8.2) |
|---|---|---|
| `tts.first_audio` | 질문 제시 → 첫 오디오 프레임 송출 | 본질문 1초(M1) |
| `stt.segment` | 구간 마지막 프레임 → `caption_partial` 송신 | 2초(M2) |
| `turn.answer_to_next` | 답변 확정 → 다음 질문 제시 | 5초(M3) |
| `rt.followup` | 꼬리질문 판단(대기 + 실행, 결과 ASK/SKIP/TIMEOUT) | 5초 마감(6.2) |
| `eval.application` | SUBMITTED → 평가 DONE | 3분/10분(M4) |
| `sandbox.run` | 컨테이너 생성 → 종료(상태 포함) | 기록만 |
| `loop.lag` | 이벤트 루프 지연 50ms 초과 경고 | 기록만 |

- **상태 확인**: `GET /api/healthz`(생존, [구현]), `GET /api/system/ai-health`(LLM·STT·TTS, INT-14), runner `GET /v1/health`(07 13.5), `ai` `GET /v1/health`. 운영자 CLI `jobs-status`(대기·실행·실패 작업, ON_HOLD 건수).
- **경보 대상**(운영자가 매일 확인, 별도 모니터링 스택은 원문 외라 MVP 제외): 샌드박스 FAILED, ON_HOLD 증가, 큐 지연 10분 초과, 디스크 여유 15% 미만, 백업 실패, 감사 일일 요약 파일 누락.

## 10. 백업·복구

| 대상 | 방법 | 주기·보관(제안) | 비고 |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc`(소유자 롤), 보관 삭제 작업(03:00 KST) 뒤 03:30 | 일 7개 + 주 4개 | 개인정보 포함 → 백업 디렉터리 0700, 운영 디스크 암호화 여부는 운영 결정 |
| 이력서 파일 볼륨 | DB 덤프 직후 tar 스냅숏(같은 시각 기준) | DB와 같음 | 파일 → DB 순서 불일치는 `file_gc`가 고아 정리 |
| 답변 음성 볼륨 | 같음 | 30일 보관 데이터라 백업도 같은 주기 | |
| TTS 캐시·모델 | 백업하지 않음 | — | TTS는 재생성, 모델은 반입 묶음 + SHA-256 목록으로 복원 |
| 설정·비밀 | 운영 비밀 파일은 별도 보관(운영자 책임) | 변경 시 | 저장소에 넣지 않음 |

- **삭제와 백업**: 보관 기간이 지나 지운 데이터·탈퇴자 데이터는 백업 보관 기간(최대 약 5주) 동안 백업에 남는다. 백업 보관 기간은 법률 확인 대상이다(X-13, OQ-04). 복원 후에는 `retention_purge`를 즉시 1회 돌려 기간이 지난 데이터를 다시 지운다.
- **복원 리허설**(출시 전 1회 + 분기 1회, 개발 장비에서는 개발 compose로): ① 새 PostgreSQL 컨테이너에 `pg_restore` ② `alembic current` = head ③ 주요 테이블 행 수와 덤프 시점 기록 비교 ④ 감사 일일 요약 파일과 행 수·해시 대조 ⑤ 파일 볼륨 복원 후 `storage_key` ↔ 파일 존재 대조 ⑥ `retention_purge` 1회 ⑦ 스모크(로그인, 리포트 조회, 원본 PDF 열기). 소요 시간을 기록한다.
- **목표(제안)**: RPO 24시간, RTO 2시간. 진행 중 면접은 복원 시점 이후 답변을 잃을 수 있으며 해당 지원 건은 INTERRUPTED로 재진입한다.

## 11. 배포·운영 절차 개요

**11.1 기동 순서**(T1의 7개 프로세스, 상세는 ADR-0010)

1. `postgres` → 헬스 확인.
2. `migrate`(1회성, 소유자 롤): 백업 → `alembic upgrade head`. 운영 데이터가 생긴 뒤에는 expand→migrate→contract 2단계.
3. `ollama` → 모델 digest가 반입 목록과 같은지 확인 → `keep_alive=-1`로 LLM 미리 적재(콜드 로드 약 40초), `num_ctx=8192` 고정.
4. `ai`(게이트웨이·STT·TTS·임베딩) → 웜업 → `/v1/health` 정상.
5. `runner` → SBX-01~14 자체 점검 → `VERIFIED`가 아니면 실행 기능은 꺼진 채로 둔다(07 13.5).
6. `worker` → 기동 직후 reaper 1회(임대 만료 작업 회수).
7. `app`(REST + 시그널링·미디어, host 네트워크, uvicorn `--proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log`).
8. `caddy`(HTTPS, SPA, 보안 헤더). 종료는 역순이며 `worker`는 임대를 반납하지 않고 끝나도 된다(만료 후 회수).

**11.2 운영 설정 점검표**(기동 검사 + 사람 확인): `AIHIRE_ENV=prod`, Secure 쿠키, 비기본 비밀(runner 토큰·DB·스로틀 키), `trusted_origins`가 운영 https 주소, AI·ICE 엔드포인트가 사설 주소, `AIHIRE_CODE_EXECUTION_ENABLED`는 운영 런타임 SBX 통과 뒤에만 true, OpenAPI·test 훅 없음, `HF_HUB_OFFLINE=1`, 방화벽 이그레스 차단·UDP는 사내 서브넷만.

**11.3 반입 묶음**: 이미지 tar(`docker save`, 다이제스트 목록), `models/` + `SHA256SUMS`(Ollama 모델, faster-whisper CT2, Supertonic), 프런트 `dist`(caddy 이미지 포함), Python·npm 잠금 파일과 SBOM. 반입 후 해시 검증 실패 시 기동하지 않는다.

**11.4 운영자 CLI**(웹 UI 없음, OQ-19, 모두 감사 `OPERATOR_CLI`): `create-company`, `create-recruiter`, `disable-user`(전 세션 무효화), `import-coding-problem`, `seed-question-bank`, `requeue-evaluation`, `jobs-status`, `purge --dry-run`. 답변·이력서 원문을 출력하는 명령은 없다(2.3).

**11.5 장애 대응**

| 상황 | 자동 동작 | 운영자 조치 |
|---|---|---|
| LLM 불가 | 새 면접 시작 차단(INT-14), 진행 중 면접은 꼬리질문 생략으로 계속, 평가 작업은 집지 않음(시도 미소모) | `ollama` 재기동·모델 적재 확인. 복구 후 자동 진행 |
| STT 불가 | 시작 차단. 면접 중 구간 실패는 '답변 없음'(INT-09) | `ai` 재기동, GPU 실패 시 CPU small 대체 설정 |
| TTS 불가 | 텍스트만 진행(INT-04, OQ-08) | `ai` 재기동 |
| 평가 보류(ON_HOLD) | 임시 점수 없음, 지원자 재응시 요구 없음 | 담당자 재평가(ADM-06) 또는 `requeue-evaluation <application_id>` |
| 샌드박스 FAILED·UNVERIFIED | 코딩 켠 공고 시작 차단, [실행] 503, 제출분 채점 대기 | 원인(이미지·데몬·런타임 변경) 확인 → runner 재기동으로 재점검 |
| 샌드박스 탈출 의심 | — | 플래그 false, runner·샌드박스 데몬 정지, 호스트 격리·재설치 판단, runner 토큰 교체, 감사 기록 |
| PostgreSQL 장애 | 면접 연결 종료 → INTERRUPTED | 복구 또는 10절 복원, 이후 재진입 |
| 디스크 부족 | 업로드·음성 저장 실패 | `file_gc`·로그 순환 확인, 볼륨 확장 |
| 계정 침해 의심 | — | `disable-user`(세션 전부 삭제), 비밀번호 재설정 절차(USR-08은 P2이므로 운영자 수동) |
