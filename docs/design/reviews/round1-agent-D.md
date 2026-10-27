# 설계 2단계 상호 리뷰 1라운드 — 에이전트 D(프런트엔드·UX)

- 대상: `agent-A-architecture.md`, `agent-B-backend-data.md`, `agent-C-ai-voice.md`, `agent-E-security-qa.md`. 참고: F 실측 `prototypes/README.md`, `prototypes/rtc_browser/index.html`·`server.py`.
- 초점: 프런트가 쓰는 API·이벤트 계약 불일치, 지원자/담당자 정보 혼합, 면접 상태 머신의 빈틈. PRD ID·수치·문구·AC는 바꾸지 않는다.
- 심각도: Critical(보안·데이터 손실·PRD 위반·실행 불가) / Major / Minor(취향 차이 포함).

## 1. 실측 결과로 바뀐 내 입장

| # | 1단계 D안 | 바뀐 입장 | 근거 |
|---|---|---|---|
| P1 | 질문 음성은 HTTP 파일 + `<audio>`(2.8 A안) | **WebRTC 하향 오디오 트랙**(A·C안)으로 바꾼다. IV-9(음성 파일 API)는 뺀다. TTS 실패·재생 차단은 INT-04대로 텍스트만 | S-1: 하향 트랙 5.2초 송출 → `tts_end` → 300ms 뒤 `mic_open` 동작. 서버가 송출 끝을 알아 게이트가 서버 기준이 되고, 크롬 에코 제거가 원격 트랙에는 확실히 적용된다 |
| P2 | 클라이언트가 TTS 종료 후 `open` 명령을 보냄(IV-10) | IV-10 삭제. **마이크 개방은 서버 단독 결정**(`mic_open`/`answer.open`). 클라이언트는 그 이벤트를 받은 뒤에만 "답변해 주세요"·[답변 완료] 활성 | S-1에서 게이트 이전 발화 5.46초를 서버가 폐기(AC-09 서버 측 충족). 클라이언트 신호로 게이트가 앞당겨지면 안 된다(E SC-45) |
| P3 | 클라이언트 마이크 트랙 `enabled=false`가 이중 방어 | 유지하되 **보조**로 격하. 권위는 서버 게이트 | 같은 실측 |
| P4 | 시계 동기화 전용 `GET /api/system/time`(AU-4) | AU-4 삭제. 데이터채널 `hello`·`pong`과 REST 응답의 `server_now`로 오프셋 계산(A 4.3, B 3.12) | 별도 API 불필요 |
| P5 | 스피커 테스트는 번들 샘플 음성(INT-02) | 점검 단계의 **하향 트랙 안내 음성**으로 겸용(C 3.3). 실제 경로를 시험하므로 더 낫다. 출력 장치는 원격 오디오 요소의 `setSinkId` | S-1 하향 트랙 동작 |
| P6 | 자막 영역은 "실시간 갱신" 가정 | 자막은 **휴지 후 1.46~1.70초**에 구간 단위로 온다. 말하는 동안은 마이크 레벨 막대 + "인식 중" 표시로 피드백하고, 구간이 오면 붙인다. 구간 자막은 `aria-live="polite"` 대상 | S-1 실측(8.2 목표 2초 안) |
| P7 | [실행]은 동기 응답(CD-3) | B의 비동기 `POST .../coding/runs` + 폴링 채택. "실행 중" 상태를 둔다 | 컨테이너 기동만 약 0.45초, 채점·실행 시간 가변 |
| P8 | 샌드박스 미검증 시 [실행]만 막고 작성·제출은 허용 | **E-Q3 채택**: 코딩 켠 공고는 면접 시작 전 점검에서 차단. 응시 도중 비활성되면 [실행] 비활성 안내 + 제출 허용(중간 차단 불가) | 실행 없는 코딩테스트는 지원자 간 공정성이 깨진다(E) |
| P9 | 담당자는 지원자 경로 전체 가드로 차단(이견 8) | **담당자도 OPEN 공고 목록·상세를 읽게 하고 [지원하기] 클릭 시 차단·안내** | AC-02 "[지원하기]를 누르면 막힌다"를 문자 그대로 시험하려면 버튼이 보여야 한다(아래 D-13) |
| P10 | `iceServers`는 `GET /api/system/rtc-config`에서 받음 | MVP는 클라이언트 코드에 `iceServers: []` 고정, PC는 `getUserMedia` 허용 **뒤에** 만든다. rtc-config는 TURN 도입 때 | S-1·loopback: 공개 STUN 기본값이 외부 통신 + 10.2초 지연 |

## 2. 리뷰 표

| ID | 대상 설계안·절 | 문제 | 심각도 | 근거·재현·테스트 | 제안 해결 |
|---|---|---|---|---|---|
| D-01 | B 3.6 `attempt_sweeper`, 3.7 하트비트 | 하트비트는 "음성 런타임이 WebRTC 연결 동안"만 갱신하는데 sweeper는 **CODING도 90초 단절이면 INTERRUPTED**로 바꾼다. 코딩 단계에는 WebRTC가 없으므로 코딩 시작 90초 뒤 모든 응시가 INTERRUPTED가 되고, 이후 `PUT .../coding/draft`·`runs`·`submit`이 409 `INVALID_STATE`로 막힌다 | **Critical** (COD-02~05 실행 불가, AC-15 실패) | 통합 테스트: CODING 진입 → WebRTC 없음 → 가짜 시계 120초 → draft PUT 기대 200. 현 설계대로면 409 | 코딩 API 호출(GET coding, draft PUT, runs, submit)과 신규 `POST .../heartbeat`(30초, D 3.8)가 `last_heartbeat_at`을 갱신. 또는 CODING을 sweeper 대상에서 빼고(제한시간은 서버 시계로 흐름) "브라우저 닫힘"은 INTERRUPTED 표시용으로만 기록 |
| D-02 | B 3.12 `POST .../interview/questions/{q}/finish`, E SC-46 `expected_question_seq`, A 4.3 `answer_complete{seq}` | 재연결 후에는 **같은 질문·같은 seq를 처음부터 재출제**한다(OQ-21). 끊기기 직전에 보낸(또는 keepalive·재전송된) [답변 완료]가 늦게 도착하면 질문 ID·seq가 같아 **새 출제분을 즉시 확정**한다 → 다시 말한 답변이 '답변 없음' 또는 잘린 텍스트로 저장 | **Critical** (답변 손실, INT-11 "저장된 답변은 잃지 않는다" 위반 경로) | 통합: Q4 답변 중 PC 강제 종료 → 오프라인 동안 finish 요청 보류 → resume → 새 출제 `mic_open` 직후 보류 요청 전달 → 현 계약이면 Q4 확정. 기대: 409 | 출제 1회마다 새 **답변 창 ID**(C의 `aid`)를 발급. finish·텍스트 답변·모든 DC 이벤트는 `aid`로 식별, 현재 `aid`가 아니면 409 `ATTEMPT_CLOSED`(재시도 금지). B `interview_questions`와 별도로 `answer_windows`(또는 `presentation_no`) 칼럼 필요 |
| D-03 | A 4.3, C 4.3, 프로토타입, D 4.3 | 데이터채널 프로토콜이 **세 벌**: 이름(`mic_open`/`answer.open`/`answer_opened`, `caption`/`caption.partial`, `answer_final`/`caption.final`), 시각 형식(A ISO `deadline_at`, C epoch ms), 식별자(A `seq`, C `qid`·`aid`). C에는 **면접 종료 이벤트가 없어**(A `next{to}`) 클라이언트가 코딩·완료 화면으로 갈 시점을 모른다. C에는 `hello`·`error`·다른 연결 인계·상태 변경(EXPIRED) 이벤트도 없다 | Major | 계약 테스트: 서버 `dc_protocol.py`의 메시지 스키마와 프런트 타입을 같은 JSON Schema 파일에서 생성하고, 양쪽이 모든 메시지 왕복 픽스처를 통과해야 함 | 단일 원천 `docs/trd`의 `dc-protocol v1`(JSON Schema). 제안 최소 집합: `hello{server_now, resume_from}`, `question{aid, no, followup, text, audio:"track"\|"none", limit_s}`, `tts_end{aid}`, `answer_open{aid, opened_at, deadline, warn_at, server_now}`(epoch ms), `answer_warn`, `caption_segment{aid, seg, text}`, `answer_final{aid, text, status}`, `processing{aid}`, `interview_end{next: CODING\|SUBMITTED}`, `status_changed{status}`, `superseded`, `error{code}`, `precheck_result{ok, text, reason}`, C→S `ping`/`pong` |
| D-04 | A 4.3 `/rtc/v1/sessions`, C 3.3 `/api/candidate/interviews/{id}/rtc/offer`, B(경로 없음), D IV-3·IV-8 | 시그널링 경로·식별자(지원 건 ID vs 면접 ID)·점검/면접 구분 방식이 모두 다름 | Major | 프런트 `rtcClient`가 하나의 경로만 구현 가능 | A안(`POST /rtc/v1/sessions {application_id, purpose: precheck\|interview, sdp}`) 채택 제안. 식별자는 **지원 건 ID** 하나로 통일(B의 IDOR 매트릭스·범위 헬퍼와 일치). Vite 개발 프록시에 `/rtc` 추가 |
| D-05 | A 4.3 `answer_complete`(DC), C 4.3 `answer.done`(DC, "불가 시 POST 대체") | DC 메시지는 응답이 없어 클라이언트가 전달 실패를 알 수 없고, "DC 불가"의 판정 기준이 없다. 반쯤 끊긴 채널에서 누른 [답변 완료]는 마감까지 무시된 채 지원자는 화면이 멈춘 것으로 본다 | Major | 통합: DC 송신 버퍼만 막는 장애 주입(`bufferedAmount` 증가) → 버튼 후 5초간 `answer_final` 없음 | **HTTP POST를 유일한 명령 경로**로(B 3.12, E SC-42·E-Q11, D 2.7). `aid` 멱등. DC는 서버→클라이언트 통지 전용 |
| D-06 | A 4.3 rtc 시그널링 인증 | rtc 프로세스는 "같은 세션 테이블 검증"만 명시. CSRF 토큰·Origin 검사가 없다. 서버는 새 offer가 오면 기존 연결을 닫으므로(SC-41) 교차 사이트 요청 하나로 **진행 중 면접 연결을 끊어 재출제를 유발**할 수 있다 | Major | 보안 통합: 다른 Origin 헤더 + 쿠키로 `POST /rtc/v1/sessions` → 기대 403 `CSRF_FAILED`, 기존 PC 유지 | api와 같은 인증·CSRF·Origin 미들웨어를 공유 패키지로 rtc에도 적용(E SC-40). SameSite=Strict만으로 대체하지 않음(E 2.6) |
| D-07 | B 3.4 "재연결 실패 30초 → INTERRUPTED", C 3.3 "DC 10초 무응답 → INTERRUPTED, 클라이언트 3회·20초", D T21~T23 | INT-18(P1)의 **텍스트 모드로 들어가는 API가 어디에도 없다**. B·C는 재연결 실패를 INTERRUPTED로 만드는데, B finish는 현재 문항만 받고 INTERRUPTED에서는 거부될 것. 텍스트 모드에는 WebRTC가 없어 하트비트도 없다(D-01과 같은 구조). 세 안의 시간값(10·20·30·90초)도 서로 맞지 않는다 | Major (P1 기능 불가) | 통합: 재연결 3회 실패 → 텍스트 답변 제출 → 기대 저장(`input_mode=TEXT`), 상태 INTERVIEWING | `POST .../interview/resume {mode: "voice"\|"text"}` 하나로 INTERRUPTED→INTERVIEWING(+`text_mode_since`). 텍스트 모드는 HTTP 하트비트와 `GET state` 폴링. 시간값은 한 표로: 서버 PC 상실 감지 즉시 현재 `aid` 무효(OQ-21) → INTERRUPTED는 X초 뒤(슬롯용) → 클라이언트 재연결 창 < 슬롯 유예(B 120초) |
| D-08 | B 3.12 `GET /api/postings/{id}`, E 3.6 AC-19·QG-02 | 지원자 금지 필드 검사 범위가 `/api/candidate/*`와 결과·목록·상세 중심이다. **`/api/postings*`, `/api/consent/*`, `/api/auth/me`, 시그널링 응답, 데이터채널 메시지**는 범위 밖. B 공고 상세의 필드가 정해지지 않았고, 공고 행은 `current_settings_id`(가중치·커스텀 질문)·`coding_problem_id`와 연결돼 있어 모델 통째 직렬화 시 가중치(SEC-04 위반)·커스텀 질문(면접 전 사전 노출) 누출 | Major | E2E·API: 가중치·커스텀 질문·문제 제목에 카나리아 → 지원자 세션의 **모든 HTTP 응답과 DC 메시지** 재귀 검사 0건(D 3.9 검사를 E QG-02로 승격) | `PublicPosting` 스키마 화이트리스트(제목·회사명·직무·요건·우대·업무·정원·마감·`coding_enabled`·`coding_time_limit_min`만). QG-02 대상 = "지원자 세션이 받는 모든 응답" |
| D-09 | C 3.5 T4 출력(`reason`, `anchor`), A 4.3 `question`, 프로토타입 `debug` 메시지 | 꼬리질문 판단 결과의 `reason`(INCONSISTENT, RESUME_MISMATCH 등)·`anchor`·`source`(BANK_FALLBACK)·`grounding`은 내부 판정 정보다. `question` 메시지가 판단 객체를 그대로 실으면 지원자에게 "이력서와 다르다고 판단됨"이 전달된다. 프로토타입의 `debug{dropped_before_gate_s}`도 운영에 남으면 안 됨 | Major (6.6·SEC-04 취지) | DC 메시지 키 허용목록 테스트(D-08 검사에 포함), `debug` 타입은 운영 프로필에서 송신 시 실패 | `question`은 `aid, no, followup, text, audio, limit_s`만. 판단 로그는 B 테이블에만 |
| D-10 | B 3.12 오류 형식 `{"error":{code,message,request_id}}`, E 4 `AUTH_FAILED`·`CODE_EXECUTION_UNAVAILABLE`, B `INVALID_CREDENTIALS`·`RUNNER_UNAVAILABLE`·`RUN_IN_PROGRESS`, A `PREP_IN_PROGRESS` vs B `QUESTIONS_NOT_READY` | 같은 상황에 코드 이름이 2~3개. 프런트는 **코드로 고정 문구**(INT-13 등)를 고르므로 이름이 어긋나면 일반 오류 문구가 나온다. 422의 칸별 오류 형식도 B에 없다 | Major | 계약 테스트: OpenAPI의 오류 스키마 + 코드 열거형(enum)을 프런트 타입으로 생성, 미등록 코드 사용 시 서버 테스트 실패 | 오류 코드 레지스트리 1개(`app/core/errors.py` enum → OpenAPI). 형식 `{"error":{"code","message","request_id","fields":[{"loc","msg"}]}}`. 이름은 B 기준 + E의 `CSRF_FAILED`·`ROLE_FORBIDDEN` |
| D-11 | B 3.12 `PUT .../coding/draft {code, base_revision}` → 409 `DRAFT_CONFLICT` | 낙관적 잠금이라 **진행 중 저장과 겹친 저장**(디바운스 저장 + `pagehide` keepalive 저장, 탭 2개)이 충돌로 거부된다. 거부된 쪽이 최신 코드면 COD-05 자동 제출은 **옛 코드**를 낸다 | Major (마지막 입력 손실) | 통합: base 5로 PUT 2건을 50ms 간격 동시 전송(두 번째가 최신) → 저장된 코드가 두 번째여야 함 | 클라이언트 단조 증가 `client_seq`를 보내고 서버는 "더 큰 seq만 반영, 작으면 200 무시"(마지막 쓰기 승리). 다른 탭은 `superseded` 처리 |
| D-12 | E 3.3 CSP `style-src 'self'` | CodeMirror 6(`style-mod`)은 런타임에 스타일을 넣고, 흔한 대화상자 라이브러리(react-remove-scroll 계열)는 `<style>` 태그를 주입한다. 막히면 **편집기 화면이 깨져 코딩테스트 불가** | Major | Playwright: 운영 헤더로 코딩·면접·담당자 화면 순회, `securitypolicyviolation` 이벤트 0건 | D는 대화상자를 네이티브 `<dialog>`로(라이브러리 주입 없음). CodeMirror는 `adoptedStyleSheets` 경로가 CSP에 걸리는지 위 테스트로 확인, 걸리면 서버가 index.html에 nonce 주입 + `EditorView.cspNonce` |
| D-13 | E 3.5 M-01(담당자 → 공개 공고 403), E 3.6 AC-02 절차 ④, D 1단계 이견 8 | M-01이 403이면 담당자 화면에 공고 상세·[지원하기]가 나올 수 없어 AC-02 ④ "X1이 [지원하기] 클릭"을 실행할 수 없다(E 문서 안의 모순) | Major (AC 시험 불가) | AC-02 E2E 시나리오를 그대로 돌리면 ④ 단계에서 요소 없음 | E-Q10 타협안 채택: OPEN 공고 목록·상세 GET은 모든 로그인 사용자 200(`PublicPosting`만, D-08). `POST .../applications`는 담당자 403 `ROLE_FORBIDDEN` → 화면 USR-05 안내. 매트릭스 M-01 기대값 수정 |
| D-14 | A 3.3, C 3.3 TTS 하향 트랙 | 새로고침 재진입 시 원격 오디오 요소 자동재생이 막히면 서버는 송출했다고 보고 300ms 뒤 마이크를 연다. 지원자는 질문 음성을 못 들은 채 답변 창에 들어간다. C의 M1 측정도 이 경우를 모른다 | Minor (INT-04 텍스트 진행은 성립) | E2E: 새로고침 후 클릭 없이 진입 → `audioEl.play()` 거부 확인 | 재진입은 D의 RESUME_GATE 클릭을 거친 뒤 시그널링. 클라이언트가 `client_audio{aid, state: playing\|blocked}`를 진단용으로만 보고(게이트에는 쓰지 않음) |
| D-15 | B 3.12 지원자 표시 상태 | ON_HOLD를 "평가 진행 중"으로 합치는 것은 찬성(D 이견 6과 같은 방향). 단 "DONE+UNDECIDED → 검토 중"은 7.3 용어 '검토 전'과 다르다. `progress`에 한국어 표시 문자열을 내리는 것도 문구 변경 때 API 변경을 부른다 | Minor | — | API는 코드(`attempt_status`, `public_eval_status: IN_PROGRESS\|DONE`, `decision`)만, 문구는 프런트 `fixed.ts`. 결정 상태 표기는 '검토 전' |
| D-16 | C 3.6(공백·문장부호 제거 정규화), E SC-72(NFKC·공백 축약만), B `answer_evaluations.quote` | 정규화 규칙이 C·E 사이에서 충돌. 실측(6/20 → 20/20)은 C 쪽을 지지한다. 어느 쪽이든 LLM 인용 문자열은 원문과 표기가 달라 프런트가 원문에서 위치를 찾을 수 없다 | Minor (강조 표시 품질) | 단위: 정규화 후 일치한 인용을 프런트 `indexOf`로 찾으면 실패하는 사례 수집 | 서버가 원문 좌표 `span{start,end}`를 저장·응답(C 3.6-9의 위치 대응표). 프런트는 좌표로만 강조. 규칙은 ADR-0008 하나로 |
| D-17 | A 3.3(유예 1.5초), C 3.3(0.5초), E SC-44(1.5초) | 마감 유예 값이 다름 | Minor | 서버 단위(가짜 시계) | 값 하나를 설정값으로. 프런트는 `deadline`(유예 제외)에서 0초를 표시하고 확정은 `answer_final`로만 |
| D-18 | E 2.6 `GET /api/auth/session`, B `GET /api/auth/me` | CSRF 토큰을 받는 경로 이름이 다르고, 로그인 때 세션 회전 시 토큰도 바뀐다는 점이 프런트 계약에 없다 | Minor | E2E: 로그아웃→재로그인 직후 첫 POST 성공 | `GET /api/auth/me` 응답에 `csrf_token` 포함, 로그인 응답에도 포함. 프런트는 401·로그인 뒤 토큰 갱신 후 대기 중 변경 요청을 재시도하지 않고 사용자에게 다시 실행하게 함 |
| D-19 | E SC-24·E-Q4(PDF 기본 `attachment`) | RES-08 "원본 PDF를 본다"를 다운로드로만 충족하면 담당자가 파일을 로컬에 남긴다(개인정보 잔존) | Minor | — | 그 엔드포인트만 `inline` + `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN`으로 크롬 내장 뷰어 표시. 다운로드는 별도 버튼 |
| D-20 | C 3.3 점검 통과 조건(기준 문장 bigram Dice ≥ 0.4) | 발화 인식 "확인"(INT-17)보다 엄격해 잡음·억양 조건에서 정상 사용자가 시작을 못 할 수 있다(AC-08상 실패 = 시작 불가) | Minor | 팀 녹음(D4)으로 거짓 실패율 측정 | 임계값 설정값화 + 인식 텍스트를 화면에 표시 + 3회 재시도(C안 유지). 거짓 실패율을 X-11 ADR에 기록 |
| D-21 | B 3.12 `GET /api/postings/{id}` `has_applied`, B 지원 건 API | `has_applied`만으로는 [지원 현황 보기] 링크에 쓸 `application_id`가 없다. INT-03 안내(코딩 여부·제한시간·문항 수)를 줄 지원자 API가 없다 | Minor | — | `my_application{id, attempt_status}\|null` 반환. `GET /api/candidate/applications/{a}`에 `coding{enabled, time_limit_min}` 포함 |
| D-22 | A 4.3·C 3.3 하트비트(DC `ping` 2초, 10초 무응답) | 백그라운드 탭(INT-16 이탈 상황)에서 브라우저 타이머가 늦춰지면 클라이언트 `ping`이 끊긴 것처럼 보일 수 있다 | Minor | E2E: 면접 중 탭 전환 3분 → INTERRUPTED 미발생 확인 | 생존 판정은 서버 측 ICE·RTP 수신 상태를 우선, DC `ping`은 보조. 탭 복귀 시 클라이언트가 즉시 `GET state`로 동기화 |

## 3. 다른 설계안에서 받아들이는 점

- **A**: 시그널링 단일 엔드포인트 + `purpose`(precheck/interview), `hello{server_now, resume_from}`, 마감 원본은 DB `answer_deadline_at`, rtc 분리와 `/rtc` 경로(개발 프록시 포함), 정적 번들을 Caddy가 서빙.
- **B**: 점수는 문자열 저장값 + 표시값 동시 제공(OQ-10), 지원자 결과 `feedback: null`(결정 전) 구조, ON_HOLD 비노출, 업로드 응답의 동기 `text_extractable`(RES-04 즉시 안내), 추출 결과 경로, 비동기 코드 실행, 서버 측 가중치 초기화(`POST .../reset`), 공고 수정 `If-Match row_version`과 결정 `expected_current`(409 시 재조회 UI), CODING 진입 시 슬롯 반환, 마감 후에도 진행 중 응시는 계속(B3 → D T26은 끊긴 뒤에만 발생), `leave-prepare`.
- **C**: 답변 창 ID `aid`, `question`의 `audio: "track"|"none"`(서버가 TTS 실패를 알림), `processing` 이벤트, 점검 하향 음성으로 스피커 겸용 시험, VAD는 구간 컷 전용(침묵 종료 없음).
- **E**: 동기화 토큰 CSRF + Origin 검사, `__Host-sid; SameSite=Strict`(SPA는 첫 요청이 정적 파일이라 Strict여도 동작), 엄격 CSP(그래서 스타일 주입 라이브러리를 피함), `data-testid` 안정 선택자, [실행] "정상" = 제한 안 종료 코드 0, 출력은 64KiB 잘라 텍스트로만(E-Q1, D 이견 13과 같음), 10MB = 10,485,760바이트(E-Q2), 카나리아 기반 누출 검사, 담당자 화면 텍스트 렌더링만(SC-30~33).
- **F 실측**: `iceServers=[]` 양쪽 고정, 하향 TTS 300ms 여유, 자막 1.5~1.7초 지연을 화면 설계 기준값으로 사용.

## 4. 남은 쟁점 (합의 필요)

1. **데이터채널 프로토콜 v1 단일 원천**(D-03): 소유자(C 제안), 이름·필드·epoch ms·`aid` 확정. A·C·D가 같은 JSON Schema에서 코드 생성.
2. **[답변 완료]·텍스트 답변 명령 경로**(D-05): HTTP 전용(B·E·D) vs DC(A·C). D는 HTTP 전용 + `aid` 멱등을 요구.
3. **답변 창 ID와 재출제**(D-02): B 스키마에 출제 회차 식별자 추가, 모든 명령·이벤트에 `aid`.
4. **하트비트·INTERRUPTED 규칙 표**(D-01, D-07, D-22): CODING·텍스트 모드의 생존 신호 출처, 서버 감지 시각, 슬롯 유예(120초), 클라이언트 재연결 창을 한 표로. D-01은 구현 착수 전 필수.
5. **텍스트 모드 진입 API**(D-07): `resume {mode}` 안. 텍스트 모드 제한시간은 PRD 값(60/45초) 유지 여부를 팀이 확인(D 이견 7).
6. **시그널링 경로·식별자**(D-04): `/rtc/v1/sessions` + 지원 건 ID, rtc에 CSRF·Origin 적용(D-06).
7. **오류 코드 레지스트리·422 형식**(D-10).
8. **지원자 응답 검사 범위**(D-08, D-09): QG-02를 "지원자 세션이 받는 모든 HTTP 응답 + DC 메시지"로 확대, `PublicPosting` 화이트리스트.
9. **AC-02와 M-01**(D-13): 담당자의 공개 공고 읽기 허용으로 매트릭스 기대값 변경 여부.
10. **CSP와 편집기**(D-12): `securitypolicyviolation` 0건 테스트 결과로 nonce 필요 여부 결정.
11. **코딩 임시저장 동시성**(D-11): `client_seq` 마지막 쓰기 승리 vs `base_revision` 낙관적 잠금.
12. **샌드박스 미검증 시 동작**(P8, E-Q3): D는 E안 수용. A·B 확인 필요.
13. **원본 PDF 보기 방식**(D-19, E-Q4)과 **지원자 평가 상태 문구**(D-15): 팀 문구 결정.
