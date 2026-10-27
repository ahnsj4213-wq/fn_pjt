# 설계 상호 리뷰 1라운드 — 에이전트 A (아키텍처·기술 리드)

- 대상: `proposals/agent-B-backend-data.md`, `agent-C-ai-voice.md`, `agent-D-frontend-ux.md`, `agent-E-security-qa.md`. 내 1단계안 `agent-A-architecture.md`도 자기 검토 대상이다.
- 근거: F 실측(`prototypes/README.md`, `results-*.txt`, 2026-10-26), Docker 공식 문서(userns-remap 제약, rootless 네트워크 문제 해결). [추정]은 실측하지 않은 값이다.
- 심각도: **Critical**(보안 취약점·데이터 손실·PRD 위반·실행 불가능) / **Major** / **Minor**(취향 차이 포함). PRD 기능 ID·우선순위·수치·AC는 바꾸지 않는다.

## 1. 실측 결과로 바뀐 내 입장

| # | 항목 | 1단계 입장 | F 실측 근거 | 바뀐 입장 |
|---|---|---|---|---|
| 1 | 프로세스 수 | 10개 | S-1(Windows aiortc 동작), S-3(연결 종료로 생성 취소 동작), STT GPU 필요 | **7개로 축소** (1.1) |
| 2 | STT 위치 | CPU 기본, GPU는 VRAM 여유 시 | CPU small 2.0~2.6초/구간(30초 창 고정 비용), CPU turbo 9~11초. GPU turbo 1.1~1.4초, S-1 자막이 휴지 후 1.46~1.70초에 도착 | **GPU turbo를 기본**으로. CPU small은 GPU 불가 시 대체(자막이 늦어도 면접은 계속, 8.3) |
| 3 | `OLLAMA_NUM_PARALLEL` | 1 + 선점, 2는 비교 대상 | 취소 직후 짧은 요청 0.49초, 취소 없을 때 11.06초 대기 | **1 + 취소 선점 확정**. 2는 VRAM 실측 없이 채택하지 않음 |
| 4 | `num_ctx` | 8192 고정 | 값이 바뀌면 5.1초 재적재. 4096→8192는 +214MiB | 전역 1개 값으로 고정 확정. 값은 STT를 함께 올린 상태의 VRAM 실측 후 정함. 1순위는 4096 + 이력서 청크 분할 |
| 5 | 임베딩 | CPU 강제 | `num_gpu=0` 임베딩이 LLM을 내리지 않고 공존 | 확정. 별도 CPU 인스턴스 불필요 |
| 6 | 5초 예산 | STT 1.0 / LLM 3.0 / TTS 0.8초 [추정] | 판단 웜 0.57초, TTS RTF 0.25~0.32(5.2초 음성 1.5초), STT GPU 1.1~1.4초 | 마지막 구간 STT 1.4 + 선점 0.5 + 판단 0.6 + 꼬리 TTS 1.5~2.2 + 여유 0.3 ≈ **3.5~4.6초**. 병목은 LLM이 아니라 비스트리밍 꼬리질문 TTS → OQ-22 "텍스트 먼저" 유지 |
| 7 | TTS 하향 경로 | WebRTC 오디오 트랙 | S-1: 트랙 송출 → `tts_end` → 300ms 뒤 `mic_open`, 게이트 이전 발화 5.46초를 서버가 폐기 | 유지(근거 강화). HTTP 오디오는 텍스트 모드와 트랙 실패 대체로만 |
| 8 | 클라이언트 명령 채널 | DataChannel | 실측과 무관. D·E가 HTTP를 권장(CSRF·멱등·감사) | **HTTP로 변경**. DataChannel은 서버→브라우저 이벤트 전용 |
| 9 | runner 채점 계약 | runner가 기대 출력과 비교 | 컨테이너 1회 기동 약 0.45초, 강화 옵션 12/12 차단 | **E의 G-b 채택**: 테스트 1건당 컨테이너 1개, 비교는 worker. 20건 ≈ 9초 + 실행 시간 [추정, runc] |
| 10 | aiortc ICE | 언급 없음 | 기본값이 Google STUN → 첫 프레임 10.2초 + 외부 통신. `iceServers=[]`면 0.17초 | `iceServers=[]`를 기동 시 fail-closed 검사 항목으로 추가(SEC-06, AC-22) |
| 11 | 기동 | 워밍업만 언급 | LLM 콜드 로드 약 40초 | `ai`의 `/readyz`는 LLM·STT 적재 완료 전까지 false. 면접 시작 게이트(INT-14)가 이 값을 봄 |
| 12 | 개발 환경 | — | ctranslate2가 cuBLAS·cuDNN을 PATH로만 찾음 | 개발 기동 스크립트가 `ai` 프로세스의 PATH를 설정 |

### 1.1 자기 재검토: 프로세스 10개 → 7개

| 새 구성 | 합친 것 | 이유 | 감수하는 것 |
|---|---|---|---|
| `app` (FastAPI, uvicorn workers=1) | api + rtc | STT가 GPU 프로세스로 빠져 rtc의 CPU 부하는 Opus 디코딩·리샘플·VAD만 남는다. 시그널링(C·D 모두 `/api` 경로), HTTP 명령(D·E), 서버 측 하트비트(B "음성 런타임")가 한 프로세스에 모이면 프로세스 간 중계가 없어진다 | app 재시작이 진행 중 면접을 끊는다(INT-11 재진입으로 흡수). 운영에서 API도 host 네트워크를 같이 쓴다 |
| `ai` | ai-gw + speech(STT·TTS) | GPU를 쓰는 쪽이 LLM·STT 둘이 되어 **GPU 중재자가 하나**여야 한다. Supertonic(ONNX, CPU)이면 TTS용 별도 가상환경이 필요 없다(C의 MeloTTS 격리 사유가 사라짐). OpenRAIL-M 라이선스 검토는 남는다 | `ai` 장애 = LLM·STT·TTS 동시 장애. 어차피 INT-14가 함께 시작을 막는다 |
| `worker` | worker-ai + worker-sys | B의 `jobs.resource`(LLM/RUNNER/CPU/NONE)별 세마포어로 한 프로세스 안에서 분리할 수 있다 | 프로세스 단위 격리 |
| 유지 | caddy, ollama, runner, postgres | runner는 Docker 소켓 단독 소유(E TB-5). ollama는 외부 런타임 | — |

- 직접 작성하는 프로세스는 app, worker, ai, runner 4개다.
- app 규칙: 이벤트 루프에서 10ms 넘는 동기 작업은 하지 않는다(Argon2는 스레드풀, PDF는 worker). 루프 지연 50ms 초과는 경고 로그로 측정한다.

## 2. 리뷰 표

| # | 대상 설계안·절 | 문제 | 심각도 | 근거·재현·테스트 | 제안 해결 |
|---|---|---|---|---|---|
| R1 | E 2.1·4절(A 계약)·E-Q9 × A 3.5 | E가 운영 필수로 정한 "rootless 또는 userns-remap"과 aiortc의 host 네트워크 요구를 함께 적용하면 동작하지 않는다. ① Docker 문서: userns-remap 데몬은 `--network=host` 공유와 호환되지 않는다(컨테이너별 `--userns=host`로만 해제). ② rootless는 Engine 29.5 미만에서 host 네트워크가 RootlessKit 안에 갇혀 실제 호스트에서 닿지 않는다. ③ aiortc는 포트 범위를 지정할 수 없어 포트 publish로도 우회할 수 없다 | **Critical** | 운영 후보 Linux에서 userns-remap을 켠 채 `docker run --network=host` → 거부되는지 확인. 사내망의 다른 PC 브라우저로 S-1 재실행 | 격리 강화는 샌드박스 데몬에만 적용한다. (a) userns-remap + app 컨테이너만 `userns_mode: host`, (b) 샌드박스 전용 rootless 데몬(소켓은 runner만) + app은 rootful 데몬 host 네트워크, (c) app을 systemd 네이티브 서비스로, 셋 중 하나. Engine 버전을 고정하고 "운영 호스트 S-1 통과"를 출시 조건에 추가 |
| R2 | C 3.1·3.3 STT 1순위·CPU 배분 | CPU small int8을 1순위로 두고 2초 자막을 기대한다 | Major | 실측 CPU small 2.0~2.6초/구간. VAD 휴지 0.5초와 전송을 더하면 2초를 넘는다. GPU turbo는 1.1~1.4초, S-1 종단 1.46~1.70초 | GPU turbo를 기본, CPU small을 대체로 둔다. PRD 8.1 "CPU에서 돌 수 있는 모델"은 turbo도 CPU 실행이 가능해 위반은 아니지만, 의도 해석이 필요해 팀 OQ로 올린다 |
| R3 | C 3.4, A 3.4 (공통 누락) | STT가 GPU로 오면 LLM 배치 생성과 STT가 같은 GPU를 시분할한다. 경합 상태의 STT 지연은 측정되지 않았다(F 측정은 LLM이 적재만 된 유휴 상태) | Major | **S-10(신규)**: LLM 600토큰 스트리밍 생성 중 turbo로 13.5초 구간 STT → 지연 측정. 기준: 자막 p95 ≤ 2초(8.2) | `ai`가 STT 요청 도착 시 진행 중 BATCH LLM을 취소(S-3 방식)하거나, LISTENING 중인 응시가 있으면 BATCH를 짧은 단위로만 허용한다. EVL-05 진행 속도와 맞바꾸는 문제라 S-10 결과로 결정 |
| R4 | C 3.1·3.4 `NUM_PARALLEL=2`, `num_ctx=8192` | 병렬 2 × 8192는 KV 슬롯이 2배다 | Major | 실측: LLM 2,995MiB(4096·병렬 1), 장비 최대 4,172MiB(데스크톱 454 포함). 여기에 turbo STT VRAM이 더해지는데 측정값이 없다. VRAM을 넘으면 Ollama가 층을 CPU로 내려 지연이 급증한다 | 병렬 1 + 취소 선점(실측 0.49초). 시험: 채택 설정의 LLM + turbo STT + 데스크톱 상태에서 nvidia-smi 최대치, `ollama ps`의 size_vram = size, 여유 ≥ 300MiB [추정 기준] |
| R5 | B 4절(A 계약) "LLM 작업 동시성 1" | 작업 단위 직렬화가 GPU 직렬화를 대신한다. 수분짜리 `evaluate_application` 뒤에 `extract_resume`·`compose_questions`가 막혀(head-of-line), 지원자가 이력서 추출과 `QUESTIONS_NOT_READY`에서 수분 대기한다 | Major | 재현: 평가 작업 실행 중 PDF 업로드 → 추출 완료까지 시간 측정. 문항 평가 6~11초 × 14 + 종합 ≈ 2~3분 [실측 기반 추정] | worker의 LLM 작업 동시성을 레인당 1(IA 1, BATCH 1)로 둔다. 실제 GPU 순서는 `ai`가 호출 단위로 정한다(IA가 BATCH를 선점). jobs에 `lane` 칼럼 또는 kind→lane 매핑 |
| R6 | C 4.3, D 4.3, A 4.3 | DataChannel 메시지 스키마가 세 벌이다(`question.present`/`question`, `answer.open`/`answer_opened`/`mic_open` 등) | Major | 통합 시 이름·필드 불일치로 화면이 멈춘다(D FR-03). 계약 테스트가 없다 | 단일 스키마 파일(Pydantic → JSON Schema → TS 타입 생성), 소유 C. 이름은 가장 완전한 D안(hello, question, answer_opened, caption_partial, caption_segment, answer_finalized, interview_completed, status_changed, session_superseded)에 C의 `answer_warn`·`processing`을 더한다. A안 이름은 철회 |
| R7 | B 3.2 interviews·interview_answers, C 4.3 `aid`, D `attempt_id`, E SC-46 `expected_question_seq` | 문항 "제시 1회"를 가리키는 식별자가 없다. C는 답변 행이 생기기 전부터 `aid`를 쓰는데 B는 확정 시에만 답변 행을 INSERT한다. D의 `attempt_id`는 B의 `attempt_status`(응시)와 이름이 겹친다 | Major | 재출제(OQ-21) 뒤 늦게 도착한 자막·완료 명령이 새 제시에 섞이는 경합. 테스트: 재연결 직후 이전 제시의 done 재전송 → 409 | 제시마다 서버가 발급하는 `turn_id`(UUID)를 둔다. `interviews.current_turn_id`, `interview_answers.turn_id UNIQUE`. DataChannel 이벤트·HTTP 명령·멱등 키를 모두 turn_id 기준으로. "attempt"라는 말은 응시에만 쓴다 |
| R8 | A 4.3, C 3.3, D IV-3·IV-8 | 시그널링 종단이 서로 다르다: A `/rtc/v1/sessions`, C `/api/candidate/interviews/{id}/rtc/offer`(api가 중계), D `…/precheck/webrtc`·`…/interview/rtc/offer` | Major | 프로세스를 나누면 api→rtc로 SDP를 중계하고 명령을 전달하는 경로가 따로 필요하다 | app 통합(1.1)으로 해소. 경로는 D안을 채택해 B 표에 추가. A안 철회 |
| R9 | D 2.8·T11·IV-10 | HTTP 오디오 방식에서는 클라이언트의 IV-10이 마이크 개방을 앞당길 수 있다 → E SC-45 위반, AC-09 우회 경로 | Major | 재현: 질문 제시 직후 IV-10 호출 + 발화 → 답변 텍스트에 들어가는지 확인 | 기본은 트랙(S-1 검증). HTTP 대체 경로에서도 서버가 `opened_at = max(IV-10 수신, 제시 시각 + TTS 길이 + 0.3초)` 하한을 강제 |
| R10 | B 3.12 resume-files 응답 `text_extractable`, 3.2 `resume_files.text_extractable NOT NULL` | 업로드 응답에서 바로 판정하려면 API 프로세스가 PDF를 파싱해야 한다. E 2.7 P-a(기각안)와 같다. app 통합 시 면접 음성 루프까지 영향받는다 | Major | 재현: 압축 폭탄형 PDF(E AC-06 픽스처) 업로드 중 다른 API·자막 지연 측정 | 202 `{file_id, extraction_id}`(D CA-7). 판정은 worker 자식 프로세스(E SC-22)에서 하고 결과는 `NO_TEXT` 상태(D CA-8). 칼럼은 NULL 허용 |
| R11 | B 4절(C 계약) `grade(code, all_cases)`, A 4.4, E 2.3 G-b | 기대 출력이 runner·컨테이너 경계를 넘는지가 세 안에서 다르다 | Major | E SBX-11(카나리아)로 검증할 수 있다 | E 계약 채택: runner에는 stdin만 보내고 비교는 worker. A 계약 철회. B는 runner 호출 DTO에서 expected를 뺀다 |
| R12 | E SC-72 인용 정규화 | "NFKC, 공백 축약, 앞뒤 공백 제거"만 허용한다 | Major | F 실측: LLM이 "APM 으로", "API 의"처럼 원문에 없는 공백을 넣어 원문 비교 6/20. 공백 축약으로는 넣은 공백이 남아 정상 인용 다수가 '근거 부족' 1점이 되고 점수가 왜곡된다(6.4) | F ADR-0008(NFKC + 공백 전부·일부 문장부호 제거, 20/20)을 채택. E의 범위 제한(해당 문항 답변만)과 최소 길이는 유지. C의 동의어 사전은 오탐 위험(E 지적) 때문에 보류 |
| R13 | B 3.3 `forbid_mutation`의 `app.purge` | 어느 DB 세션이든 `SET LOCAL app.purge='on'`으로 불변 트리거(감사 로그·결정 이력·답변 포함)를 우회할 수 있다. B도 역할 조건을 "권장"으로만 적었다 | Major | 재현: `app_api` 연결에서 SET LOCAL 후 `audit_logs` DELETE. E SC-68(앱 롤의 UPDATE·DELETE 회수)과 충돌 | 트리거에 `current_user = 'app_purge'` 조건을 필수로 넣고, 보관 삭제는 전용 롤로만. `audit_logs`·`decision_history`는 우회 대상에서 뺀다 |
| R14 | C 3.3 재접속(10초 무응답 → INTERRUPTED), B 3.4(재연결 실패 30초, 하트비트 90초, 슬롯 유예 120초), D T4·T7·T22(연결 10초 × 3회, 1·2·4초 간격) | 끊김 판정·재연결 타이밍이 서로 다르다. 서버가 아직 INTERVIEWING인데 D가 IV-6 resume을 부르면 B 전이표(INTERRUPTED에서만 허용)상 409가 난다 | Major | 재현: 새로고침 → 3초 안에 재진입 → resume이 409인지(AC-13) | 단일 타이밍 표(소유 C·B). 같은 지원 건의 새 offer는 상태와 무관하게 기존 PC를 대체(supersede)한다. resume API는 INTERRUPTED·INTERVIEWING 둘 다 멱등으로 허용 |
| R15 | C 3.4, B 3.6 "10분은 시도 시작부터" | `ai` 선점으로 배치 호출이 기다린 시간도 시도의 10분에 들어간다 | Minor | 측정: 상한 2 부하에서 `evaluate_application` 시도 시간 분포 | EVL-06 수치는 그대로 둔다. 선점 대기 누계를 run에 기록해 원인 분석에만 쓴다 |
| R16 | B 3.5 OQ-15 "CODING 진입 시 슬롯 반환" | OQ-15 임시안과 다르다 | Minor | 근거 타당: 코딩은 STT·LLM을 쓰지 않는다 | 수용하고 팀에 OQ-15 갱신을 요청. D IV-15 코딩 하트비트는 슬롯용이 아니라 INTERRUPTED 표시용 |
| R17 | B 3.12, D 4.1·4.2, E 4절 | 오류 봉투(`{"error":{…}}` vs `{code,…}`)와 코드명(`INVALID_CREDENTIALS`/`AUTH_FAILED`, `HAS_APPLICATIONS`/`HAS_APPLICANTS`, `RUN_IN_PROGRESS`/`RUN_BUSY`)이 다르다 | Minor | OpenAPI 계약 테스트로 검출 | B가 오류 코드 목록을 단일 소유하고 D·E는 참조 |
| R18 | B `coding_runs` 비동기 + 폴링, D CD-3 동기 | [실행]의 동기·비동기가 다르다 | Minor | 실행 1회 = 기동 0.45초 + 코드 실행 ≤ 10초 | 동기(타임아웃 15초) + B의 "진행 중 1건" 유일 제약 유지 |
| R19 | C 2.4·3.1 MeloTTS + 별도 가상환경 | 실측된 TTS는 Supertonic-3(RTF 0.25~0.32, 되읽기 CER 0)이다 | Minor | F 결과 | Supertonic을 1순위로 바꾸면 별도 프로세스가 필요 없다. OpenRAIL-M 검토는 C 지적대로 남김 |
| R20 | B 4절(A) "프로세스 = api·worker·runner, DB가 유일한 통합 지점" | 음성 런타임, `ai` 프로세스, RT LLM 경로가 B 모델에 없다 | Minor | — | 1.1 구성을 반영. app이 B의 `app_api` 롤로 음성 쪽 쓰기(`confirm_answer`, heartbeat)까지 수행 |
| R21 | A 1단계안 자체 | 프로세스 10개, CPU STT 가정, DataChannel 명령, runner 비교, 시그널링 경로 | Major(자기) | 1절 | 1절대로 개정한다(2라운드에 반영) |

## 3. 다른 설계안에서 받아들이는 점

- **B**
  - `jobs.resource` + `lease_token` 펜싱 + `infra_deferrals`·`lost_leases` 분류. 내 `requires`·`run_no` 제안을 대체한다.
  - 슬롯 확보: advisory lock + 임대 테이블.
  - 상태 전이 표를 시드 테이블 하나로 두는 단일 원천.
  - 기준 시계는 DB `now()`.
  - 복합 FK `(id, company_id)`.
  - CODING 진입 시 슬롯 반환.
  - 7.1 표에 없는 전이(B1·B2)를 OQ로 올린 것.
- **C**
  - 턴 상태 기계(PRESENTING/LISTENING/FINALIZING/DECIDING).
  - 모든 요청에 `think=false` 강제.
  - 고정 접두부 → 비신뢰 입력 순서의 프롬프트 배치(접두부 캐시).
  - manifest 해시로 EVL-08 기록.
  - Whisper 환각 차단 목록.
  - 점검 단계에서 하향 TTS까지 확인.
  - EVL-05를 P0 일정에 함께 구현하자는 권고(A와 같은 결론, 우선순위 표기는 유지).
- **D**
  - 명령은 HTTP + `Idempotency-Key`.
  - 상태는 IV-7 스냅샷으로 복구하고 이벤트는 증분일 뿐이라는 원칙.
  - `session_superseded`.
  - `GET /api/system/rtc-config`.
  - test 프로필 전용 라우트(서버 측 WebRTC 강제 종료 등).
  - CodeMirror 6 + 외부 CDN 금지 검사.
  - offer SDP에 비디오 m-line이 없는지 E2E로 검사.
- **E**
  - 신뢰 경계 TB-1~8.
  - runner 계약 G-b와 SBX-01~14, 지문(fingerprint).
  - `CODE_EXECUTION_ENABLED` 기본 false.
  - fail-closed 기동 검증(SC-61).
  - compose secrets(SC-62).
  - AC-22를 로깅 DNS + `internal: true`로 판정.
  - PDF 자식 프로세스 상한.
  - 원격 후보는 사설 대역만 수락.
- **F**: 위 실측값 전부. 특히 S-1(서버 게이트)과 S-3(취소 선점)이 내 위험 A-R1·A-R3의 상당 부분을 해소했다. 남은 것은 운영 Linux와 장시간·다른 PC 조건이다.

## 4. 남은 쟁점 (합의 필요)

1. **프로세스 구성**: 7개안(app = api+rtc, ai = 게이트웨이+STT+TTS, worker 단일) 채택 여부. C는 voice 분리, B는 api 내 런타임을 가정했다.
2. **운영 Docker 보안 모드**와 app 네트워크(R1): 아래 셋 중 하나를 E와 합의. 운영 호스트 S-1을 출시 조건에 넣을지도 정한다.
   - userns-remap + `userns_mode: host`
   - 샌드박스 전용 rootless 데몬
   - app을 네이티브 서비스로
3. **GPU 공유 정책**(R3·R4): STT GPU 기본화의 PRD 8.1 해석(팀 OQ), S-10 결과에 따른 LISTENING 중 BATCH 처리, `num_ctx` 값(4096 vs 8192), 병렬 1.
4. **TTS 경로**: 트랙 기본 + HTTP 대체(A·C) vs HTTP 기본(D). 8.1 "주고받는다" 해석은 팀 OQ.
5. **실시간 계약 단일화**(R6·R7·R14): DataChannel 스키마 소유자(C), `turn_id` 도입(B 스키마 변경), 재연결 타이밍 표.
6. **인용 정규화 규칙**(R12): F ADR-0008 vs E SC-72 vs C `qn-1`(동의어 사전).
7. **worker LLM 동시성**(R5): 레인별 1과 `ai` 호출 단위 선점.
8. **불변 트리거 우회 차단**(R13)과 감사 로그 무결성 방식(트리거 vs GRANT 회수 vs 해시 체인) — B·E 합의.
9. **OQ 갱신 요청**: OQ-15(CODING 슬롯 반환), 신규 S-10(STT·LLM GPU 경합), 8.1 STT 해석, 8.1 TTS 하향 경로 해석.
10. **오류 봉투·코드명과 [실행] 동기 여부**(R17·R18): B 단일 소유로 정리.
