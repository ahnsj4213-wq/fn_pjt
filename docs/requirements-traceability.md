# PRD 요구사항 추적표

- 기준: `통합 PRD.pdf` v1.0 초안 (2026-10-26). 기능 ID·우선순위·수치·인수 기준은 PRD 그대로 옮겼다. 우선순위는 추출 텍스트가 아니라 PDF 페이지 이미지로 대조했다.
- 상태 값: **미착수 / 진행 중 / 구현 / 검증 완료 / 보류**. `구현`은 코드가 있으나 자동 테스트로 확인하지 않은 상태, `검증 완료`는 표의 테스트 ID가 실제로 실행되어 통과한 상태다.
- 테스트 ID 규칙: `T-<요구사항ID>`(단위·통합), `AC-xx`(인수 기준 테스트). 실제 테스트 함수와의 대응은 `docs/trd/08-testing-and-operations.md` 4절에 둔다.
- 도메인/API 칸의 경로는 `docs/trd/04-api-contract.md`의 확정 계약을 따른다. 구현 칸은 저장소 상대 경로다.
- 마지막 갱신: 2026-11-02. 아래 `미착수`로 남은 행 가운데 코드가 이미 있는 것이 많다(Phase 2~5 구현). 이번에 코드·테스트로 확인해 고친 행은 USR-08(기업회원 가입·운영자 인증), ADM-01~04·08, JOB-03·04·07·09, RES-07, RPT-01~03이다. 아래 11절은 화면정의서(pptx 78쪽)에서 새로 생긴 요구를 따로 적었다. 나머지 행은 아직 대조하지 않았으니 `미착수`를 구현 안 됨으로 읽지 말 것.

## 1. 기능 요구사항

### 5.1 사용자 관리 (USR)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| USR-01 | 역할(지원자/담당자) 선택 로그인, 이메일·비밀번호 형식 검사 | P0 | auth / `POST /api/auth/login` | backend `app/auth`, frontend `pages/Login` | T-USR-01 | 미착수 | 선택한 역할과 계정 역할이 다르면 USR-04와 같은 일반 오류로 응답 |
| USR-02 | 지원자 가입: 이름·이메일·비밀번호·확인·연락처(010-0000-0000) 필수, 생년월일·성별 없음 | P0 | auth / `POST /api/auth/signup` | `app/auth`, `pages/Signup` | T-USR-02, AC-01 | 미착수 | DB 스키마에 생년월일·성별 칼럼 자체를 두지 않는다 (8.5) |
| USR-03 | 담당자 계정은 운영자가 회사별 발급, 담당자 가입 없음 | P0 | auth / CLI `create-recruiter` | `app/cli.py` | T-USR-03, AC-02 | 미착수 | 가입 API는 role=candidate만 생성 |
| USR-08 | 기업회원 가입(화면정의서 SC-R01): 메일 인증번호, 회사 도메인 메일은 즉시 승인, 개인 메일은 승인 대기(공고 게시 차단) | P0 | auth / `POST /api/auth/company-signup[/code]`, `GET/POST /api/recruiter/company[/evidence]` | `app/auth/company_signup.py`, `CompanySignupModal.tsx`, `PendingBanner.tsx` | `test_company_signup.py`, `CompanySignup.test.tsx` | 구현 | 화면정의서가 USR-03(담당자 가입 없음)보다 우선(결정 기록). 운영자 승인 화면 SC-O01은 미구현, 당분간 CLI `approve-company` |
| USR-04 | 로그인 실패 문구로 계정 존재 여부 비노출 | P0 | auth / `POST /api/auth/login` | `app/auth` | T-USR-04 | 미착수 | 존재하지 않는 계정도 해시 검증을 수행해 응답 시간 차이도 줄인다 |
| USR-05 | 담당자 계정으로 지원자 기능 접근 차단·안내 | P0 | auth guard / 모든 `/api/candidate/*` | `app/auth/deps.py`, frontend 라우트 가드 | T-USR-05, AC-02 | 미착수 | 서버 403 + 화면 안내. 프런트 숨김만으로 대체하지 않음 (SEC-03) |
| USR-06 | 마지막 요청 후 2시간 세션 유지 | P1 | auth / 세션 미들웨어 | `app/auth/sessions.py` | T-USR-06 | 미착수 | 서버 세션 테이블에 슬라이딩 만료 |
| USR-07 | 회원 탈퇴와 계정·데이터 삭제 요청 | P1 | auth / `DELETE /api/candidate/me` | `app/retention/router.py`, `pages/candidate/Account` | T-USR-07 | 검증 완료 | 8.5: 탈퇴 시 보관 기간과 무관하게 삭제. 응시 중(PREPARING·INTERVIEWING·CODING)이면 409(OQ-12 잠정). `tests/test_retention.py`, `Account.test.tsx` (2026-10-28) |
| USR-08 | 아이디·비밀번호 찾기 | P2 | — | — | — | 미착수 | 메일 발송 인프라 필요(온프레미스 SMTP). P2 |

### 5.2 채용공고 (JOB)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| JOB-01 | 로그인 후 공고 목록·상세(제목·지원요건·우대·업무·정원·마감일·코딩테스트 여부). 정원은 표시만 | P0 | postings / `GET /api/postings`, `GET /api/postings/{id}` | `app/postings`, `pages/candidate/Postings*` | T-JOB-01 | 미착수 | 게시(OPEN) 공고만 노출. 정원으로 마감하지 않음 |
| JOB-02 | 같은 공고 1회 지원 | P0 | applications / `POST /api/candidate/postings/{id}/applications` | `app/applications`, DB `UNIQUE(posting_id, candidate_id)` | T-JOB-02, AC-04 | 미착수 | DB 제약조건 + API 409 |
| JOB-03 | 공고 작성·수정·임시저장·미리보기·게시·마감, 마감일 경과 시 자동 마감 | P0 | postings / `/api/recruiter/postings*` | `app/postings`, worker 주기 작업 | T-JOB-03 | 미착수 | 상태: DRAFT→OPEN→CLOSED (7.4). 자동 마감은 조회 시점 판정 + 주기 작업 |
| JOB-04 | ~~지원자 있는 공고는 삭제 불가, 마감만~~ → **변경(화면정의서 SC-R04)**: 지원자가 있어도 삭제할 수 있고 응시자 결과는 응시자 목록에 남는다 | P0 | postings / `DELETE /api/recruiter/postings/{id}` | `app/postings/service.py` `delete`(게시 중이면 먼저 마감, `deleted_at`), `Postings.tsx` | `test_postings_apply.py::test_job04_delete_keeps_the_applicants_results` | 검증 완료 | 화면정의서가 PRD보다 우선(2026-10-30). 응시자 데이터는 지우지 않는다 |
| JOB-05 | 공고별 코딩테스트 사용 여부, 문제·허용 언어·제한시간 지정 | P0 | postings / `PUT .../postings/{id}` | `app/postings`, `app/coding` | T-JOB-05 | 미착수 | 언어는 Python 1종 (X-10) |
| JOB-06 | 첫 응시 시작 후 코딩테스트 설정·가중치 잠금 | P0 | postings / 설정 변경 API 409 | `app/postings`, `app/interview` | T-JOB-06, AC-21 | 미착수 | 첫 응시 시작(=PREPARING 진입이 아니라 INTERVIEWING 진입) 정의는 OQ-05 |
| JOB-07 | 공고 목록 진행 중·마감·임시저장 탭, 공고별 지원자 수·응시 완료 수 | P0 | postings / `GET /api/recruiter/postings?status=` | `app/postings`, `pages/recruiter/Postings` | T-JOB-07 | 미착수 | |
| JOB-08 | 직무 필터·키워드 검색 | P1 | postings / `GET /api/postings?q=&job_family=` | `app/postings` | T-JOB-08 | 미착수 | |
| JOB-09 | 공고 템플릿 저장·불러오기(SC-R05: 이전 공고·기본 템플릿·저장한 템플릿·외부 파일 DOCX/PDF) | P2 | recruiter / `/api/recruiter/posting-templates`, `POST /api/recruiter/postings/import-file` | `app/recruiter/templates.py`, `TemplateModal.tsx`, `PostingForm.tsx` | `test_templates_inquiries.py`, `Inquiries.test.tsx` | 검증 완료 | HWP는 읽지 못하고 DOCX·PDF 저장을 안내한다. 마감일은 템플릿에 넣지 않는다 |
| JOB-10 | AI 공고 초안 자동 생성 | P2 | — | — | — | 미착수 | |

### 5.3 이력서 (RES)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| RES-01 | 지원 시 이력서 필수. 등록본 불러오기 / PDF 업로드 / 직접 작성 | P0 | resumes, applications | `app/resumes`, `pages/candidate/Apply` | T-RES-01 | 미착수 | |
| RES-02 | PDF만, 10MB 이하, 드래그 앤 드롭, 다른 형식·손상 파일 거부와 이유 안내 | P0 | resumes / `POST /api/candidate/resume-files` | `app/resumes/pdf.py` | T-RES-02, AC-06 | 미착수 | 서버에서 크기·매직바이트·파싱 가능 여부 재검사 |
| RES-03 | PDF 텍스트 추출 → 표준 항목(성명·학력·경력·프로젝트·기술 스택·자격증), 원문에 있는 내용만 | P0 | resumes | `app/resumes/extract.py` | T-RES-03, AC-06 | 미착수 | LLM 구조화 + 원문 부분 문자열 검증. 원문에 없는 값은 버림 |
| RES-04 | 스캔 PDF는 "직접 입력" 안내, OCR은 P2 | P0 | resumes | `app/resumes/router.py`, `app/resumes/ocr.py`, `app/resumes/ocr_extract.py`, `app/jobs/handlers.py` | T-RES-04, `tests/test_resume_ocr.py` | 구현 | 페이지별 글자 50자 미만이면 OCR(Tesseract, worker). 평균 신뢰도 0.5 미만·엔진 없음 → NO_TEXT 직접 입력 안내. 0.7 미만 줄이 들어간 항목은 확인 표시. PNG/JPG 사진도 허용. 원래 "OCR 흉내 금지"였으나 실제 OCR을 구현했고 화면정의서가 OCR을 요구하며 사용자가 팀원 로직 반영을 요청(2026-10-30). 실제 휴대폰 사진 미검증 |
| RES-05 | 추출 결과 확인·수정 | P0 | resumes / `PUT /api/candidate/resume` | `pages/candidate/ResumeEditor` | T-RES-05, AC-06 | 미착수 | |
| RES-06 | 직접 작성 최소 필수: 성명 + (학력 1건 또는 경력 1건) | P0 | resumes | `app/resumes/schemas.py` | T-RES-06 | 미착수 | |
| RES-07 | 마이페이지 등록본 관리, 지원 시점 스냅샷 고정, 등록본 자동 덮어쓰기 금지 | P0 | resumes, applications | `app/applications`, `app/resumes`, frontend `screens/candidate/MyResume.tsx` | T-RES-07, `tests/test_mypage.py`, `MyPage.test.tsx` | 진행 중 | 지원 건에 JSON 스냅샷 + 원본 PDF 사본 참조. 마이페이지 이력서(SC-A10)는 와이어프레임 양식(이름·희망 직무·최종 학력·경력·보유 기술·프로젝트·자기소개서·파일)으로 저장하고, 첨부 파일(PDF·DOCX·PNG·JPG)은 서버가 읽어 양식을 채운다. 희망 직무·경력 연차·자기소개서는 AI 입력에 넣지 않는다 |
| RES-08 | 담당자가 지원자 상세에서 원본 PDF와 표준 항목 이력서를 봄 | P0 (표준 항목 보기는 P1) | recruiter / `GET /api/recruiter/applications/{id}/resume-file` | `app/recruiter` | T-RES-08 | 미착수 | |
| RES-09 | 가입 직후 이력서 등록 팝업 | P2 | — | — | — | 미착수 | |

### 5.4 질문 생성 (QST)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| QST-01 | 질문은행(직무·유형별), 덜 쓴 질문부터, 부족하면 AI 생성 후 은행에 추가, 유사 질문 중복 저장 금지 | P0 | questions | `app/questions/bank.py` | T-QST-01 | 미착수 | 유사도는 로컬 임베딩 (ADR-0005). 생성 시 RAG 예시 3개(`app/questions/rag.py`, `tests/test_question_rag.py`) **ADR-0020: 동기·직무는 회사 자료 기반 생성 우선, 은행은 대체 경로로 순서 변경.** `app/questions/knowledge.py`, `tests/test_company_knowledge.py` |
| QST-02 | 면접 시작 전 본질문 10개 구성 (6.1) | P0 | questions / 준비 단계 작업 | `app/questions/compose.py` | T-QST-02 | 미착수 | 인성2·이력서2·기업2·직무4, 순서 고정 **2026-11-07 변경(ADR-0022, 사용자 결정): 시작 전에는 첫 질문만, 이후는 면접 중 답변을 반영해 한 문항씩 생성. `AIHIRE_QUESTION_MODE=upfront`로 원래 방식** **2026-11-09 ADR-0025: 기본값을 upfront로 되돌림(본질문·TTS를 면접 전 준비, 면접 중 LLM은 꼬리질문만). 지원자 수준별 난이도 배분과 질문 품질 코드 검사 추가 (`app/questions/level.py`, tests/test_question_level.py)** |
| QST-03 | 이력서 질문은 이력서에 실제 적힌 경험·프로젝트만 근거, 근거 없으면 버리고 은행 질문으로 | P0 | questions | `app/questions/resume_q.py` | T-QST-03, AC-07 | 미착수 | 근거 문장을 스냅샷 원문과 코드로 대조 ADR-0020: 근거를 `grounding.sources`(RESUME_SNAPSHOT)로 추적, 공고·회사 자료와 겹치는 항목 우선 |
| QST-04 | 공고별 커스텀 질문(1개), 비우면 자동 채움 | P1 | postings / 평가 설정 API | `app/postings` | T-QST-04 | 미착수 | ADM-05(P0)가 커스텀 질문 설정을 포함 → 우선순위 불일치 OQ-02 |
| QST-05 | 답변에 따른 꼬리질문 (6.2) | P0 | interview | `app/interview/followup.py` | T-QST-05, AC-12 | 미착수 | ADR-0020: 앞선 답변·회사 자료를 입력에 추가, 근거를 `grounding.sources`로 추적 |
| QST-06 | 출제 질문은 변경 불가 스냅샷 | P0 | questions / DB 트리거 | `migrations` | T-QST-06 | 미착수 | UPDATE 금지 트리거 |
| QST-07 | 생성 실패 시 1회 재시도 후 질문은행 대체 | P0 | questions | `app/questions/compose.py` | T-QST-07 | 미착수 | |
| QST-08 | JD에서 질문 후보 자동 추출·담당자 선택 | P2 | — | — | — | 미착수 | |

### 5.5 AI 면접 (INT)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| INT-01 | 환경 점검: 마이크(권한·장치·레벨), 카메라(권한·장치·영상), 모두 통과해야 시작, 이전 단계 복귀 가능 | P0 | interview / 프런트 | `pages/candidate/Precheck` | T-INT-01, AC-08 | 구현 | 서버에도 점검 통과 기록이 있어야 start 허용 2026-10-30 화면 SC-A04 `screens/candidate/apply/Check.tsx`(카메라·마이크 레벨·스피커 테스트음, 장치 선택) 구현, vitest·스크린샷 확인(E2E 미실행). |
| INT-02 | 스피커 테스트 음성 재생 | P1 | 프런트 | `pages/candidate/Precheck` | T-INT-02 | 미착수 | |
| INT-03 | 시작 전 안내(방식·문항 수·제한시간·코딩 여부·음량) | P0 | 프런트 | `pages/candidate/Guide` | T-INT-03 | 미착수 | |
| INT-04 | 질문 텍스트(자막)+TTS 동시 제시, 아바타 없음, TTS 실패 시 텍스트만 | P0 | interview, voice | `app/voice/tts.py`, `pages/candidate/Interview` | T-INT-04, AC-09 | 미착수 | |
| INT-05 | WebRTC 실시간 음성, TTS 종료 후 마이크 열림, 끊김 구간 STT, [답변 완료] 또는 시간 만료로 종료, 침묵으로 종료 안 함, 반이중 | P0 | interview / WebRTC 시그널링 | `app/voice/rtc.py`, `app/interview/runtime.py` | T-INT-05, AC-09, AC-10 | 미착수 | 마이크 열림 이전 프레임은 서버가 폐기 (프런트 차단만 믿지 않음) |
| INT-06 | 최대 답변 시간 본질문 60초·꼬리 45초, 10초 경고, 만료 시 그때까지 내용으로 확정, 무발화는 '답변 없음', 카운트다운 없음 | P0 | interview | `app/interview/runtime.py` | T-INT-06, AC-11 | 미착수 | 서버 시계 기준 마감 + 네트워크 유예 |
| INT-07 | 진행률 "3 / 10", 꼬리질문은 같은 번호에 표시 | P0 | 프런트 | `pages/candidate/Interview` | T-INT-07, AC-12 | 미착수 | |
| INT-08 | 본인 카메라 화면 표시, 녹화·저장 안 함 | P0 | 프런트 | `pages/candidate/Interview` | T-INT-08 | 미착수 | 비디오 트랙은 서버로 보내지 않음 (로컬 미리보기만) |
| INT-09 | 답변 음성·STT 텍스트 문항마다 즉시 저장, STT 실패 시 '답변 없음'으로 저장 후 진행 | P0 | interview | `app/interview/runtime.py` | T-INT-09 | 미착수 | |
| INT-10 | "잘 모르겠습니다" 등 내용 없는 답변은 반복하지 않고 다음 질문 | P0 | interview | `app/interview/followup.py` | T-INT-10 | 미착수 | 규칙 기반 판정 → 꼬리질문 생략 |
| INT-11 | 끊김·새로고침 후 재진입 시 끊긴 문항부터, 저장 답변 유지 | P0 | interview / `GET .../interview/state` | `app/applications/lifecycle.py` `end_left`, `app/voice/router.py` | T-INT-11, AC-13 | 구현 | **ADR-0024(사용자 지시)로 변경**: 재진입 없음. 도중에 나가면 종료, 저장 답변은 유지·평가 |
| INT-12 | 지원 건당 1회 응시 | P0 | interview | DB 상태 전이 + 유일 제약 | T-INT-12 | 미착수 | SUBMITTED·EXPIRED에서 재응시 불가 |
| INT-13 | 동시 응시 상한, 초과 시 지정 문구로 시작 거부, 마이페이지에서 나중에 시작 | P0 | interview / `POST .../interview/start` | `app/interview/slots.py` | T-INT-13, AC-14 | 미착수 | 문구: "이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요." |
| INT-14 | 시작 전 AI 서버(LLM·STT·TTS) 상태 확인, 불가 시 시작 차단 | P0 | `GET /api/system/ai-health` | `app/ai/health.py` | T-INT-14 | 미착수 | TTS 실패는 INT-04로 텍스트 진행 가능 → TTS 장애 시 차단 여부 OQ-08 |
| INT-15 | 실시간 자막, 종료 후 확정 텍스트로 교체 | P0 | interview / WebRTC 데이터채널 | `app/voice/stt_stream.py` | T-INT-15, AC-10 | 미착수 | 목표: 말 끊긴 뒤 2초 내 (8.2) |
| INT-16 | 화면 이탈 경고, 횟수·시간 기록, 리포트 표시, 종료 안 함 | P2 | interview / `POST .../proctor-events` | `app/interview` | T-INT-16 | 구현 | 2026-10-30: 탭 이탈(LEAVE/RETURN)과 SC-A06 시선 이탈(브라우저 얼굴 감지, 1.5초 이탈 시 경고 1회, `GAZE_AWAY` 기록, 3회 이상 의심 표시) 구현. 종료하지 않음. 실제 카메라로 얼굴 판정 정확도는 미측정(가짜 카메라로 '얼굴 없음→이탈' 동작만 확인) |
| INT-17 | 점검 단계에서 서버와 WebRTC 실제 연결 + 짧은 발화 인식 확인, 실패 시 시작 차단 | P0 | interview / `POST .../precheck/webrtc` | `app/voice/`, `pages/candidate/Precheck` | T-INT-17, AC-08 | 구현 (시작 조건에서 제외) | **2026-10-30 화면정의서 SC-A04 우선(사용자 결정 "화면정의서 기준 대로")**: 점검 화면은 카메라·마이크·스피커만 확인한다. 서버 WebRTC 발화 확인은 선택 기능으로 남고 시작을 막지 않는다. 음성 연결은 면접 세션이 연결될 때 확인한다. PRD 원문과 다름 |
| INT-18 | WebRTC 자동 재연결, 실패 시 텍스트 입력 답변, 리포트에 표시 | P1 | interview | `app/interview`, 프런트 | T-INT-18 | 구현 | ADR-0024: 면접 도중 자동 재연결은 없음(끊기면 종료). 시작 시 연결 3회 실패·제시 한도 도달 시 텍스트 모드만 유지 |

### 5.6 코딩테스트 (COD)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| COD-01 | 켠 공고만, 면접 후 이어서, 순서 고정, 건너뛰기 불가 | P0 | coding | `app/coding` | T-COD-01, AC-15 | 미착수 | |
| COD-02 | 문제 설명·입출력 예시, 언어 선택(MVP 1종), 남은 시간, 코드 편집기 | P0 | coding / `GET .../coding` | `pages/candidate/Coding` | T-COD-02 | 미착수 | 편집기는 번들 포함(오프라인 동작, AC-22) ADR-0021: 문제 설명 패널, Lv·제한 표시, 시작 코드, 실행 결과·테스트케이스 탭(`Coding.tsx`, `coding.module.css`) |
| COD-03 | [실행]은 격리 환경, 정상/비정상 작동과 출력·오류 표시, 반복 가능, 실행만으로 완료 안 됨 | P0 | coding / `POST .../coding/run` | `runner/`, `app/coding` | T-COD-03, AC-15 | 미착수 | 공개 예시 입력으로만 실행 |
| COD-04 | [최종 제출] 확인 후 스냅샷 저장, 공개·비공개 테스트로 자동 채점 | P0 | coding / `POST .../coding/submit` | `app/coding/grading.py` | T-COD-04 | 미착수 | ADR-0021: 55문제 정답 풀이를 실제 샌드박스·채점기로 검증(`tools/verify_coding_bank.py`), 독립 풀이 대조(`tests/test_coding_bank.py`), 브라우저 E2E |
| COD-05 | 제한시간 종료 시 마지막 저장 코드 자동 제출 | P0 | coding | `app/coding`, worker | T-COD-05, AC-15 | 미착수 | 서버 시간 기준. 브라우저가 닫혀도 서버가 제출 |
| COD-06 | 네트워크 차단·CPU·메모리·시간·동시 실행 제한, 비공개 테스트 비노출 | P0 | runner | `runner/`, `app/coding/templates.py`, `variants.py` | T-COD-06, AC-15, tests/test_coding_variants.py | 미착수 | 샌드박스 검증 전에는 실행 기능 비활성 (ADR-0007). 2026-11-03: 비공개 테스트를 저장소·시드에서 빼고 응시 건마다 생성(`coding_problems` 변형본, 마이그레이션 0022). 문제는 담당자가 고르지 않는다 |
| COD-07 | LLM 코드 가독성·복잡도·개선점 담당자용 서술, 점수 미반영 → **2026-11-04 사용자 결정으로 변경: 효율성·문제 해결 방식·코드 품질을 점수에 반영하고 지원자에게도 공개**(ADR-0017) | P1 | evaluation | `app/ai/tasks/report.py`, `app/evaluation/pipeline.py`, `app/evaluation/scoring.py` | `tests/test_coding_metrics.py` | 구현 | 가짜 LLM으로만 시험. 실제 모델의 판정 정확도는 미측정. 비공개 테스트는 프롬프트에 넣지 않음 |
| COD-08 | 응시 중 LLM 채팅 패널·AI 활용 평가 | P2 | — | — | — | 미착수 | |
| COD-09 | 제출 코드 채팅형 꼬리질문 | P2 | — | — | — | 미착수 | |

### 5.7 AI 평가 (EVL)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| EVL-01 | 문항마다 루브릭 항목별 1~5점, 답변 원문 인용, 코멘트 | P0 | evaluation | `app/evaluation/judge.py` | T-EVL-01, AC-16 | 구현 | ADR-0023(2026-11-09): rubric-v4, 질문 유형별 논리성·전문성 요소, 점수별 예시, 근거는 문장 번호(`app/ai/tasks/evaluate.py`·`app/ai/rubric.py`), 3회 독립 판정 중앙값(`pipeline.median_judgement`). 테스트 `test_eval_reliability.py`, 실모델 보정 `scripts/eval_calibration.py` |
| EVL-02 | 인용을 코드로 원문 대조, 불일치 시 1회 재생성, 재실패 시 '근거 부족', 무응답·매우 짧은 답변은 LLM 전 규칙으로 '근거 부족' | P0 | evaluation | `app/evaluation/quotes.py` | T-EVL-02, AC-16 | 구현 | "매우 짧은" 기준값은 OQ-09 **변경(ADR-0023, 사용자 결정 2026-11-09)**: 인용 대신 문장 번호 → 코드가 원문 범위 저장. 번호 없음+3점 이상은 1회 재요청 후 `UNLINKED`(모델 점수 유지, 1점 처리 안 함), 번호 없음+1~2점은 `INSUFFICIENT/LOW_CONTENT`. 무응답·짧은 답변 규칙은 그대로 |
| EVL-03 | LLM 출력 JSON 형식 검사, 형식·범위 오류 시 재요청 | P0 | evaluation | `app/evaluation/judge.py` | T-EVL-03 | 미착수 | |
| EVL-04 | 항목 점수·총점은 서버 코드가 계산·저장 (6.4) | P0 | evaluation | `app/evaluation/scoring.py` | T-EVL-04, AC-17 | 미착수 | Decimal 계산 |
| EVL-05 | 답변 확정 시 문항 선평가 | P1 | evaluation | worker | T-EVL-05 | 미착수 | |
| EVL-06 | 실패·10분 초과 시 자동 재생성, 재실패 시 임시 점수 없이 '평가 보류', 담당자 재평가, 재응시 요구 안 함 | P0 | evaluation, task queue | `app/worker`, `app/evaluation` | T-EVL-06, AC-18 | 미착수 | |
| EVL-07 | 평가 입력에서 이름·성별·나이·사진 등 제외 | P0 | evaluation | `app/evaluation/redact.py` | T-EVL-07 | 미착수 | |
| EVL-08 | 결과마다 모델·프롬프트·루브릭·가중치 버전 기록 | P1 | evaluation | DB `evaluation_runs` | T-EVL-08 | 미착수 | 비용이 작아 P0와 함께 구현 |

### 5.8 결과 리포트 (RPT)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| RPT-01 | 완료 화면에서 제출 완료·평가 진행 상태 안내 후 마이페이지로, 이탈해도 서버에서 평가 계속 | P0 | applications | `pages/candidate/Done`, worker | T-RPT-01 | 미착수 | |
| RPT-02 | 담당자 리포트(2026-11-03: 세부 항목·STAR·코드가 잰 값·기업 추가 항목 포함, `rubric-v3`/`score-v2`/`details-v1`): 총점, 항목별 점수, 총평, 강점·개선점 1~3순위, 질문별 평가(면접 순서, 항목 점수·인용·코멘트), 코딩 결과 | P0 | recruiter / `GET /api/recruiter/applications/{id}/report`(응시자 상세용), `…/report-view`(SC-R10, 지원자용과 같은 화면 + AI 추천) | `app/recruiter/router.py`, `app/applications/report.py` `build_report`, `ApplicationDetail.tsx`, `Report.tsx` | `test_recruiter.py`, `test_candidate_report.py`, `Report.test.tsx` | 검증 완료 | AI 추천은 담당자에게만 보인다 |
| RPT-03 | 지원자 피드백은 별도 데이터, 6.6 범위·시점에만 노출. **변경(화면정의서 SC-A08):** 결과 리포트는 담당자 결정과 무관하게 평가가 끝나면 바로 본다. 합격·불합격과 피드백만 결정 뒤에 나온다 | P0 | candidate / `GET /api/candidate/applications/{id}/result`, `…/report` | `app/applications/report.py`, `Report.tsx`, `Result.tsx` | `test_candidate_report.py`, `Report.test.tsx` | 검증 완료 | 별도 테이블 + 별도 응답 스키마(결과), 리포트는 점수·인용을 보인다 |
| RPT-04 | 지원자 피드백 화면 "이 피드백은 AI가 만든 참고 자료입니다" 고정 표시 | P1 | 프런트 | `pages/candidate/Result` | T-RPT-04 | 미착수 | |
| RPT-05 | 사이트 안 알림 | P2 | — | — | — | 미착수 | |

### 5.9 관리자 기능 (ADM)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| ADM-01 | 담당자 첫 화면 공고 관리 + 요약 카드(진행 중 공고, 검토 대기 지원자) | P0 | recruiter / `GET /api/recruiter/dashboard` | `pages/recruiter/Home` | T-ADM-01 | 구현 | SC-R13 대시보드: `tests/test_recruiter.py`, `guards.test.tsx` |
| ADM-02 | 지원자 목록: 공고·결정 상태 필터, 총점·진행률·평가 상태·최종 결정. 평가 전에는 총점 대신 상태 | P0 | recruiter / `GET /api/recruiter/applications` | `app/recruiter` | T-ADM-02 | 구현 | SC-R08 응시자 목록 (AI 예상 합격 참고값 포함) |
| ADM-03 | 지원자 상세: 이력서·리포트·코딩 결과·질문별 답변·근거, 고정 문구 | P0 | recruiter / `GET /api/recruiter/applications/{id}` | `pages/recruiter/ApplicationDetail` | T-ADM-03 | 구현 | 문구: "AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다" |
| ADM-04 | 최종 결정 저장·변경, 누가·언제 이력, 지원자 마이페이지 표시 | P0 | recruiter / `POST .../decision` | `app/recruiter/decisions.py` | T-ADM-04, AC-20 | 구현 | 평가 완료 전 변경 불가 (7.3). 결정값은 합격·보류·불합격(화면정의서 SC-R09). 보류는 담당자 내부 상태라 지원자에게는 계속 ‘검토 중’이고 결과·메일이 나가지 않는다(마이그레이션 0014, `test_hold_is_internal…`) |
| ADM-05 | 공고별 커스텀 질문·가중치(합 100) 설정, 초기화 버튼. **변경(화면정의서 SC-R07, 2026-11-03):** 가중치는 0~60%의 5% 단위, 기업이 직접 평가 항목을 최대 5개 추가(이름·설명·상중하 기준·가중치)하고 합 100을 같이 지킨다 | P0 | recruiter / `PUT .../postings/{id}/evaluation-settings` | `app/postings`, `EvaluationSettings.tsx`, 마이그레이션 0024 | `test_postings_apply.py`(custom_items), `test_handlers.py`(company_items), `EvaluationSettings.test.tsx`, T-ADM-05, AC-21 | 검증 완료 | DB CHECK(`w_custom` 포함) + API 422. PRD의 루브릭 4개 고정과 달라 화면정의서를 따름 |
| ADM-06 | '평가 보류' 재평가 실행 | P0 | recruiter / `POST .../re-evaluate` | `app/evaluation` | T-ADM-06 | 미착수 | |
| ADM-07 | 지원자별 담당자 메모, 같은 회사만 | P1 | recruiter / `.../notes` | `app/recruiter/notes.py` | T-ADM-07 | 미착수 | |
| ADM-08 | 통계: 월별 응시자 수, 직무별 평균 점수, 공고·직무별 합격률 | P1 | recruiter / `GET /api/recruiter/stats` | `app/recruiter/stats.py` | T-ADM-08 | 구현 | 대시보드(SC-R13)의 공고별 합격률·직무별 평균·월별 응시자 |
| ADM-09 | 지원자 목록 리포트 요약 팝업 | P2 | — | — | — | 미착수 | |
| ADM-10 | 공고 이력(작성·마감·삭제) | P2 | — | — | — | 미착수 | 감사 로그(SEC-08)로 일부 대체 가능 |
| ADM-11 | 비교 모드·하이라이트 플레이어·캘린더·메일 | P2 | — | — | — | 미착수 | |

### 5.10 보안 (SEC)

| ID | 요약 | 우선 | 도메인 / API | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|---|
| SEC-01 | 개인정보 동의 고지(목적·항목·보유기간·AI 사용과 담당자 결정·사내 처리·공개 범위) | P0 | consent / `GET /api/consent/current` | `app/consent`, `pages/candidate/Consent` | T-SEC-01 | 미착수 | 문구는 법률 검토 전 초안 (X-13) |
| SEC-02 | 필수 항목 전부 동의해야 진행, 거부 시 공고 상세로, 문구 버전·항목별 여부·일시 기록 | P0 | consent | `app/consent` | T-SEC-02, AC-05 | 미착수 | |
| SEC-03 | 역할 기반 권한·회사 단위 분리를 모든 API·쿼리에서 서버 강제 | P0 | 전체 | `app/auth/deps.py`, 회사 범위 쿼리 헬퍼 | T-SEC-03, AC-03 | 미착수 | 다른 회사 리소스는 404로 응답(존재 비노출) |
| SEC-04 | 지원자 API는 공개용만, 메모·내부 채점·가중치 비노출 | P0 | candidate API 응답 스키마 | `app/reports/public.py` | T-SEC-04, AC-19 | 미착수 | 응답 스키마 화이트리스트 + 금지 필드 검사 테스트 |
| SEC-05 | 비밀번호 단방향 해시, 전송 구간 암호화 | P0 | auth, 배포 | `app/auth/passwords.py`, `deploy/` | T-SEC-05 | 미착수 | Argon2id. HTTPS는 리버스 프록시 |
| SEC-06 | 모든 AI 추론 사내 처리, 외부 AI API 금지 | P0 | ai | `app/ai/*`, 설정 검사, `app/aisvc/offline.py` | T-SEC-06, AC-22 | 검증 완료 | AI 엔드포인트 호스트 허용목록(사설망·localhost만). `tests/test_offline_static.py` + 외부망 차단 E2E (2026-10-28) |
| SEC-07 | 보관 기간 경과 데이터 자동 삭제 (8.5) | P1 | retention | `app/retention/service.py`, 주기 작업 `retention_purge`·`file_gc` | T-SEC-07 | 검증 완료 | 기간은 법률 검토 전 잠정값(음성 30일·내용 365일 구현값 유지, OQ-39). `tests/test_retention.py` (2026-10-28) |
| SEC-08 | 감사 로그(열람·메모·결정·동의·삭제·재평가), 앱 로그에 답변 원문·개인정보 금지 | P1 | audit | `app/audit`, 로그 필터 | T-SEC-08 | 미착수 | 로그 마스킹은 P0 품질 게이트에도 포함 |

## 2. 규칙·상태·비기능 요구사항

| ID | 요약 | 우선 | 구현 | 테스트 ID | 상태 | 근거·비고 |
|---|---|---|---|---|---|---|
| RULE-6.1 | 본질문 10개: 인성2·이력서2·기업2·직무4, 순서 인성→이력서→기업→직무, 커스텀 1개는 직무 1개 대체, 담당자 변경 불가 | P0 | `app/questions/compose.py` | T-RULE-6.1 | 미착수 | |
| RULE-6.2 | 꼬리질문 본질문당 최대 1·전체 최대 4, 생성 조건, 발화·이력서 문장 근거, 5초 내 미결정 시 건너뜀(→ 30초로 변경, ADR-0019), 45초, 점수 평균, 꼬리 전 중단 시 본답변만 평가·감점 없음 | P0 | `app/interview/followup.py`, `app/evaluation/scoring.py` | T-RULE-6.2 | 미착수 | |
| RULE-6.3 | 고정 루브릭 4항목, 1~5점 기준 문장, 텍스트만 평가, 태도·음성 지표 없음 | P0 | `app/evaluation/rubric.py` | T-RULE-6.3 | 구현 | 기준 문장은 TRD에서 정함 (05-ai-pipeline) rubric-v4(ADR-0023): 4항목·1~5점 유지, 기준 문장·예시·질문 유형별 요소 |
| RULE-6.4 | 점수 계산: 문항 점수(꼬리 평균), 항목 점수 = 10문항 평균×20, 코딩 = 통과/전체×100, 총점 = Σ(항목×가중치/100), 근거 부족 1점, 답변 없음 전 항목 1점, 기본 가중치, 첫 응시 후 잠금, 버전 기록 | P0 | `app/evaluation/scoring.py` | T-RULE-6.4, AC-17 | 미착수 | 반올림 규칙은 TRD에서 정함 (OQ-10) ADR-0023: '근거 부족 1점'은 규칙 결과(TOO_SHORT)에만 적용. 근거 문장 연결 실패는 1점이 아니라 `UNLINKED`(모델 점수), 내용 부족은 모델의 1~2점 |
| RULE-6.5 | 코딩 점수 = 최종 제출 코드 공개+비공개 통과율, [실행] 미반영, 자동 제출도 동일, 미작성·컴파일 실패 0점, LLM 코멘트 미반영 | P0 | `app/coding/grading.py` | T-RULE-6.5 | 미착수 | |
| RULE-6.6 | 결과 공개 범위 표 (지원자/담당자) | P0 | `app/reports/public.py` | T-RULE-6.6, AC-19 | 미착수 | |
| STATE-7.1 | 응시 상태 READY/PREPARING/INTERVIEWING/CODING/INTERRUPTED/SUBMITTED/EXPIRED와 전이 | P0 | `app/applications/states.py`, DB CHECK | T-STATE-7.1 | 미착수 | |
| STATE-7.2 | 평가 상태 PENDING/RUNNING/DONE/ON_HOLD, 제출과 같은 트랜잭션으로 작업 등록, 실패를 끝 상태로 두지 않음 | P0 | `app/evaluation`, `app/worker` | T-STATE-7.2, AC-18 | 미착수 | 제출 전 값은 NULL(평가 없음) — OQ-06 |
| STATE-7.3 | 채용 결정 검토 전/합격/불합격, 평가 완료 전 변경 불가, 변경 이력, AI 변경 불가, 보류 없음 | P0 | `app/recruiter/decisions.py` | T-STATE-7.3, AC-20 | 미착수 | |
| STATE-7.4 | 공고 임시저장→진행 중→마감, 마감 후 신규 지원·응시 차단 | P0 | `app/postings` | T-STATE-7.4 | 미착수 | |
| NFR-8.1 | 온프레미스 AI, 기준 장비 GPU 6GB·RAM 64GB, LLM 1개 4bit 공용, STT·TTS·임베딩 CPU 가능 모델, WebRTC 직접 연결, TURN은 TRD | P0 | `app/ai`, `deploy/` | T-NFR-8.1 | 미착수 | X-11 실측 결과 05-ai-pipeline |
| NFR-8.2 | 성능: 본질문 음성 1초 내(사전 TTS), 자막 2초 내, 답변 종료→다음 질문 5초 내, 리포트 3분(최대 10분), 10분 초과 시 1회 재생성 | P0 | 전 영역 | T-NFR-8.2 (측정) | 미착수 | 실측값과 목표값을 구분해 기록 |
| NFR-8.3 | 동시 응시 상한(6GB에서 1~2명 시작), 분석 누락 0건, 문항별 즉시 저장, AI 장애 시 면접 지속(은행·텍스트·답변 없음 대체) | P0 | `app/interview`, `app/worker` | T-NFR-8.3, AC-14, AC-18 | 미착수 | |
| NFR-8.4 | HTTPS(또는 localhost), 해시, 역할·회사 강제, PDF 10MB, 격리 실행, 감사 로그·로그 마스킹 | P0 | `deploy/`, 전체 | T-NFR-8.4 | 미착수 | |
| NFR-8.5 | 보관: 음성 30일, 카메라 저장 안 함, 텍스트·리포트·코드·이력서 365일, 생년월일·성별·사진 받지 않음, 탈퇴 시 삭제, 법률 검토 | P0(수집 제한)/P1(자동 삭제) | `app/retention`, 스키마 | T-NFR-8.5 | 미착수 | 기산점 미정 OQ-11 |
| NFR-8.6 | 평가 입력 개인정보 제외, 시선·표정·목소리 미사용, 인용 코드 검사, 골든셋 Kappa ≥ 0.6 | P0(앞 3개)/P1(골든셋) | `app/evaluation`, `tools/golden_set` | T-NFR-8.6 | 미착수 | 골든셋 데이터는 팀이 만들어야 함 (OQ-13) |
| NFR-8.7 | PC 크롬·엣지 최신, 모바일 미지원, 마이크·카메라 필수 | P0 | 프런트 | T-NFR-8.7 | 미착수 | |
| SCOPE-9.2 | 만들지 않는 것: AI 합불 예측·추천, 시선·표정 분석, 영상 녹화, AI 끼어들기, 화이트보드, 구글 로그인, 기업 가입, 모바일, 외부 AI API | P0(금지) | 전체 | T-SCOPE-9.2 | 미착수 | 합불 추천 문구 차단 테스트 포함 |

## 3. 인수 기준 (AC-01 ~ AC-22)

| AC | 내용 | 관련 요구사항 | 테스트 형태 | 상태 |
|---|---|---|---|---|
| AC-01 | 이름·이메일·비밀번호·연락처로 가입, 생년월일·성별 입력란 없음 | USR-02 | API 통합 + UI E2E | 미착수 |
| AC-02 | 담당자는 발급 계정으로만 로그인, 담당자 [지원하기] 차단 | USR-03, USR-05 | API 통합 + UI E2E | 미착수 |
| AC-03 | 다른 회사 공고·지원자 주소 직접 열면 거부 | SEC-03 | API 통합(IDOR 매트릭스) | 미착수 |
| AC-04 | 같은 공고 두 번 지원 불가 | JOB-02 | API 통합 + DB 제약 | 미착수 |
| AC-05 | 필수 동의 안 하면 진행 불가, 동의 기록에 문구 버전·일시 | SEC-02 | API 통합 + UI E2E | 미착수 |
| AC-06 | PDF 아님·10MB 초과 거부와 이유, 텍스트 PDF는 표준 항목 채움·수정 가능 | RES-02, 03, 05 | API 통합 + UI E2E | 미착수 |
| AC-07 | 이력서 질문 2개가 이력서에 실제 적힌 경험·프로젝트 언급 | QST-03 | 단위(근거 검증) + 실모델 통합 | 미착수 |
| AC-08 | 마이크·카메라·WebRTC 중 하나라도 실패하면 시작 불가 | INT-01, INT-17 | API 통합 + UI E2E(가짜 장치) | **2026-10-30 변경: 화면정의서 SC-A04에 따라 시작 조건은 카메라·마이크·스피커 점검(서버 기록)만. WebRTC는 세션 연결 시 확인(INT-17 비고). tests/test_interview.py 수정, 통과(2026-10-30).** 이전 기록: 서버 자동 시험 통과(2026-10-28, tests/test_interview.py (WebRTC 점검 발화·미디어 자가 보고·AI 상태)); UI E2E 정상 경로 통과(2026-10-28, frontend/e2e/candidate-flow.spec.ts, 가짜 장치·가짜 AI — 점검 통과 전 [면접 시작] 비활성 확인) |
| AC-09 | 질문 음성+자막, 음성 끝나기 전 소리는 답변에 안 들어감 | INT-04, INT-05 | 서버 단위(프레임 게이트) + WebRTC 통합 | 서버 자동 시험 통과(2026-10-28, tests/test_voice.py (게이트 이전 발화 STT 미전달)), UI 미구현 |
| AC-10 | 실시간 자막, [답변 완료]로 확정, 침묵만으로 종료 안 됨 | INT-05, INT-15 | WebRTC 통합(합성 음성) | 서버 자동 시험 통과(2026-10-28, tests/test_voice.py, test_interview.py (자막·[답변 완료]·침묵으로 종료 안 됨)), UI 미구현 |
| AC-11 | 60/45초 만료 시 확정, 10초 경고, 무발화 '답변 없음' | INT-06 | 서버 단위(가짜 시계) + UI | 서버 자동 시험 통과(2026-10-28, tests/test_interview.py (가짜 시계: 만료 확정·10초 경고·무발화)), UI 미구현 |
| AC-12 | 진행률 "3 / 10", 꼬리질문 본질문당 1·전체 4 이하 | INT-07, 6.2 | 단위 + UI | 서버 자동 시험 통과(2026-10-28, tests/test_interview.py (본질문당 1·전체 4, 텍스트 모드)), UI 미구현 |
| AC-13 | 새로고침·끊김 후 끊긴 문항부터, 저장 답변 유지 | INT-11 | API 통합 | **ADR-0024로 변경**: 끊김·새로고침 → 응시 종료(EXPIRED), 답한 문항 평가, 재연결 409. 서버 자동 시험 통과(2026-11-09, tests/test_interview.py) |
| AC-14 | 동시 응시 상한 초과 시 안내 문구, 시작 안 됨 | INT-13 | API 통합(동시성) | 서버 자동 시험 통과(2026-10-28, tests/test_interview.py (동시 시작 3건 중 1건 거부, 고정 문구)), UI 미구현 |
| AC-15 | 코딩테스트는 켠 공고만 면접 뒤, [실행]은 정상/비정상만, 비공개 테스트 비노출, 시간 종료 자동 제출 | COD-01, 03, 05, 06 | API 통합 + 샌드박스 통합 | 서버 자동 시험 통과(2026-10-28, tests/test_coding.py; 실제 Docker 샌드박스는 `python -m runner --selftest`로 장비에서 확인); UI E2E 정상 경로 통과(2026-10-28, frontend/e2e/candidate-flow.spec.ts — [실행]은 정상 작동·출력만, 정답 여부 문구 없음, 최종 제출) |
| AC-16 | 모든 점수에 원문 인용, 원문에 없는 인용은 '근거 부족' | EVL-01, 02 | 단위(인용 검증) + 가짜 LLM 통합 | 미착수 ADR-0023: 근거는 답변 문장(코드가 자른 원문 범위)이라 원문에 없는 인용이 생길 수 없음. 근거 없는 점수는 화면에 '근거 미연결'·'내용 부족'으로 표시 |
| AC-17 | 총점 = 6.4 공식 직접 계산값 | EVL-04 | 단위(계산) + 통합 | 미착수 |
| AC-18 | 평가 서버 멈춘 채 제출해도 평가 건 유지, 재기동 후 평가 완료, 재응시 요구 없음 | 8.3, 7.2 | 통합(워커 중지·재기동) | 미착수 |
| AC-19 | 결정 전 점수 비공개, 결정 후 항목별 점수·강점·개선점만, 응답에 총점·인용·메모·가중치 없음 | 6.6, SEC-04 | API 통합(응답 필드 검사) | 미착수 |
| AC-20 | 합격·불합격 저장·변경, 이력에 누가·언제 | ADM-04 | API 통합 | 미착수 |
| AC-21 | 가중치 합 ≠ 100 저장 거부, 첫 응시 후 가중치·코딩 설정 변경 불가 | ADM-05, JOB-06 | API 통합 | 미착수 |
| AC-22 | 외부 인터넷 차단 상태에서 지원부터 리포트까지 전체 흐름 동작 | SEC-06 | 통합(외부망 차단 컨테이너 네트워크) + 코드 검사 | 자동 시험 통과(2026-10-28: 정적 검사 `tests/test_offline_static.py`, 외부망 차단 네트워크 네임스페이스에서 지원→면접→코딩→평가→결정→결과 E2E `tools/offline/run.sh`, 가짜 AI). 실제 모델·시연 장비 수동 리허설 미실시(`docs/offline-rehearsal.md` 3절) |

## 4. PRD 결정 사항 대응 (11장)

| ID | PRD 상태 | 내용 | 이 프로젝트의 처리 |
|---|---|---|---|
| X-01 | 확정 | WebRTC 실시간 음성, 답변 단위 업로드 안 씀 | 그대로 따름 |
| X-02 | 확정 | 침묵 기준 없음, [답변 완료] 버튼, 60/45초 고정, 10초 경고, 무발화 '답변 없음' | 그대로 따름 |
| X-03 | 확정 | 개발·시연 기준 장비 GPU 6GB | 이 장비가 해당 (`environment.md`) |
| X-04 | 확정 | 지원자 결과 공개는 담당자 최종 결정 후 | 그대로 따름 |
| X-05 | 확정 | 4항목 텍스트 기준, 태도·음성 지표 없음 | 그대로 따름 |
| X-06 | 확정 | 화면 이탈 경고·기록만, 강제 종료 없음 | 그대로 따름 |
| X-07 | 확정(법률 검토 후 최종) | 답변 음성 30일 | 잠정값으로 구현, 설정값으로 분리 |
| X-08 | 확정 | 지원 건당 1회, 시스템 오류는 재처리 | 그대로 따름 |
| X-09 | 확정 | 커스텀 질문 1개, 직무 질문 1개 대체 | 그대로 따름 |
| X-10 | 확정 | Python 1종, 팀이 만든 문제·테스트케이스 | 시드 문제는 예시로만 제공 (OQ-14) |
| X-12 | 확정 | 결정 상태 합격·불합격만, 검토 전이 보류 역할 | 그대로 따름 |
| X-11 | **미결정** | LLM·STT·TTS·임베딩 모델 선정, 6GB 실측 | TRD에서 후보 비교·실측 후 ADR로 기술적 임시 결정 |
| X-13 | **미결정** | 동의 문구·보관 기간 법률 검토 | 임의 확정하지 않음. 초안 문구와 설정값만 둠 |

## 11. 화면정의서(pptx 78쪽)에서 생긴 요구 — 2026-11-02 기준

화면정의서가 PRD·CLAUDE.md보다 우선한다(2026-10-30 결정). 아래는 화면 ID로 묶은 구현 현황이다.

| 화면 | 내용 | 구현 | 테스트 | 상태 | 비고 |
|---|---|---|---|---|---|
| SC-R01·O01·O02 | 기업회원 가입·인증, 운영자 기업 인증·공고 숨김·기업 정지 | `app/auth/company_signup.py`, `app/operator`, `screens/operator/*` | `test_company_signup.py`, `test_operator.py` | 검증 완료 | |
| SC-R03·R04·R06 | 공고 관리·작성·미리보기 | `app/postings`, `Postings.tsx`, `PostingForm.tsx`, `PostingPreview.tsx` | `test_postings_apply.py` | 검증 완료 | 미리보기는 작은 창이 아니라 쪽이고, 작성 중 저장하지 않은 내용은 미리볼 수 없다(저장된 초안만) |
| SC-R05 | 템플릿 불러오기 | `app/recruiter/templates.py`, `TemplateModal.tsx` | `test_templates_inquiries.py` | 검증 완료 | HWP 미지원 |
| SC-R07 | 면접 질문 관리 | `EvaluationSettings.tsx`, `app/postings` | `EvaluationSettings.test.tsx` | 진행 중 | 평가 항목 추가(상·중·하 기준)와 세부 항목은 없음(평가 구조 2~4단계) |
| SC-R08·R09·R10 | 응시자 목록·상세·담당자 리포트, 메모 | `app/recruiter/router.py`, `ApplicationDetail.tsx`, `Report.tsx` | `test_recruiter.py`, `test_candidate_report.py` | 검증 완료 | |
| SC-R11·R14·R12 | 일정 관리, 메일 보내기, 알림 창 | `app/recruiter/schedule.py`, `notifications.py`, `MailModal.tsx`, `NotificationBell.tsx` | `test_schedule.py`, `test_mail.py`, `test_notifications.py` | 검증 완료 | |
| SC-R13 | 대시보드 | `app/recruiter/router.py`, `Home.tsx` | `test_recruiter.py` | 검증 완료 | |
| SC-R15 | 문의 쪽지 | `app/recruiter/inquiries.py`, `Inquiries.tsx`, `InquiryModal.tsx` | `test_templates_inquiries.py`, `Inquiries.test.tsx` | 검증 완료 | 정의서에 지원자 쪽 화면이 없어 ‘지원 현황’의 ‘기업에 문의’ 창을 두었다. 운영자가 직접 쓰는 화면은 없고 기업 인증 반려 사유만 쪽지로 간다 |
| SC-A08 | 지원자 결과 리포트(슬라이드 30~34) | `app/applications/report.py`, `Report.tsx` | `test_candidate_report.py`, `Report.test.tsx` | 진행 중 | 영역 이름·순서는 정의서와 같다. 세부 항목, 규칙 지표 점수, 발음·높낮이, STAR, 표정·긴장도 그래프는 아직 없음 |
| (제거) 화이트보드 | 2026-11-02 결정으로 기능 전체 제거 | — | — | 보류 | 저장된 데이터·열은 남김 |
| (미착수) | 평가 구조 2~6단계: 세부 항목, 규칙 지표 반영, 기업이 추가한 평가 항목, 음성 분석, STAR | — | — | 미착수 | 사용자가 ‘정의서 구조로 전환’을 선택함. 1단계(영역 이름·순서·가중치)만 끝남 |
