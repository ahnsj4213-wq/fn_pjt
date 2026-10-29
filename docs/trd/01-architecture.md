# TRD 01 — 시스템 아키텍처

- 작성: 에이전트 A(아키텍처). 기준: `docs/design/consolidation-round1.md`(이하 "통합 결정", 모든 설계안보다 우선), `prototypes/README.md`·`results-*.txt`(실측, 2026-10-26), 통합 PRD v1.0.
- 관련 ADR: ADR-0001(프로세스 구성), ADR-0002(기술 스택·asyncpg), ADR-0003(LLM 레인·선점), ADR-0004(STT·TTS 배치), ADR-0006(작업 큐·상태), ADR-0007(runner·샌드박스), ADR-0009(실시간 프로토콜), ADR-0010(배포·강화).
- **2026-10-29 변경**: 컨테이너 구성은 ADR-0014(구성도 전면 교체)를 따른다. 이 문서의 `ai`·`ollama`·`runner`·`web`(Caddy) 서비스는
  llm-rt·llm-eval·stt·tts·embed·worker 내장 샌드박스·frontend(Next.js)로 바뀌었다. `ai`·`runner` 단일 프로세스는 네이티브 개발용으로만 남는다.
- 표기: **[실측]** = 이 장비에서 측정한 값(출처 파일 명시), **[추정]** = 측정하지 않은 값, **(예정)** = 아직 코드가 없는 구성 요소. PRD 기능 ID·수치·AC는 바꾸지 않는다.

## 1. 시스템 구성 (7개 프로세스)

```
 브라우저(Chrome/Edge, PC)
   │ HTTPS 443 (REST·시그널링·명령)          │ WebRTC UDP(DTLS-SRTP, 오디오 상·하향 + 데이터채널)
   ▼                                          ▼
 [caddy] ──HTTP 127.0.0.1:8000──▶ [app] ◀─────┘          (운영: caddy·app 모두 host 네트워크)
                                    │  ├─ SQL ─────────▶ [postgres]
                                    │  ├─ HTTP 8200 ──▶ [ai] ──HTTP 11434──▶ [ollama] (GPU: LLM)
                                    │  │                 └ STT(GPU) · TTS(CPU) · 임베딩 프록시(CPU)
                                    │  └─ HTTP 8400 ──▶ [runner] ──Docker API──▶ 샌드박스 컨테이너(network none)
 [worker] ── SQL ─▶ [postgres],  HTTP ▶ [ai],  HTTP ▶ [runner]
```

| 프로세스 | 역할 | 기술 | 포트 | DB 자격 | GPU | 동시성 |
|---|---|---|---|---|---|---|
| `app` | REST API 전부, WebRTC 시그널링·미디어(aiortc), 면접 진행기(턴 상태 기계·마이크 게이트·VAD 분할·자막 이벤트), 답변 즉시 저장, 슬롯·하트비트 | FastAPI + uvicorn, aiortc | 8000 | 있음 | 없음 | uvicorn 워커 1개(PeerConnection이 프로세스 메모리에 있음) |
| `worker` | 영속 작업 큐(임대·펜싱), 주기 작업(자동 마감·자동 제출·sweeper·보관 삭제), 이력서 추출·질문 구성·사전 TTS·문항 평가·종합 평가·채점 오케스트레이션 | 같은 backend 패키지 | 없음 | 있음 | 없음 | `jobs.resource`별 세마포어. LLM 작업은 레인별 1개(INTERACTIVE 1, BATCH 1) |
| `ai` | LLM 게이트웨이(레인 RT > INTERACTIVE > BATCH, 선점, `think=false`·`num_ctx` 고정), STT, TTS, 임베딩 프록시, AI 상태 탐침 | FastAPI 소형 + faster-whisper·ctranslate2, supertonic(onnxruntime) | 8200 | **없음** | STT(turbo int8_float16) | 단일 인스턴스(우선순위는 전역이어야 함) |
| `runner` | 코드 실행·채점 컨테이너 기동, 샌드박스 자체 점검(SBX), 실행 플래그 | Python 소형 + Docker Engine API (예정) | 8400 | **없음** | 없음 | 전역 동시 실행 2 [E 제안값] |
| `postgres` | 유일한 상태 저장소(파일 본문은 볼륨) | PostgreSQL 16 | 개발 55432, 운영 5432 | — | — | — |
| `ollama` | LLM 서빙(GPU), 임베딩(CPU, `num_gpu=0`) | Ollama 0.40.7 | 11434 | — | LLM | 게이트웨이가 동시 요청 1개로 제한 |
| `caddy` | HTTPS 종단, SPA 정적 파일, `/api/*`→app, 보안 헤더 | Caddy 2 (운영) | 443, 80→443 | — | — | — |
| `redis` | 작업 분배(자원별 sorted set)·실시간 상태 알림(pub/sub → SSE). 내용은 모두 PostgreSQL에서 재구성 가능(ADR-0013, 2026-10-29 추가) | Redis 7 | 6379 | — | — | — |

- 직접 작성하는 프로세스는 app, worker, ai, runner 4개다. 나머지 3개는 기성 런타임이다.
- 개발에서는 caddy를 두지 않고 Vite 개발 서버(5173)가 같은 출처 프록시를 맡는다(6.1절).

## 2. 경계·책임·권한

| 프로세스 | 하는 일 | 하지 않는 일(경계) |
|---|---|---|
| app | 인증·권한·회사 범위 강제(SEC-03), 상태 전이(공유 모듈), 시그널링 인증(쿠키+CSRF, 소유자·상태 확인), 프레임 게이트(AC-09), 서버 시계 마감(INT-06), 지원자/담당자 응답 스키마 분리(SEC-04) | PDF 파싱, LLM 직접 호출(꼬리질문도 ai 경유), Docker 접근, 10ms 넘는 동기 CPU 작업(Argon2는 스레드풀) |
| worker | 작업 집기 전 인프라 상태 확인(OQ-23), 결과 펜싱 반영, 기대 출력 비교(채점 판정, 통합 결정 C8), PDF 파싱(자식 프로세스, 20초·50쪽 상한, T22) | 채용 결정 칼럼 쓰기(P-8), 사용자 요청 응답 |
| ai | 모델 호출, 레인 스케줄링, 출력 JSON 스키마 강제, 모델 digest 보고(EVL-08), 프롬프트·출력 본문 비기록 | DB 접근, 점수 합산(EVL-04는 worker의 서버 코드), 외부 네트워크 호출 |
| runner | 컨테이너 생성 직후 inspect 검증(SBX-01), stdin으로 코드·입력 전달, 출력 64 KiB 절단, 지문 기록 | DB 접근, 기대 출력 보유(C8), 비공개 테스트 저장 |
| postgres | 제약·트리거로 불변식 강제(가중치 합 100, 스냅샷 불변, 허용 전이) | — |

신뢰 경계(E 설계안 TB 번호):

| 경계 | 위치 | 통제 |
|---|---|---|
| TB-1 | 브라우저 ↔ caddy/app | HTTPS, 쿠키 세션 + CSRF 토큰 + Origin 검사. WebRTC는 인증된 시그널링으로만 생성 |
| TB-2 | app·worker ↔ postgres | 앱 롤은 DML만(마이그레이션 롤 분리, ADR-0006·0012) |
| TB-3 | app·worker ↔ ai ↔ ollama | 루프백/내부망 바인딩. AI 출력은 비신뢰 데이터로 검증 |
| TB-4 | app·worker ↔ runner | 루프백/내부망 + `X-Runner-Token` |
| TB-5·6 | runner ↔ Docker ↔ 샌드박스 | 소켓은 runner만. 샌드박스는 network none·read-only·cap-drop ALL 등(ADR-0007) |
| TB-7 | 서버 ↔ 외부 인터넷 | 운영 이그레스 차단, AI 엔드포인트 사설 주소 허용목록(`config.is_private_endpoint`), `iceServers=[]` |

## 3. 프로세스 간 통신

| 호출자 → 대상 | 방식 | 주소(개발 기본값) | 인증 | 타임아웃·실패 시 |
|---|---|---|---|---|
| 브라우저 → app | HTTPS REST, 시그널링 POST(비-trickle SDP) | 개발 `http://localhost:5173`(Vite 프록시 → 8000) | 쿠키 세션 + `X-CSRF-Token` | 오류 형식 `{"error":{code,message,request_id}}`(`app/core/errors.py`) |
| app → 브라우저 | 데이터채널(서버→클라이언트 이벤트), 하향 오디오 트랙(TTS) | — | 시그널링으로 보호된 DTLS | 끊기면 IV-7 상태 스냅샷으로 복구(ADR-0009) |
| 브라우저 → app 명령 | HTTPS(`turn_id` 포함, 불일치 409 `STALE_TURN`) | — | 쿠키 + CSRF + 멱등 키 | 네트워크 유예 뒤 도착분은 무시 |
| app·worker → ai | HTTP JSON | `http://127.0.0.1:8200` (`AIHIRE_AI_SERVICE_URL`) | 내부망 바인딩 | RT는 `deadline_ms`, 초과 시 꼬리질문 생략. 503은 인프라 오류(시도 미소모) |
| ai → ollama | HTTP 스트리밍 | `http://127.0.0.1:11434` (`AIHIRE_OLLAMA_URL`) | 루프백 | 선점 = 스트림 연결 종료 [실측 취소 후 0.49초] |
| app·worker → runner | HTTP JSON `{case_id, stdin}` | `http://127.0.0.1:8400` (`AIHIRE_RUNNER_URL`) | `X-Runner-Token` | 503 `CODE_EXECUTION_UNAVAILABLE`면 [실행] 비활성, 채점은 대기(0점 아님) |
| app·worker → postgres | asyncpg | `127.0.0.1:55432` 개발 / `5432` 운영 | DB 계정 | `statement_timeout` 60초, `idle_in_transaction` 30초(`app/core/db.py`) |
| worker 깨우기 | `LISTEN/NOTIFY jobs` + 2초 폴링 | — | — | 알림 유실은 폴링이 흡수 |

모든 AI 엔드포인트 설정값은 기동 시 사설·루프백 주소인지 검사한다. 위반 시 기동 실패(SEC-06, `Settings._check`).

## 4. 데이터 흐름

### 4.1 지원부터 결과 공개까지

| 단계 | 처리 위치 | 동작 | 관련 ID |
|---|---|---|---|
| 1 동의 | app | 동의 문구 버전·항목별 여부·서버 시각 기록(INSERT 전용) | SEC-01·02, AC-05 |
| 2 이력서 업로드 | app → worker | 크기(10 MiB)·매직바이트만 app이 검사해 UUID 파일명으로 저장, 202 + 추출 작업 등록(같은 트랜잭션). worker 자식 프로세스가 텍스트 추출, 글자 수로 스캔본 판정(RES-04), ai INTERACTIVE 레인으로 구조화, 원문 부분 문자열 검증 | RES-02~05, AC-06 |
| 3 지원 | app | 동의 FK 필수, `UNIQUE(posting_id, candidate_id)`, 이력서 JSON 스냅샷 + PDF 사본 고정 | JOB-02, RES-07, AC-04 |
| 4 준비 진입 | app → worker | READY→PREPARING과 같은 트랜잭션에서 질문 구성 작업 등록 → 본질문 10개 스냅샷(생성 최대 2회 후 은행 대체) → 사전 TTS. 사전 합성은 시작 조건이 아님(T20) | QST-01~03·07, 6.1 |
| 5 환경 점검 | 브라우저 + app | 마이크·카메라는 브라우저가 검사. WebRTC 점검 연결은 PREPARING만 요구(슬롯 불필요, 서버 전체 4개 상한, C4). 짧은 발화를 STT로 확인해 서버가 통과 기록 | INT-01·17, AC-08 |
| 6 면접 시작 | app | 한 트랜잭션: 공고 OPEN, 점검 기록, AI 상태(LLM·STT 필수, TTS는 경고), 코딩 켠 공고면 샌드박스 검증 상태(T13), 슬롯 확보, PREPARING→INTERVIEWING, 설정 잠금, 질문 스냅샷 동결 | INT-13·14, JOB-06, AC-14·21 |
| 7 실시간 루프 | app ↔ ai | 4.2절 | INT-04~11·15, 6.2 |
| 8 답변 확정 | app | 한 트랜잭션: `interview_answers`(turn_id UNIQUE) 저장 + 문항 평가 작업(BATCH) 등록(T15) | INT-09, EVL-05 |
| 9 코딩 | app, runner, worker | INTERVIEWING→CODING 시 슬롯 반환(T10)·서버 마감 기록. 하트비트는 코딩 API가 갱신(C7). [실행]은 공개 예시만 | COD-01~06, AC-15 |
| 10 제출 | app 또는 worker | 한 트랜잭션: 코드 스냅샷, →SUBMITTED, 평가 상태 PENDING, 채점·종합 평가 작업 등록 | 7.2, 8.3 |
| 11 평가 | worker → runner, ai | 채점: 테스트 1건 = 컨테이너 1개, 비교는 worker(C8). 종합: 유효한 선평가 재사용 → 인용 검증(ADR-0008) → 총평·지원자 피드백 별도 생성 → Decimal 점수 계산(T18) → 펜싱 조건부 반영 → DONE. 실패·10분 초과 시 1회 재생성, 재실패 ON_HOLD | EVL-01~08, RPT-02·03, AC-16~18 |
| 12 결정·공개 | app | 평가 DONE 후에만 결정, 이력 append-only. 지원자 결과 API는 결정 후 공개 범위만(6.6) | ADM-04, RPT-03, SEC-04, AC-19·20 |

### 4.2 실시간 면접 루프와 5초 예산

| 순서 | 동작 | 근거 |
|---|---|---|
| ① | 새 `turn_id` 발급(`interview_turns`) → 데이터채널 `question` 이벤트 + 사전 TTS를 하향 트랙으로 송출 | T7, ADR-0009 |
| ② | 송출 종료 + 0.3초(`tts_playout_guard_seconds`) 뒤 서버가 마이크 게이트를 열고 `answer_opened{deadline_at}` 전송. 그 전 프레임은 폐기 | [실측 S-1] 게이트 이전 발화 5.46초 폐기 |
| ③ | 48 kHz Opus → 16 kHz mono → VAD 구간 분할 → ai STT → `caption_partial` | [실측 S-1] 휴지 후 1.46~1.70초 도착 |
| ④ | [답변 완료] HTTPS 명령 또는 `deadline + 네트워크 유예(1.0초, 설정값)` 도달 시 확정. 침묵으로는 끝내지 않음 | X-02, T9 |
| ⑤ | 마지막 구간 STT → 저장(4.1의 8) → 규칙 판정(횟수 1/4, 내용 없음) → ai RT 레인 꼬리질문 판단(마감 5초, T27) → 다음 제시 | 6.2 |

| 구간(답변 종료 → 다음 질문 제시) | 값 |
|---|---|
| 마지막 구간 STT(GPU turbo) | 1.1~1.4초 [실측, LLM 유휴 상태. 생성 중 경합은 S-10 미측정] |
| BATCH 선점(스트림 종료 후 처리 시작) | ≤ 0.49초 [실측] |
| 꼬리질문 판단(웜) | 0.56~0.57초 [실측] |
| 꼬리질문 TTS(비스트리밍, 5.2초 음성) | 1.5초, RTF 0.25~0.32 [실측] → 긴 질문은 텍스트 먼저 표시(OQ-22) |
| 저장·전송 여유 | 0.3초 [추정] |
| 합계 | 3.5~4.6초 [추정, 위 실측값의 합] → 5초 이내 판정은 통합 부하 측정에서(OQ-31) |

## 5. GPU·CPU 자원 배분

| 항목 | 값 | 비고 |
|---|---|---|
| LLM `qwen3.5:4b` Q4_K_M | 2,995 MiB(`num_ctx` 4096) / 3,209 MiB(8192) [실측] | 전 층 GPU. `num_ctx`는 8192 전역 고정(T4). 값이 바뀌면 5.1초 재적재 [실측] |
| 장비 최대 VRAM | 4,172 MiB(LLM + 데스크톱 454 MiB) [실측] | STT turbo를 함께 적재한 상태에서 동작은 확인 [실측]. 사용량 수치는 미기록 → 통합 측정에서 기록 |
| STT | large-v3-turbo int8_float16(GPU) 기본, small int8(CPU) 대체 | 2초 자막 근거: GPU 1.1~1.4초 vs CPU small 2.0~2.6초 [실측] |
| TTS | Supertonic-3 CPU | 본질문 10개 사전 합성 15~25초 [추정, RTF 실측 기반] |
| 임베딩 | `qwen3-embedding:0.6b`, `num_gpu=0` | LLM과 공존, 재적재 없음 [실측] |
| 콜드 로드 | LLM 약 40초 [실측] | ai `/readyz`는 LLM·STT 적재 완료 전 false |
| 동시 응시 상한 | 기본 2(`max_concurrent_interviews`) | INTERVIEWING만 슬롯 점유(T10). 상한 조정은 부하 측정 후(8.3) |
| 샌드박스 CPU | 컨테이너당 `--cpus 1`, 전역 동시 2 | STT가 GPU로 가서 CPU 경합은 줄었다 [추정] |

레인 규칙(ADR-0003): ai가 Ollama로 보내는 요청은 동시에 1개다. 상위 레인 요청이 도착하면 진행 중인 BATCH 스트림을 끊고 재대기열에 넣는다(시도 미소모). worker가 LLM 작업을 레인별로 1개씩 동시에 실행해야 INTERACTIVE 작업이 BATCH 작업 뒤에서 막히지 않는다(head-of-line 방지).

## 6. 배포 구조

### 6.1 개발 (Windows 10 + Docker Desktop)

| 위치 | 구성 |
|---|---|
| Windows 네이티브 | Ollama(11434, 이미 실행 중), Python 3.12 가상환경의 app(8000)·worker·ai(8200)·runner(8400), Vite 개발 서버(5173, `/api` 프록시) |
| Docker Desktop | `deploy/docker-compose.dev.yml`의 postgres(127.0.0.1:55432), runner가 띄우는 샌드박스 컨테이너 |
| 브라우저 | `http://localhost:5173`. localhost는 보안 컨텍스트라 마이크 허용(8.4) |

- app을 네이티브로 두는 이유: Docker Desktop 컨테이너 IP에는 Windows 브라우저가 닿지 않는다. aioice는 포트 범위를 지정할 수 없어 포트 publish로 우회할 수도 없다.
- **2026-10-29 갱신**: 전체 컨테이너 실행(`deploy/docker-compose.yml`, `deploy/README.md`)이 추가됐다. `app/voice/netcfg.py`가 aioice 포트를 범위 안으로 묶고(`AIHIRE_RTC_UDP_PORT_MIN/MAX`, publish 40000~40019/udp) 응답 SDP에 브라우저가 닿는 주소(`AIHIRE_RTC_ANNOUNCE_IPS`, 기본 127.0.0.1)의 host 후보를 추가해 위 제약을 우회한다. 네이티브 실행도 그대로 가능하다.
- ai 기동 스크립트는 cuBLAS·cuDNN 경로를 PATH에 넣는다. ctranslate2는 PATH로만 찾는다 [실측 S-1].
- uvicorn은 `--reload`·`--workers` 없이 실행한다(ADR-0002 남은 위험: 이벤트 루프 종류).

### 6.2 운영 (Linux 단일 호스트, docker compose)

| 항목 | 결정 |
|---|---|
| 네트워크 | caddy·app은 `network_mode: host`(aiortc host 후보 도달성, C1). caddy는 443을, app은 127.0.0.1:8000만 바인딩 |
| 내부 서비스 | worker·ai·ollama·runner·postgres는 내부 브리지망. app이 접근할 포트는 127.0.0.1에만 publish. `internal: true` 망에서는 포트 publish가 되지 않는 것으로 알려져 있어 [확인 필요] 이그레스는 호스트 방화벽(DOCKER-USER 체인 포함)으로 막는다 |
| UDP | app의 OS 임시 포트 범위를 사내 서브넷에서만 허용(호스트 방화벽) |
| GPU | ollama·ai 컨테이너에 NVIDIA Container Toolkit으로 GPU 할당 |
| 샌드박스 | 강화 런타임(runsc 권장, 최소 rootless 전용 데몬)은 **샌드박스 컨테이너에만** 적용. 주 데몬의 userns-remap은 쓰지 않는다(C1, ADR-0010) |
| 기동 순서 | postgres → `migrate`(1회성 `alembic upgrade head`) → ollama → ai(LLM·STT 적재 후 ready) → runner(SBX 자체 점검) → app·worker → caddy |
| 재시작 | 전 서비스 `restart: unless-stopped`. 진행 중 면접은 하트비트 만료로 INTERRUPTED → 재진입(INT-11) |
| 볼륨 | pgdata, files(이력서), audio(답변 음성 30일), tts(사전 합성), models(읽기 전용 반입), backup |
| HTTPS | Caddy `tls internal`(사내 루트 CA를 응시 PC에 설치) 또는 회사 CA 인증서 파일 |
| 운영 표면 | OpenAPI 문서·테스트 훅 비활성(T25). 설정 검증 실패 시 기동 거부(`env=prod`: Secure 쿠키, runner 토큰) |

### 6.3 오프라인 시험 프로필 (AC-22)

- 모든 서비스를 `internal: true` 망에 두고, app도 일반 컨테이너로 둔다. 같은 망 안에서는 host 후보가 닿는다.
- 같은 망에 Playwright Chromium 컨테이너를 두고 가짜 장치(`--use-fake-device-for-media-stream --use-file-for-fake-audio-capture`)로 지원→리포트→결정→결과 전체 흐름을 실행한다.
- 내부 DNS 질의를 기록하고, 외부 이름 질의나 외부 연결 시도가 하나라도 있으면 실패한다(E SC-84).
- 시험 시작 시 외부 egress가 실패하는지부터 확인한다.

## 7. 장애 시 대체 경로 (8.3)

| 장애 | 면접 시작 전 | 면접 중 | 제출 후 |
|---|---|---|---|
| LLM(ai·ollama) | 시작 차단(INT-14) | 꼬리질문 생략. 본질문은 사전 구성이라 계속 | 작업을 집지 않음, 시도 미소모, 복구 후 자동 진행(OQ-23, AC-18) |
| 질문 생성 실패 | 최대 2회 후 은행 대체(QST-07, T17) | — | — |
| STT | 시작 차단 | 구간 재시도 1회 후 비면 '답변 없음'(INT-09). GPU 불가 시 CPU small로 전환(자막 지연) | — |
| TTS | 경고 후 진행(OQ-08) | 텍스트만 제시(INT-04) | — |
| WebRTC 끊김 | 시작 차단(INT-17) | INTERRUPTED → 재연결 시 끊긴 문항부터, 문항당 재출제 2회 후 텍스트 답변(T11·T12, INT-18) | — |
| 샌드박스 미검증·runner 장애 | 코딩 켠 공고는 시작 차단(T13) | [실행] 503 안내, 임시저장 계속 | 채점 대기, 0점 처리 안 함 |
| worker 중지 | — | 영향 없음(답변 저장은 app) | PENDING 유지, 재기동 시 임대 만료 작업 회수(AC-18) |
| postgres | 전체 중단 | app이 "일시 장애" 안내 후 연결 종료 → INTERRUPTED | 복구 후 재개 |
| app 재시작 | — | 연결 끊김 → INTERRUPTED → 재진입 | 영향 없음 |
| 호스트 재부팅 | — | 하트비트 만료로 INTERRUPTED, 슬롯 반환 | 임대 만료 작업 회수 |

## 8. 설정과 관측성

- **PRD 고정값은 코드 상수**(`app/core/constants.py`)로 둔다. 예: 본질문 10, 60·45초, 10초 경고, 꼬리 1/4, 판단 5초, 10 MiB, 2시간, 평가 2회·10분, 기본 가중치, 고정 문구. 테스트로 고정한다.
- **설정값**은 `app/core/config.py`(`AIHIRE_` 접두 환경변수)에 둔다. 예: 동시 응시 상한, 슬롯 임대, 네트워크 유예, 보관 기간, 짧은 답변 기준, 모델 이름, 서비스 주소, 실행 플래그(기본 false).
- **비밀**은 `SecretStr`로 다루고, 운영에서는 compose secrets 파일 마운트로 전달한다(ADR-0010).
- **로그**: 모든 프로세스가 stdout JSON 한 줄을 남긴다(`app/core/logging.py`).
  - 허용 필드만 출력한다: `request_id, user_id, role, company_id, application_id, posting_id, question_id, job_id, job_kind, lane, status, status_code, method, path, latency_ms, event, error_code, attempt, run_no, model, count, seq, reason, duration_ms`.
  - 메시지의 이메일·전화번호는 마스킹하고, 예외는 타입만 남긴다.
  - SQLAlchemy·aiortc·aioice·httpx 로거는 WARNING으로 낮춘다.
  - 답변·자막·이력서·코드·프롬프트·LLM 출력 본문은 어느 프로세스도 남기지 않는다(SEC-08).
- **카나리아 검사**(QG-07): 이름·이메일·전화·이력서·답변·코드에 `CANARY-<용도>-<난수>`를 넣고 전체 흐름을 돌린 뒤 app·worker·ai·runner·caddy·ollama·Docker 로그를 모두 검색한다. 0건이어야 통과다.
- **지연 측정**: `event`·`duration_ms`로 `stt.segment`, `rt.followup`, `tts.first_audio`, `eval.application` 등을 기록하고 집계 도구(예정)가 8.2 목표 대비 실측표를 만든다. Prometheus 등 외부 모니터링은 도입하지 않는다.
- **상태 점검**:
  - app은 `GET /api/healthz`(생존, 구현됨)와 readiness 점검(예정)을 둔다. ai·runner는 `GET /healthz`(생존)와 `GET /readyz`(의존성 포함)를 둔다.
  - `GET /api/system/ai-health`가 ai(LLM·STT·TTS)와 runner(샌드박스 검증 상태)를 모아 시작 게이트에 쓴다.
- **감사 로그**(SEC-08)는 앱 로그가 아니라 DB `audit_logs`(append-only)에 남긴다.

## 9. 디렉터리 구조 (있음 / 예정)

```
final_test/
├─ backend/                        # Python 패키지 하나, 진입점만 다름 (app·worker·ai)
│  ├─ pyproject.toml               # 고정 버전, extras [ai]=모델 런타임, [dev]=테스트   [있음]
│  ├─ app/
│  │  ├─ core/                     # config, constants, db, errors, logging, middleware  [있음]
│  │  ├─ auth/                     # deps, passwords, router, sessions, throttle         [있음]
│  │  ├─ postings/ applications/ consent/ resumes/ jobs/                              [있음, 구현 진행 중]
│  │  ├─ main.py, cli.py, audit.py, models.py   # app 진입점, 운영자 CLI(OQ-19), 감사, ORM 매핑 [있음]
│  │  ├─ questions/ interview/ voice/ coding/ evaluation/ reports/ recruiter/ retention/ (예정)
│  │  ├─ worker/                   # 작업 큐·주기 작업 진입점                              (예정)
│  │  └─ ai/                       # ai 서비스(게이트웨이·STT·TTS) 진입점·클라이언트      (예정)
│  ├─ migrations/                  # env.py, versions/20261027_0001_initial.py            [있음]
│  └─ tests/                                                                          (예정)
├─ frontend/                       # Vite + React + TS SPA (D 설계안)                      (예정)
├─ runner/                         # 별도 패키지, DB 의존성 없음 + 샌드박스 이미지 Dockerfile (예정)
├─ deploy/                         # docker-compose.dev.yml [있음]; 운영·오프라인 시험 compose, Caddyfile, env 예시 (예정)
├─ docs/                           # prd, trd, adr, design, 추적표, OQ, 위험, 환경
└─ prototypes/                     # 스파이크 스크립트·결과(운영 코드 아님)
```

## 10. 남은 검증 항목

| ID | 항목 | 통과 기준 | 시점 |
|---|---|---|---|
| S-10 | LLM 생성 중 GPU STT 지연 | 자막 p95 ≤ 2초. 넘으면 STT 요청 시 BATCH 선점으로 확장 | 통합 부하 측정 |
| S-1(운영) | 운영 Linux 호스트에서 다른 PC 브라우저로 WebRTC·게이트·자막 | 연결·게이트·자막 2초 | 출시 조건(ADR-0010) |
| VRAM | LLM(8192) + STT turbo + 데스크톱 동시 적재 최대치 | `ollama ps` size_vram = size, 여유 ≥ 300 MiB [추정 기준] | 통합 측정 |
| 5초 예산 | 답변 종료→다음 질문 p95 | ≤ 5초(8.2). 넘으면 OQ-31 | 통합 측정 |
| 리포트 | 제출→평가 DONE | 단독 ≤ 3분, 부하 ≤ 10분(8.2) | 통합 측정 |
| SBX(운영) | 운영 런타임에서 SBX-01~14 | 전부 통과 | 출시 조건(ADR-0007·0010) |
| 설정 정합 | `config.py` 기본값과 통합 결정 일치. 현재 `answer_grace_seconds=1.5`인데 T9는 1.0초. `slot_lease_seconds=90`은 T10의 INTERRUPTED 120초 유예보다 짧음 | 값 정정 또는 근거 기록 | 구현 담당(B·C) |
