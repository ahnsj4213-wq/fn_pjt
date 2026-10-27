# 설계 2단계 상호 리뷰 1라운드 — 에이전트 E (보안·품질 검증)

- 검토 대상: `agent-A-architecture.md`, `agent-B-backend-data.md`, `agent-C-ai-voice.md`, `agent-D-frontend-ux.md` 전문. 조정자 실측: `prototypes/README.md`, `prototypes/sandbox/escape_tests.py`·`Dockerfile`, `results-sandbox-escape.txt`, `quote_norm_check.py`, `results-quote-normalization.txt`, `results-rtc-loopback.txt`.
- 다른 에이전트의 리뷰 파일은 읽지 않았다. 설치·서버·Docker 실행은 하지 않았다. 인용 정규화만 표준 라이브러리 스크립트로 오프라인 재계산했다(scratchpad `quote_norm_review.py`, 결과 파일의 인용을 입력으로 사용).
- 심각도: Critical(보안 취약점·데이터 손실·PRD 위반·실행 불가) / Major / Minor. **Critical은 다수결로 승인되지 않는다**: 아래 Critical 2건은 계약이 바뀌기 전까지 해당 기능을 "검증 완료"로 올리지 않는 것을 E의 조건으로 둔다.
- PRD 기능 ID·우선순위·수치·AC는 바꾸지 않는다. 표의 "시험"은 그대로 테스트 케이스로 옮길 수 있게 적었다.

## 1. 실측 결과로 바뀐 내 입장

| # | 실측 | 바뀐 입장 (E 설계안 절) |
|---|---|---|
| 1 | Docker Desktop(WSL2)·runc 강화 컨테이너 12/12 차단 | **개발 검증 기준선으로 확정**(2.1). 다만 12개는 내 SBX 14개 중 일부만 덮는다: SBX-02(uid·CapEff만), 03, 04, 05, 06, 07(무한 루프만), 08(절단만), 09(env 일부). 추가 필요: SBX-01 매 실행 전 inspect, NoNewPrivs·Seccomp 상태, SBX-10(mount·unshare·ptrace), mountinfo·`docker.sock` 부재, `sleep` 기반 벽시계, SBX-12 정리, SBX-13 동시성, 출력 폭주 시 runner 메모리·Docker 디스크. "host gateway" 사례는 DNS 실패로 끝났으므로 IP 직접 연결도 시험한다 |
| 2 | (같은 결과) 커널 취약점 탈출은 스파이크가 증명할 수 없는 영역 | E-Q9 완화: "운영 runsc **필수**" → "runsc **권장**, 최소 조건 = rootless Docker(또는 userns-remap) + 같은 강화 옵션 + 운영 호스트에서 SBX 재통과 + 커널·이미지 패치 주기". rootful runc 단독은 운영 불가 유지 |
| 3 | 컨테이너 1회 기동 약 0.45초 | E-Q14 해소: 테스트 1건 = 컨테이너 1회(2.3 G-b) 유지. 테스트 20건 순차 약 9초(runc). runsc는 운영에서 재측정 |
| 4 | 스파이크 runner는 `communicate()`로 출력 10MB를 메모리에 올린 뒤 64KB로 자름, `--log-driver` 미지정 | 3.4.1의 "스트림 상한 + `--log-driver none`"을 권고에서 **필수**로 올리고 SBX-08 판정에 runner RSS·Docker data-root 증가량을 넣는다 |
| 5 | 스파이크 이미지 진입점 `python -I -B -`(코드를 stdin으로 받음) | 이 방식은 테스트 입력을 줄 stdin이 남지 않는다 → 3.4.1의 길이 접두 프레이밍 런처 유지 |
| 6 | 컨테이너 env에 베이스 이미지 변수(`GPG_KEY`, `PYTHON_SHA256`, `HOSTNAME` 등) 존재 | SBX-09 수정: "허용목록과 정확히 같음" → "이미지 빌드 때 기록한 ENV 목록 + runner 주입분과 같고, 비밀 키워드 없음" |
| 7 | aiortc 기본 설정이 Google STUN 사용(첫 프레임 10.2초 + 외부 통신), `iceServers=[]`로 0.17초 | SC-43을 "확인 필요"에서 **확정 요구**로. AC-22 시험에 "ICE 수집 1초 미만 + 외부 DNS·STUN 패킷 0건"을 추가 |
| 8 | 원문 그대로 비교 6/20, 정규화 후 20/20 | 내 SC-72("공백 **축약**만")는 틀렸다. 모델이 원문에 없는 공백을 넣으므로("APM 으로") 공백 **제거**가 필요하다. 재계산: 결과 파일의 인용 20개(파일에는 앞 40자만 기록됨)는 **공백·제로폭 제거만으로 20/20**. 문장부호·기호 제거는 이 표본에서 필요 없었고 위조를 통과시킨다(표의 #2) |
| 9 | 브라우저↔aiortc에서 게이트 이전 발화 5.46초를 서버가 폐기 | TTS를 서버 WebRTC 트랙으로 보내는 A·C안이 AC-09를 서버 권위로 지킨다는 것이 실측으로 확인됨 → 이 안을 지지. HTTP 오디오(D안)를 쓰면 개방 시각 하한 규칙이 필요(#10) |
| 10 | 원본 PDF 내장 뷰어(D 2.9) | E-Q4 변경: 기본 `attachment` → 조건부 `inline` 수용(#15) |

## 2. 리뷰 표

| # | 대상·절 | 문제 | 심각도 | 근거·재현·테스트 | 제안 해결 |
|---|---|---|---|---|---|
| 1 | A 4.4 runner `POST /v1/grade {tests:[{id,input,expected,hidden}]}` + A 3.6 `sandbox/`에 "채점 하니스" + B 4 `grade(code, all_cases)` | 기대 출력이 runner를 거쳐 **샌드박스 이미지 안의 하니스**로 들어가는 계약이다. 제출 코드와 하니스가 같은 경계에 있으면 코드가 기대 출력(파일·인자·메모리)을 읽어 그대로 내거나, 하니스를 조작(`os._exit(0)`, 모듈 바꿔치기, 결과 줄 위조)해 비공개 테스트를 유출·통과할 수 있다 | **Critical** | COD-06 "비공개 테스트는 어떤 화면·AI 입력에도 노출하지 않는다", 6.5 점수 무결성(P-5). 시험: 비공개 기대 출력에 카나리아를 넣고, 제출 코드가 `/` 전체·환경변수·`/proc/self/cmdline`·`/proc/self/fd`를 훑어 카나리아가 보이면 출력하게 함 → 비공개 통과 수 0, 컨테이너 안 카나리아 미발견(SBX-11). 하니스 조작 코드(`import sys; sys.exit(0)` 직후 가짜 "PASS" 출력)로 채점 → 통과로 세지 않음 | runner 요청은 `{case_id, stdin}`만, **기대 출력은 runner에도 보내지 않는다**. 비교는 worker(B)가 컨테이너·runner 밖에서 한다. 테스트 1건 = 컨테이너 1회(실측 0.45초). 샌드박스 이미지에는 언어 런타임과 런처만 둔다. 판정은 종료 코드·시간·메모리 상태 + 밖에서 한 출력 비교뿐 |
| 2 | C 3.6 `qn-1`(유니코드 P·S 범주 전부 제거) + F 스파이크 정규화(ADR-0008 후보: `. , : ~ -` 등 제거) + B 3.2 `answer_evaluations.quote text` | 문장부호·기호 제거가 **숫자·기술명이 다른 인용**을 통과시키고, 저장·표시되는 것은 LLM이 쓴 문자열이라 원문에 없는 인용이 `quote_verified=true`로 담당자에게 보인다 | **Critical** | AC-16 "원문에 없는 인용은 '근거 부족'", P-4. 재계산(stdlib): 원문→인용 "3.5배"→"35배", "C로"→"C++로", "C로"→"C#로", "2~3명"→"23명", "1:1"→"11", "10%"→"10" 6쌍이 **qn-1에서 모두 통과**, 스파이크 정규화는 3쌍 통과. 공백 제거만 쓰면 6쌍 모두 거부하면서 스파이크 인용 20/20 유지 | ① 1차 정규화 = NFKC·소문자·공백·제로폭 제거만 ② 문장부호 제거는 2차 단계로만 쓰고, 이때 영숫자·기호 토큰(`[0-9a-z][0-9a-z+#.%:/~-]*`)이 인용과 원문에서 같아야 통과(재계산에서 위조 6쌍 거부·정상 띄어쓰기 1쌍 통과) ③ 일치하면 **원문 범위(start,end)와 원문 부분 문자열을 저장·표시**하고 LLM 문자열은 버린다(D RC-14 `quote{text,start,end}`와 연결, 불변식 `text == answer_text[start:end]`) ④ 오프셋 기준 텍스트를 하나로 고정(C의 `answer_eval_text` 또는 `transcript_raw`, 화면도 같은 것) ⑤ C의 기술 용어 동치 사전(파이썬↔python)은 2차 단계에만. AC-16 표 테스트에 위 6쌍 추가 |
| 3 | B 3.3 `forbid_mutation`(`SET LOCAL app.purge='on'`이면 통과) 적용 대상에 `audit_logs`·`decision_history`·`consent_records` 포함 | `app.*` 같은 사용자 정의 설정은 어떤 롤이든 SET할 수 있어 앱 롤(또는 SQL 주입)이 감사 로그·결정 이력·동의 기록을 고칠 수 있다. 행 트리거는 TRUNCATE에 걸리지 않는다 | Major | SEC-08, ADM-04 이력 무결성. 시험: `app_api`·`app_worker`로 접속해 `SET LOCAL app.purge='on'` 후 `UPDATE`·`DELETE`·`TRUNCATE audit_logs`(결정 이력·동의 기록도) → 모두 권한 오류 | 세 테이블은 우회 없는 불변 + 앱 롤의 UPDATE·DELETE·TRUNCATE 권한 회수(삭제 작업도 건드리지 않음, B 3.10과 일치). 보관 삭제는 GUC가 아니라 전용 롤 소유 `SECURITY DEFINER` 함수로만, 트리거는 `current_user` 확인. 해시 체인(E SC-68)은 P1 선택 |
| 4 | A 4.4 runner, A 3.1 + 스파이크 runner | 출력을 다 읽은 뒤 자르고 `--log-driver`가 없어 무한 출력이 runner 메모리와 Docker 로그 파일(디스크)에 쌓인다 | Major | F 실측: `print('A'*10_000_000)`를 메모리에 10MB 올린 뒤 절단. 시험: 벽시계 제한 동안 무한 출력 → runner RSS 증가 ≤ 상한+여유, Docker data-root 증가 ≈ 0, 다른 실행 지연 없음 | attach 스트림에서 stdout 64KB·stderr 16KB까지만 읽고 즉시 kill, `--log-driver none`. SBX-08 판정 확장 |
| 5 | A 4.4 `GET /v1/health → {sandbox_verified, runtime}` | 기동 때 한 번 정한 불리언뿐이다. 실행마다 설정 확인, 지문, 유효 기간이 없어 이미지 교체·플래그 누락 뒤에도 verified가 유지된다 | Major | "검증 전 비노출"(ADR-0007 취지)의 구현 공백. 시험: 이미지 태그를 다른 다이제스트로 바꾸거나 플래그 하나를 빼고 실행 → 그 실행 거부 + 상태 FAILED + [실행] 503 | E 3.4.4: 지문(runner 버전·이미지 다이제스트·엔진 버전·런타임·seccomp·제한값 해시) + 24시간 유효 + 매 실행 전 inspect(SBX-01). health에 `fingerprint`·`checked_at` 추가, 상세는 내부 전용 |
| 6 | A 3.1·4.3 rtc 시그널링 `POST /rtc/v1/sessions`(별도 rtc 앱, 쿠키 세션) | rtc 쪽 CSRF·Origin·Host 검사가 명시되지 않았다. api와 다른 앱이라 미들웨어가 빠지기 쉽다 | Major | 쿠키 인증 상태 변경 요청(TH-08, TH-18). 시험: 다른 출처에서 자격 증명 포함 offer 전송, Origin 없음, `text/plain` 본문 → 모두 403. 다른 지원자의 `application_id` → 404 | api와 같은 인증·CSRF·Origin·TrustedHost 미들웨어 패키지를 rtc에도 적용. 라우트 인벤토리·IDOR 매트릭스에 rtc 앱 포함 |
| 7 | A 3.1(rtc는 host 네트워크 + DB 쓰기) + B 1(DB 롤 `app_api`·`app_worker`뿐) | 사내망 UDP(STUN·DTLS·SCTP·RTP)를 직접 파싱하는 rtc가 host 네트워크와 넓은 DB 권한을 함께 가진다 | Major | 최소 권한. 시험: rtc 롤로 `recruiter_notes`·`recruiter_reports`·`coding_testcases` 조회, `applications.decision_status` 갱신 → 권한 오류 | `app_rtc` 롤(면접 진행·답변·슬롯·하트비트 테이블만). rtc는 비루트, `cap_drop: ALL`, `no-new-privileges`, 읽기 전용 루트 |
| 8 | D 4.2 IV-12 `answer-mode {mode:'text'}`, B 3.12 `finish {input_mode, text?}` | 텍스트 답변 전환을 클라이언트가 고를 수 있다. 정상 연결 중에도 타이핑(붙여넣기 포함)으로 음성 면접을 우회 | Major | INT-05·X-01은 음성, INT-18은 "다시 연결되지 않으면" 텍스트. 시험: WebRTC 정상 상태에서 IV-12·`input_mode=TEXT` → 409, rtc가 재연결 실패를 기록한 뒤에만 허용, 리포트에 텍스트 문항 표시 | 서버 전이 조건: rtc가 기록한 재연결 실패(`text_mode_since`, 사유·횟수)가 있는 지원 건만 TEXT. 같은 서버 마감, 길이 상한 |
| 9 | C 3.3 재접속(진행 중 문항 처음부터 재출제, OQ-21) | 문항을 들은 뒤 일부러 끊으면 같은 문항을 횟수 제한 없이 다시 받아 준비 시간을 늘릴 수 있다 | Major | TH-17(답변 시간 조작). 시험: Q3에서 강제 끊김 5회 → `reask_count=5`가 저장되고 담당자 리포트에 표시, 시도마다 서버 마감이 독립 | 재출제 횟수·시각 기록·표시는 필수(OQ-21 그대로). 상한(예: 문항당 N회 뒤 처리)은 PRD 해석이 필요하므로 F가 OQ로 등록 |
| 10 | D 2.8 질문 음성 HTTP 재생 + IV-10 개방 알림 | 개방 시각을 클라이언트 알림이 정하면 조작된 클라이언트가 재생 끝 전에 개방할 수 있어 AC-09의 서버 권위가 약해진다 | Major | 시험: `question` 수신 직후 IV-10 → `answer_opened.opened_at ≥ presented_at + tts_duration_ms`여야 하고, 그 전 프레임은 답변에 없음 | 실측으로 검증된 서버 WebRTC 트랙(A·C) 채택 권장. HTTP 안이면 서버가 개방 시각 하한 = 제시 시각 + TTS 길이로 강제 |
| 11 | D 4.5 test 훅(`/api/test/*`, 시간 배율, 상한 변경, 강제 끊기), D 4.1 OpenAPI 문서 | 운영에 남으면 마감·상한·연결을 밖에서 조작할 수 있다 | Major | 시험: 운영 프로필로 앱을 만들어 라우트 인벤토리에 `/api/test/*`·`/docs`·`/redoc`·`/openapi.json` 0개, 가짜 시계 주입점 비활성, 운영 프로필에서 test 설정을 켜면 기동 실패 | 빌드·설정 단계에서 제외 + E 품질 게이트(QG-01)에 인벤토리 검사 추가 |
| 12 | C 3.3 grace 0.5초 vs A 3.3 유예 1.5초 | 네트워크 유예 값 불일치 → AC-11 테스트 기대값이 정해지지 않는다 | Minor | 유예는 답변 시간을 늘리는 값이라 작게 고정해야 한다 | 설정값 하나로 합의(제안 1.0초 이하), 유예 뒤 수신분은 폐기 |
| 13 | D 4.1 CSRF "이중 제출 쿠키 또는 AU-3" | 방식 미정. 이중 제출은 형제 서브도메인 쿠키 주입에 약하다 | Minor | E 2.6 | Synchronizer 토큰(세션 행에 해시 저장, 로그인·`/api/auth/me` 응답으로 전달) + Origin 검사. B `sessions`에 칼럼 추가 |
| 14 | D 4.4 CSP | `base-uri`·`form-action` 누락, `style-src` 미정 | Minor | E 3.3 | E 3.3 값 사용. 정적 호스팅이라 nonce 주입 비용이 크면 `style-src 'self' 'unsafe-inline'`만 예외(스크립트는 엄격 유지) |
| 15 | D 2.9 PDF 내장 뷰어 iframe(`frame-ancestors 'self'`) | 업로드 파일을 같은 출처에서 inline 렌더 | Minor | 시험: HTML·SVG를 `.pdf`로 위장한 폴리글롯 → 저장 단계 거부(매직바이트 + 파싱). 응답은 `Content-Type: application/pdf` 고정, `nosniff`, `Content-Disposition: inline; filename="resume.pdf"`(원본 파일명 미사용) 확인 | 조건부 수용. CSP `sandbox`는 내장 뷰어를 막는 것으로 알고 있어 쓰지 않는다(FR-14와 함께 실측) |
| 16 | B 3.12 `PATCH·DELETE /api/recruiter/notes/{n}`, `GET .../resume-files/{id}/extraction`, D IV-9 `attempts/{attempt}/audio`, rtc offer | 부모 지원 건 없이 하위 리소스 ID만 받는 경로 | Minor | IDOR 매트릭스(E 3.5)에 행 추가: Y사 담당자가 X사 메모 ID로 수정 → 404, 지원자 B가 A의 추출 ID·질문 음성 ID → 404 | 범위 헬퍼가 부모의 company_id·owner를 조인해 검사 |
| 17 | 경로·코드 이름 불일치: 시그널링(A `/rtc/v1/sessions`, C `/api/candidate/interviews/{id}/rtc/offer`, D IV-3·IV-8), 실행 불가 코드(`RUNNER_DISABLED`·`RUNNER_UNAVAILABLE`), 로그인 실패 코드 | 정책 선언·테스트가 경로·코드에 묶여 있다 | Minor | — | F가 B 3.12·D 4.2 기준으로 단일화 |
| 18 | B 3.12 login 429 | 제한을 계정 존재와 무관하게 건다는 명시가 없다 | Minor | 시험: 없는 이메일·있는 이메일 각 6회 실패 → 같은 429·본문·지연 | E SC-04 반영 |
| 19 | B 3.2 `users.role='OPERATOR'` | 운영자 행이 웹 로그인 경로로 들어올 수 있다 | Minor | 시험: OPERATOR 계정으로 로그인 → 일반 실패 | 웹 로그인은 CANDIDATE·RECRUITER만, OPERATOR는 비밀번호 해시 없음(CLI는 OS 권한) |
| 20 | B 3.12 `coding/runs`(동시 1건만) | 지원자별 빈도 제한이 없어 연속 실행으로 전역 실행 슬롯을 점유 | Minor | COD-06 "동시 실행 제한". 시험: 1명이 순차 100회 → 제한 뒤 429 | 지원자별 10분 20회(제안) + 전역 세마포어(A의 2) |
| 21 | D 3.13 오프라인 검사(`context.route` 차단) | 브라우저 쪽 차단은 서버 쪽 외부 통신(aiortc STUN, 텔레메트리)을 보지 못한다 | Minor | 실측 #7 | AC-22 판정은 A `offline-test`(내부망 + 로깅 DNS), 브라우저 차단은 보조 |
| 22 | A 3.1 허용목록은 ai-gw에만, rtc는 speech를 직접 호출. D AU-6 `rtc-config`는 서버 설정의 ICE 서버를 내려줌 | 허용목록 우회 경로 | Minor | 시험: rtc의 STT URL 또는 ICE 서버에 공인 호스트 → 기동 실패 | 모든 AI 엔드포인트·ICE 서버를 공통 설정 검증기(E SC-80)에서 검사, ICE 기본값 `[]` |
| 23 | B 3.2 `audit_logs.detail`, `jobs.payload`(자유 형식 jsonb) | 개인정보·답변 혼입 가능 | Minor | 카나리아 E2E 뒤 두 칼럼 검색 0건 | 키 허용목록 스키마 |

## 3. 다른 설계안에서 받아들이는 점

- **B 격리 구현**: 복합 FK `(id, company_id)`, `with_loader_criteria` 자동 조건, 담당자 라우터 팩토리 강제, `app/recruiter/**`의 원시 SQL 금지 검사. E 2.5 I-a보다 강하므로 그대로 채택하고 E의 IDOR 매트릭스를 이 위에 돌린다. RLS는 B의 "P1, 3개 테이블 시범"에 동의(E-Q7 종결 제안).
- **B DB 권한**: `coding_public_examples` 뷰만 `app_api`에 허용, `decision_status`는 `app_worker`에 칼럼 권한 없음 → E SC-19(정적 규칙)를 DB 강제로 대체.
- **B 지원자 데이터 구조**: `candidate_feedback`에 총점·인용·가중치 칼럼이 아예 없음, 결정 전 `feedback=null`을 쿼리 조건으로 → AC-19의 구조적 1차 방어로 채택. 지원자 표시에서 ON_HOLD를 "평가 진행 중"으로 합치는 안(B 7-8)도 내부 상태 노출 최소화 관점에서 지지.
- **B·C 질문은행**: `source='AI'`면 `company_id` 필수 CHECK, 이력서 질문은 은행에 넣지 않음 → E-Q8 해소.
- **B 탈퇴 재인증**(`DELETE /api/candidate/me`에 password) → E SC-09와 일치.
- **A 경계**: runner·ai-gw에 DB 자격 증명 없음, exec 네트워크 분리 + `X-Runner-Token`, PDF 파싱은 worker 자식 프로세스, PRD 고정값을 설정이 아닌 코드 상수로 고정, `offline-test` 프로필에서 egress 실패를 먼저 확인.
- **A·D 면접 도중 샌드박스 장애**: [실행]만 비활성, 코드 저장·제출은 계속, 채점은 대기(0점 처리 금지) → 수용. 단 시작 전 미검증 상태의 처리는 쟁점 2로 남긴다.
- **C 정보 흐름 최소화**: 꼬리질문 입력에서 성명·연락처 제외, 리포트 요약 입력에서 원문·총점·가중치 제외, 지원자 피드백 15자 겹침 금지(E의 20자보다 엄격하므로 15자 채택), 인용 대상 = 그 호출의 답변 단위, 코드 리뷰 입력에서 비공개 테스트 제외.
- **C 인젝션 방어**: 호출마다 nonce 구분자, 모델 제어 토큰 정화, `injection_suspected` 표시(자동 감점 없음). E-Q15("UI 추가 안 함")를 철회하고 PRD 외 표시로 팀 확인만 받는다. Ollama `127.0.0.1` 바인딩·외부 pull 금지·digest 고정도 채택.
- **D 클라이언트 2차 방어**: 역할별 OpenAPI 타입 분리, 지원자 세션이 `rc-*` 청크를 요청하지 않는지 E2E, 금지 키 재귀 검사, 질문 음성 URL은 현재 attempt만 허용(다음 질문 선노출 방지), 이력서를 브라우저 저장소에 쓰지 않음, 리포트 미리 받기 금지(감사 로그 정확성), `MediaRecorder` 린트 금지, offer SDP `m=video` 부재 검사, CodeMirror 선택(Monaco CDN 로더 위험 회피).
- **D 가입 중복 문구**: "사용할 수 없는 이메일입니다." + E의 IP 속도 제한, 잔여 위험 기록(B9·D16·E-Q13 합의안).

## 4. 남은 쟁점 (합의 필요)

1. **Critical #1·#2 계약 변경**(runner에 기대 출력 비전달·밖에서 비교 / 인용은 공백 제거 1차 + 토큰 동일성 + 원문 범위 저장): A·B·C·F의 계약 수정 확인이 필요하다. 다수결 대상이 아니며, 바뀌기 전에는 COD-04·EVL-02를 "검증 완료"로 올리지 않는다.
2. **샌드박스 미검증 상태에서 코딩 켠 공고의 면접 시작**: E는 시작 전 차단(INT-14와 같은 위치, 지원자 간 [실행] 유무 차이 방지), A·D는 진행 후 [실행] 비활성. 도중 장애는 A·D안 수용.
3. **운영 샌드박스 최소 조건**: runsc 권장, 불가 시 rootless(또는 userns-remap) + 강화 옵션 + 운영 호스트 SBX 재통과. A-R9의 "runc 강화 플래그로 대체할지"에 대한 E의 답이며 F의 출시 조건 승인이 필요하다.
4. **TTS 전달 경로**: 서버 WebRTC 트랙(A·C, 실측 확인) vs HTTP 파일(D). HTTP를 고르면 #10 하한 규칙이 조건이다.
5. **[답변 완료] 채널**: 데이터채널(A·C) vs HTTP(D·E). 어느 쪽이든 서버가 attempt ID·상태·마감을 검증하고, HTTP면 CSRF·멱등 키를 적용한다.
6. **재출제 상한**(#9): OQ-21 확장으로 팀이 정한다. 그 전에는 기록·표시만 한다.
7. **텍스트 모드 진입 조건**(#8)과 INT-18 텍스트 제한시간(D 이견 7): 서버가 관측한 재연결 실패만 진입 조건으로 인정하는 데 B·C·D 동의 필요.
8. **단일화 항목**: 네트워크 유예 값(#12), CSRF 방식(#13), `style-src` 예외(#14), 시그널링 경로·오류 코드(#17), "10MB" 바이트 정의(B는 CHECK에 10,485,760 사용, D 이견 11, E-Q2와 같은 값으로 확정 요청).
9. **[실행]의 "정상 작동" 정의**(D 이견 13, E-Q1): E는 "제한 안에서 종료 코드 0"을 권장. 공개 예시 기대 출력 일치 여부 표시는 팀 결정.
