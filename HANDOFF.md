# 작업 인수인계 (2026-10-28, AI 서비스·작업 핸들러·음성 런타임·지원자 화면·보관 삭제·탈퇴·오프라인 시험 반영)

온프레미스 AI 채용 플랫폼. 기준 문서는 `통합 PRD.pdf`(저장소에 없음, 추출본은 `docs/prd/`). 이 문서만 읽고 이어서 진행할 수 있도록 현재 상태·검증 결과·다음 할 일을 적었다.

## 1. 먼저 읽을 문서 (순서대로)

1. `docs/design/consolidation-round1.md` — 설계 통합 결정(Critical 8건 판정, 임시 결정 T1~T27). **모든 설계안·리뷰보다 우선한다.**
2. `docs/requirements-traceability.md` — PRD 요구사항·AC-01~22 추적표(상태 칸은 Phase 0 기준이라 갱신 필요, 6절 참고)
3. `docs/trd/01~08` — TRD. 03(데이터 모델)과 04(API 계약)가 구현 기준
4. `docs/adr/` — ADR 0001~0012
5. `docs/open-questions.md`(OQ-01~39), `docs/risks.md`, `docs/data-retention.md`(보관 기간 잠정안)
6. `prototypes/README.md` — 이 장비(GTX 1660 SUPER 6GB) 실측 결과

설계 과정 기록: `docs/design/proposals/`(에이전트 A~E 독립 설계안), `docs/design/reviews/`(상호 리뷰 1라운드).

## 2. 확정된 구성 (요약)

- 프로세스 7개: `app`(FastAPI REST + WebRTC 시그널링·미디어 + 면접 진행), `worker`(DB 작업 큐·주기 작업), `ai`(LLM 게이트웨이·STT·TTS·임베딩, DB 접근 없음), `runner`(코드 샌드박스, DB 접근 없음), `postgres`, `ollama`, `caddy`(운영)
- 스택: Python 3.12, FastAPI, SQLAlchemy 2 async + **asyncpg**(psycopg async는 Windows Proactor 루프 미지원), Alembic, PostgreSQL 16 / React 19 + TypeScript 6 + Vite 8 / aiortc 1.15
- 모델(실측 기반 임시 선정, X-11): LLM `qwen3.5:4b`(Ollama, num_ctx 8192 고정, think=false), STT faster-whisper `large-v3-turbo` GPU int8_float16(대체 `small` CPU), TTS Supertonic-3(CPU), 임베딩 `qwen3-embedding:0.6b`(CPU). `exaone3.5`는 비상업 라이선스라 제외

## 3. 구현·검증 상태

### 3.1 검증 완료 (backend, 실제 PostgreSQL에서 105개 테스트 통과 + runner 11개 + frontend 단위 32개 + 브라우저 E2E 2개(일반·외부망 차단) — 2026-10-28 Linux 클라우드 환경에서 확인, Windows 장비에서는 아직 재실행 안 함)

| 영역 | 파일 | 테스트 |
|---|---|---|
| 스키마·트리거(41개 테이블, 상태 전이 표, 불변·append-only) | `backend/migrations/versions/` | 전 테스트에서 사용 |
| 인증(가입·역할 로그인·세션 2시간·CSRF+Origin·Argon2id·로그인 제한) | `backend/app/auth/` | `tests/test_auth.py` (AC-01, USR-04 등) |
| 공고·평가 설정(가중치 합 100, 잠금)·동의·이력서 업로드(PDF 하위 프로세스 파싱)·지원 | `app/postings`, `consent`, `resumes`, `applications` | `tests/test_postings_apply.py` (AC-02~06, AC-21) |
| 담당자 지원자 관리·리포트·결정 이력·재평가·메모·통계 | `app/recruiter/router.py` | `tests/test_recruiter.py` (AC-03, AC-19, AC-20, ADM-06/07) |
| 점수 계산(6.4)·인용 검증(ADR-0008) | `app/evaluation/` | `tests/test_scoring_quotes.py` (AC-17, AC-16 규칙) |
| 영속 작업 큐(임대·펜싱·인프라 장애 시 시도 미소모·재시작 복구) | `app/jobs/` | `tests/test_job_queue.py` (AC-18 기반) |
| 작업 핸들러: `compose_questions`(ADR-0005 슬롯·커스텀 7번·이력서 근거·BANK_FALLBACK·회사 범위 은행·시드 최후 대체), `tts_pregen`, `extract_resume`(T1→폼 키 변환), `pre_evaluate`, `evaluate_application`(선평가 재사용·T5~T8·점수·리포트·지원자 피드백, ON_CLAIM/ON_REQUEUE/ON_EXHAUSTED로 run·eval_status 관리), `grade_coding`(기대 출력 비교는 worker, C8) | `app/jobs/handlers.py`, `app/questions/compose.py`, `app/evaluation/pipeline.py`, `app/coding/` | `tests/test_handlers.py` (12개) |
| 코딩테스트 API(TRD 04 §5.3: 문제·초안·[실행]·제출), 자동 제출 `coding_autosubmit`(AC-15), 생존 판정 `attempt_sweeper`(120초 무신호→INTERRUPTED, 준비 30분→READY), 평가가 채점을 기다림(`depends_on`) | `app/coding/router.py`, `app/jobs/tasks.py` | `tests/test_coding.py` (4개) |
| runner HTTP 계약·강화 옵션·inspect 검증(Docker 없이) | `runner/` | `runner/tests/test_runner.py` (11개, `cd runner && python -m pytest`) |
| 면접 API 등록(interview·voice 라우터, `GET /api/system/ai-health`)과 인수 기준 AC-08~14 HTTP 시험 | `app/main.py`, `app/interview/system.py` | `tests/test_interview.py` (5개) |
| 음성 런타임(TRD 06, ADR-0009): 지원 건당 aiortc 세션(`iceServers=[]`), TTS 하향 트랙, 서버 게이트, 에너지 VAD, 구간 STT→`caption_segment`, [답변 완료]·시간 만료 확정, 꼬리질문, 면접 종료, 끊김→INTERRUPTED, 슬롯 갱신, 점검 발화(INT-17), 데이터채널 프로토콜 v1(`dc_protocol.py`) | `app/voice/` | `tests/test_voice.py` (8개, 같은 프로세스의 aiortc 클라이언트로 실제 WebRTC 왕복) |
| 지원자 화면: 공고 목록·상세, 지원(동의→이력서·PDF 추출→확인→완료, `ApplyFlow` 메모리 상태), 지원 현황, 면접 안내, 장비 점검(카메라 미리보기·마이크 레벨·WebRTC 발화 점검), 음성 면접(`media/rtc.ts` VoiceLink + 순수 리듀서 `interview/machine.ts`, 재연결 1·2·4초 후 텍스트 모드), 코딩테스트(CodeMirror, 자동 저장 revision, [실행], 최종 제출, 서버 시계 카운트다운), 응시 완료, 결과 | `frontend/src/pages/candidate/`, `src/media/`, `src/api/candidate/interview.ts` | `npm test`(27개), `e2e/candidate-flow.spec.ts`(지원→점검→음성 면접 10문항→코딩 실행·제출→지원 현황, 실제 WebRTC, 약 40초) |
| 보관 기간 자동 삭제(SEC-07): 지원 건 단위 2단계(음성 → 내용 일괄), 기산점 = 첫 결정일 또는 공고 마감일, `app.purge`로 불변 테이블 삭제, 파일은 `file_deletions` outbox → `file_gc`, 끝난 응시의 TTS 캐시, 미사용 업로드 7일, 로그인 제한 기록 24시간, 감사 `DATA_PURGE`, 운영자 CLI `python -m app.cli purge [--dry-run]` | `app/retention/service.py`, `app/jobs/tasks.py`, `app/cli.py` | `tests/test_retention.py` |
| 탈퇴(USR-07): `DELETE /api/candidate/me {password}` → 비밀번호 재확인(403 `REAUTH_FAILED`), 응시 중이면 409 `ACTIVE_ATTEMPT`, 남은 응시 EXPIRED, 모든 지원 건 내용 삭제 + `withdrawn_at`, 진행 중 작업 CANCELLED(펜싱), 등록 이력서·파일 삭제, 계정 익명화, 전 세션 무효화, 감사 `ACCOUNT_WITHDRAW`. 담당자 화면에는 "탈퇴한 지원자" | `app/retention/router.py`, `frontend/src/pages/candidate/Account.tsx` | `tests/test_retention.py`, `Account.test.tsx` |
| AC-22 오프라인: 정적 검사(엔드포인트 허용목록·클라우드 AI SDK 금지·소스 URL·`iceServers=[]`·HF 오프라인 변수) + 외부망 차단 E2E(네트워크 네임스페이스, 백엔드 감사 훅 + Chromium net-log 판정) | `tests/test_offline_static.py`, `tools/offline/run.sh`, `frontend/e2e/global-teardown.ts`, `app/aisvc/offline.py`, `docs/offline-rehearsal.md` | 정적 5개, `tools/offline/run.sh` PASS |
| AI 서비스(TRD 05 §4·§5): 게이트웨이 레인·선점·마감·장애 감지, `/v1/llm·stt·tts·embed·health`, 토큰 인증, 기동 순서, hl-1, 48kHz 리샘플 / 클라이언트·가짜 클라이언트 | `app/aisvc/`, `app/ai/client.py`, `app/ai/fake.py` | `tests/test_ai_service.py` (15개) |

### 3.2 작성했지만 아직 실행·테스트하지 않은 코드

- 프런트 화면 중 E2E가 지나가지 않는 경로: 텍스트 모드(INT-18), 재연결·재개 게이트, 시간 만료 자동 제출 화면, 정원 초과 안내. 내 이력서(`MyResume`)·담당자 화면 전체는 아직 골격(TodoPanel)이다.
- `scripts/s10_gpu_contention.py`(S-10 측정): `--fake` 자체 점검만 했다. GPU 장비에서 실제 측정 필요(아래 4절 7번).

### 3.3 중단된 작업 (사용량 한도로 끊김, 부분 구현)

| 담당(에이전트) | 범위 | 상태 |
|---|---|---|
| C | `backend/app/aisvc/`, `app/ai/client.py`·`fake.py` | **완료(2026-10-27)**. 실제 모델(Ollama·faster-whisper·Supertonic) 연동은 GPU 장비에서 미확인 — `python -m app.aisvc` 기동 후 `/readyz`·`/v1/health` 확인 필요(S-10~S-12). 게이트웨이는 선점된 호출을 내부에서 같은 레인 맨 앞으로 다시 넣으므로 호출자에게 409를 돌려주지 않는다(클라이언트는 409도 처리). BATCH 기아 방지(5분마다 순서 당김)는 같은 레인 FIFO라 구현하지 않음 |
| E | `runner/`(샌드박스 실행 서비스, SBX 자체 점검) | HTTP 계약·컨테이너 옵션·inspect 검증은 시험 통과. **자체 점검 SBX-01~14 전부 통과(2026-10-28, 개발 장비 Windows + Docker Desktop, runtime `runc`) — ADR-0007에 따라 "개발 검증"으로만 본다.** 운영 Linux 호스트에서는 `runsc`(gVisor)로 다시 통과해야 한다(prod는 승인 런타임만 VERIFIED). 계약은 ADR-0007, TRD 07 §13 |
| D | `frontend/` 지원자·담당자 화면 | 지원자 응시 흐름 완료(2026-10-28, build·lint·test 27개·E2E 통과). 남은 것: 내 이력서·계정 화면, 담당자 화면 전체 |

### 3.4 전체 컨테이너 실행 (2026-10-29)

> 이 절의 서비스 구성은 3.6(ADR-0014)으로 대체됐다. 기록으로 남긴다.

`deploy/docker-compose.yml` 하나로 postgres·migrate·ollama(+ollama-pull)·ai(GPU)·runner·app·worker·web(Caddy)을 띄운다. 사용법은 `deploy/README.md`. Redis는 설계에 없다(큐는 PostgreSQL).

- 이미지: `backend/Dockerfile`(target `app`=API·worker·migrate, `ai`=CUDA 12/cuDNN 9 pip 휠 포함 4.7 GB), `runner/Dockerfile`(docker CLI, 첫 기동 때 샌드박스 이미지 빌드), `frontend/Dockerfile`(빌드 + Caddy, `/api`→app).
- 컨테이너 안 WebRTC: `app/voice/netcfg.py`(UDP 포트 범위 고정 + 응답 SDP에 announce 주소 추가). `tests/test_voice.py` 2개 추가.
- 클라우드 세션에서 확인한 것(GPU 없음): 네 이미지 빌드, ai 이미지 안에서 ctranslate2·cuBLAS·cuDNN·supertonic 로드, `docker-compose.verify.yml`(가짜 AI)로 전체 기동 → runner 자체 점검 VERIFIED(runc), 브라우저 E2E 통과(지원→음성 면접→코딩→평가→결정→결과), `scripts/check_container_rtc.py`로 127.0.0.1 후보만으로 WebRTC 연결 PASS.
- **확인 안 한 것**: GPU 할당(Windows Docker Desktop), 실제 모델 다운로드·적재(이 환경은 컨테이너 외부 DNS 차단), Windows에서 UDP 포트 publish를 거친 브라우저 음성. 사용자 PC에서 `deploy/README.md` 3절로 확인 필요.
- 빌드가 TLS 검사 프록시 뒤에 있으면 `BUILD_CA_FILE`(BuildKit secret `build_ca`)로 CA를 넣는다. 설정하지 않으면 아무 영향 없다.
- 시연 데이터: `scripts/seed_demo.py`(회사·담당자 `rec@demo.example`·코딩 포함 공고, 멱등).

### 3.5 Redis 작업 분배·실시간 알림 (2026-10-29, ADR-0013)

사용자 결정으로 Redis를 넣었다.
- 대기줄: 자원별 sorted set, worker가 `BZPOPMIN`으로 즉시 집기.
- 실시간: 상태 변경 → Redis pub/sub → SSE `GET /api/events/stream` → 화면이 다시 조회.
- **`jobs` 테이블은 영속 기록·outbox로 유지**했다. 제출과 작업 등록의 원자성(7.2), 누락 0, 펜싱·인프라 게이트를 지키기 위해서다.
- Redis가 꺼지면 SQL 집기로 자동 전환한다.
- 개발: `deploy/docker-compose.dev.yml`에 redis(127.0.0.1:6379) 추가, `AIHIRE_REDIS_URL`(빈 값 = 끔).
- 테스트는 Redis DB 15를 쓰고, Redis가 없으면 해당 테스트만 건너뛴다.
- 시험:
  - `tests/test_redis_dispatch.py`(4개), `tests/test_events.py`(3개), `liveUpdates.test.ts`
  - E2E의 담당자 결정이 새로고침 없이 지원 현황에 반영(로컬·컨테이너·외부망 차단 실행 모두 통과)

### 3.6 구성도 전면 컨테이너화 (2026-10-29, ADR-0014)

사용자 결정("구성도 그대로 전면 교체"). `deploy/docker-compose.yml` 서비스:
frontend(Next.js)·api·mailer(Mailpit)·migrate·worker(Celery)·scheduler(beat)·llm-rt(llama.cpp GPU)·llm-eval(llama.cpp CPU)·stt·tts·embed(BGE-M3)·postgres(pgvector)·redis·minio. 사용법은 `deploy/README.md`.

- 모델: `backend/scripts/prepare_models.py --out ..\models` 가 Ollama의 GGUF·Whisper·Supertonic을 `models/`로 복사한다. 컨테이너는 읽기 전용으로만 읽는다.
- 코드 실행: worker가 docker.sock으로 샌드박스 컨테이너(python:3.11-slim, network none)를 실행한다. runner 서비스는 없다.
  - Celery 자식 프로세스마다 자체 점검을 따로 갖는다(`sandbox_local.local_ready`).
  - 이를 고치기 전에는 채점 작업이 QUEUED로 멈췄다. 이번에 수정했다.
- 메일: 지원 접수 확인·결과 공개 알림. 팀 PC는 Mailpit 받은편지함(127.0.0.1:8025), 기준 PC는 `.env`의 `MAIL_RELAY_*`로 Gmail 중계.
- MinIO: 프로필 `minio`(`COMPOSE_PROFILES=minio`). Docker Hub 공식 이미지가 없어 `MINIO_IMAGE`를 정해야 한다. 정하기 전에는 `STORAGE_BACKEND=local`.
- 의존성: `redis==6.4.0` (celery 5.6의 kombu가 redis<6.5를 요구).
- 클라우드 세션 확인(점검용 구성 `docker-compose.verify.yml`, 가짜 AI·로컬 파일):
  - 전체 기동
  - 브라우저 E2E 통과
  - 샌드박스 VERIFIED
  - WebRTC 127.0.0.1 PASS
  - Mailpit 메일 3통(접수 2, 결과 1)
  - Mailpit 중계 환경변수 적용 로그 확인
  - backend 테스트 129개 통과
- **사용자 PC에서 확인할 것**:
  - llama.cpp가 qwen3.5 GGUF를 적재하는지
  - 6 GB VRAM에 llm-rt와 STT가 함께 올라가는지(안 되면 `LLM_RT_CTX=8192`)
  - stt·tts 모델 적재
  - Gmail 중계
  - MinIO 이미지

### 3.7 사용자 PC(GTX 1660 SUPER 6GB) 실제 모델 기동 확인 (2026-10-29, 진행 중·커밋 안 함)

`deploy/README.md` 순서로 전체 스택을 띄웠다(`230f320` 기준).

- 준비: `ollama pull bge-m3` → `prepare_models.py` 결과 `llm=OK embed=OK stt=OK tts=OK`. `deploy/.env`는 `.env.example` 복사본에 `STORAGE_BACKEND=local`, `COMPOSE_PROFILES=`(비움)만 바꿨다.
- **MinIO**: `minio/minio`(Docker Hub)와 `quay.io/minio/minio` 둘 다 받을 수 없다(pull access denied / 401). 이미지 선택은 사용자 결정 대기, 그동안 local 저장소.
- 확인 결과(모두 실제 모델):
  - 13개 서비스 기동, llm-rt·llm-eval·stt·tts·embed healthy. VRAM 약 5.1/6 GB(llm-rt ctx 16384 + STT)로 함께 올라감
  - `scripts/ai_smoke.py`의 검사를 api 컨테이너 안에서 `DirectAIClient`로 실행: 전부 PASS. T4 1~2초, T5 47~78초(CPU), TTS→STT CER 0.000(large-v3-turbo/cuda), embed dim 1024
  - worker 샌드박스 VERIFIED(runc), `check_container_rtc.py` PASS(Windows UDP publish 경유)
- **고친 것 1 (`deploy/docker-compose.yml` llm-rt)**: llama.cpp CUDA 이미지에 sm_75용 커널이 없어 컨테이너를 만들 때마다 첫 prompt에서 커널 JIT 컴파일 1~2분 → T4가 전부 TIMEOUT(꼬리질문 없음). `CUDA_CACHE_PATH=/cuda-cache` + 볼륨 `cudacache`로 보관하고, healthcheck가 warm-up prompt 1회를 마친 뒤에만 healthy가 되게 했다. 확인: 캐시 빈 상태 첫 기동 141초 뒤 healthy, 이후 첫 호출 1.4초. 재생성 시 기동 7초.
- **고친 것 2 (E2E 실제 AI 모드)**: `frontend/playwright.config.ts`에 `E2E_VOICE_WAV`(가짜 마이크로 실제 음성 재생), `e2e/candidate-flow.spec.ts`에 `E2E_REAL_AI=1`(발화 대기 8초, 채점 대기 20분, 답변은 정규식 확인), `E2E_POSTING`(공고 제목). 음성 파일 `frontend/e2e/.fixtures/voice-real.wav`는 tts 컨테이너로 만든 12.8초 한국어 답변.
- **진행 중이던 것**: 실제 모델 E2E 실행. 결과 미확인. 다시 돌리는 명령(frontend에서, PowerShell):
  ```
  $env:E2E_BASE_URL='http://localhost:8080'; $env:E2E_RECRUITER='rec@demo.example'; $env:E2E_POSTING='백엔드 개발자'
  $env:E2E_REAL_AI='1'; $env:E2E_VOICE_WAV='e2e/.fixtures/voice-real.wav'
  npx playwright test e2e/candidate-flow.spec.ts --project=chromium --reporter=line
  ```
- 남은 것: E2E 결과 확인·실패 시 수정, Mailpit(127.0.0.1:8025) 메일 수신 확인, llm-rt healthy 전에도 api의 ai-health가 up으로 보일 수 있음(첫 기동 한정, 미수정), `tools/offline/run.sh`가 CRLF(Linux 전용, `.gitattributes`에 `*.sh text eol=lf` 권장), 변경 커밋(사용자 확인 후).

## 4. 다음 할 일 (권장 순서)

1. ~~ai 서비스 완성~~ (완료). 인터페이스:
   ```
   AIClient.llm(task, input, *, lane, deadline_ms=None) -> LLMResult(status, output, flags, usage, manifest_hash)
   AIClient.stt(pcm16k, *, purpose) -> STTResult(text, segments, model)
   AIClient.tts(text) -> TTSResult(pcm48k, duration_ms, voice_model)
   AIClient.embed(texts) -> list[list[float]];  AIClient.health() -> dict
   get_ai_client() / set_ai_client(c);  AIUnavailable 예외(worker는 시도 미소모로 연기)
   ```
   테스트에서는 `set_ai_client(FakeAIClient())`. 가짜도 실제 작업 검증기(`run_task`)를 거친다. 설정 `AIHIRE_AI_TOKEN`(운영 필수), `AIHIRE_GPU_POLICY`, `AIHIRE_TTS_ENGINE=none`(텍스트 전용).
2. ~~작업 핸들러~~ (완료). 알아둘 점:
   - 이력서는 폼·스냅샷 키(`career`, `certificates`)와 AI 계약 키(`experience`, `certifications`)가 다르다. 변환은 `app/resumes/schemas.py`의 `from_ai_resume`·`to_ai_resume`에서만 한다.
   - 담당자 리포트의 `strengths`·`improvements`는 프런트 계약대로 문자열 배열로 저장한다(T6의 `question_refs`는 저장하지 않음).
   - 코딩 채점 전에 평가가 집히면 `InfraUnavailable`로 미룬다(시도 미소모). 출력 비교 규칙: 줄바꿈 통일, 줄 끝 공백·끝 빈 줄 무시(`app/coding/grading.py`).
   - 질문 구성과 평가는 LLM 호출 동안 DB 트랜잭션을 열어 둔다(MVP 단순화). 동시 응시가 늘면 읽기·쓰기 트랜잭션을 나눌 것.
   - 테스트 정리(`tests/conftest.py`)가 `companies`를 TRUNCATE CASCADE 하면서 시드 질문까지 지우던 버그를 고쳤다.
3. ~~음성 런타임~~ (완료). 시그널링 라우터는 `app/voice/router.py`(IV-3 `…/rtc/offer`, IV-18 `…/precheck/utterance`)인데 **아직 `app/main.py`에 등록하지 않았다**(4번에서 interview 라우터와 함께 등록). 알아둘 점·남은 것:
   - 함께 고친 기존 버그: `turns.decide_followup`이 검증된 anchor(`text`) 대신 없는 키(`quote`)를 읽어 **모든 꼬리질문을 새 주제로 버리던 문제**, `turns.finish_interview`가 eval_status 없이 SUBMITTED를 먼저 flush해 **DB CHECK 위반으로 면접 종료가 실패하던 문제**.
   - 답변 음성은 TRD의 Opus(OGG) 대신 FLAC(무손실, soundfile 지원)으로 `data_dir/answers/{turn_id}.flac`에 저장한다.
   - 남은 것: 꼬리질문 TTS가 5초 예산을 넘을 때 "텍스트 먼저" 보내기(OQ-22, 지금은 TTS 최대 10초 대기 후 실패 시 텍스트만), 점검 단계 하향 안내 음성(INT-02, P1), `dc_protocol.json_schemas()` → 프런트 TS 타입 생성, 30분 장시간·다른 PC·운영 Linux host 네트워크 검증(R-10).
4. ~~interview 라우터 등록 + 테스트~~ (완료). AC-08~14 서버 시험 통과(`docs/requirements-traceability.md` 갱신). 시간 만료는 음성 런타임 시계를 앞당기는 방식으로 시험한다. UI 쪽 확인은 6번(프런트·Playwright)에서.
5. ~~코딩테스트 API + runner 연동 + 자동 제출~~ (완료). 알아둘 점: runner의 실제 경로는 `/v1/execute`(TRD 04 표의 `/v1/run`과 다름, 구현 기준). [실행] 결과는 정상/비정상과 출력만 주고 정답 여부는 주지 않는다(AC-15). [실행] 결과 상세는 `coding_runs.stdout_head`에 JSON으로 둔다. `attempt_sweeper`는 음성 면접도 120초 무신호면 INTERRUPTED로 바꾼다(app 프로세스가 죽은 경우 대비).
6. ~~프런트 면접·코딩 화면, Playwright E2E~~ (완료). 알아둘 점:
   - E2E 백엔드는 `backend/scripts/e2e_server.py`: `aihire_e2e` DB를 새로 만들고 회사·담당자·코딩 공고를 시드한 뒤, 가짜 AI(STT는 고정 문장, TTS 0.4초 무음)·가짜 runner(로컬 Python 실행)·같은 프로세스 worker로 8000번에 뜬다. Playwright가 이 서버와 Vite를 함께 띄운다(`webServer`).
   - 가짜 마이크 입력은 `e2e/global-setup.ts`가 만드는 3초 WAV(`--use-file-for-fake-audio-capture`)라 서버 에너지 VAD가 발화로 인식한다.
   - E2E로 찾아 고친 서버 버그: 면접 종료 이벤트를 받은 브라우저가 바로 연결을 닫으면 서버가 이를 끊김으로 보고 **막 시작한 코딩테스트를 INTERRUPTED로 바꾸던 문제**(`app/voice/session.py`, 회귀 시험 `test_hanging_up_on_completion_does_not_interrupt_coding`).
   - 면접 화면은 들어올 때 항상 `POST …/interview/resume`으로 서버 상태를 맞춘 뒤 연결한다(INTERRUPTED 재개, CODING이면 코딩 화면, TEXT 모드면 텍스트 화면). [면접 시작] 직후가 아니면 클릭(자동 재생 정책)을 먼저 받는다.
   - 남은 것: 텍스트 모드·재연결·시간 만료·정원 초과 E2E, msedge 프로젝트 실행(Windows), 데이터채널 타입 자동 생성.
7. ~~보관 기간 자동 삭제(SEC-07), 탈퇴(USR-07), AC-22 오프라인 시험~~ (완료), **S-10 실측은 GPU 장비에서 남음**. 알아둘 점:
   - 보관 기간 설정값은 법무 확인 전이라 구현값(음성 30일·내용 365일)을 바꾸지 않았다(`docs/data-retention.md`의 180일·3년안은 OQ-39 결론 대기). 3단계(기록 3년) 삭제도 아직 없다.
   - 탈퇴는 OQ-12 잠정안: 응시 중이면 거부, 지원 건 행·동의·결정·감사 기록은 개인정보 없이 남긴다. 법정 보존 대상 분리 보관(data-retention 5절 2)은 법무 결론 뒤에.
   - AC-22: 컨테이너 compose 대신 Linux 네트워크 네임스페이스(`unshare -n` + veth 사설 LAN, 기본 경로 없음)로 전체 E2E를 돌린다. Chromium 자체 서비스(구글 로그인·업데이트)는 플랫폼 요청이 아니라서 판정에서 빼고 보고서에만 남긴다. AI는 가짜이므로 실제 모델 오프라인 기동과 실제 브라우저는 `docs/offline-rehearsal.md` 3절 수동 리허설로 확인한다.
   - AI 서비스는 `AIHIRE_MODEL_AUTO_DOWNLOAD`가 false(기본)이면 `HF_HUB_OFFLINE=1` 등으로 뜬다. **처음 모델을 받는 개발 장비에서는 `AIHIRE_MODEL_AUTO_DOWNLOAD=true`로 한 번 띄워야 한다.**
   - S-10 측정: ai 서비스를 `AIHIRE_GPU_POLICY=shared`로 띄워 `python scripts/s10_gpu_contention.py --label shared`, 다시 `stt_preempts_batch`로 띄워 같은 명령. 기준은 경합 시 자막 p95 ≤ 2초. 결과로 기본 정책을 정한다(TRD 02 §5).
8. 추적표 상태 갱신, TRD 09(구현 계획) 작성

E의 구현 차이 목록 G-01~16(TRD 07 §14) 중 G-05(프록시 뒤 IP), G-06(가입 속도 제한), G-09/10(DB 역할 분리), G-11(라우트 정책 선언·IDOR 자동 생성), G-12(TrustedHost)가 남아 있다.

## 5. 개발 환경 재현

```bash
# 1) PostgreSQL (Docker Desktop 필요)
docker compose -f deploy/docker-compose.dev.yml up -d --wait
# 2) Python 3.12 가상환경 (저장소 루트에 .venv)
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e "backend[ai,dev]"   # Linux/macOS: .venv/bin/python
# 3) 마이그레이션과 테스트
cd backend && ../.venv/Scripts/python -m alembic upgrade head
../.venv/Scripts/python -m pytest -q -p no:logging
# 4) 프런트
cd ../frontend && npm ci && npm run build && npm test
# 5) 브라우저 E2E (PostgreSQL 필요, 8000·5173 포트 비워 둘 것)
npx playwright install chromium        # 오프라인망은 사전 반입
$env:E2E_PYTHON="..\.venv\Scripts\python"; npx playwright test --project=chromium   # Linux: E2E_PYTHON=../.venv/bin/python
```

- 모델: Ollama에 `qwen3.5:4b`, `qwen3-embedding:0.6b`. faster-whisper `large-v3-turbo`/`small`, Supertonic-3은 처음 실행 시 Hugging Face에서 받는다(운영은 사전 반입). GPU STT는 `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*` 휠을 설치하고 그 `bin` 폴더를 **PATH**에 넣어야 한다.
- 개발용 비밀번호·토큰은 모두 `dev-...-change-me` 같은 예시값이다. 운영 프로필(`AIHIRE_ENV=prod`)은 기본값이면 기동을 거부한다.
- 외부망 차단 E2E(AC-22, Linux 전용): 저장소 루트에서 `tools/offline/run.sh` → `frontend/e2e/.fixtures/offline/ac22-report.json`. Windows에서는 `docs/offline-rehearsal.md` 3절 수동 리허설.
- runner(Docker Desktop 필요): `docker build -t aihire-sandbox:py311 runner/sandbox` → `cd runner && ..\.venv\Scripts\python -m runner --selftest`(SBX-01~14) → 통과하면 `python -m runner`로 띄우고 backend에 `AIHIRE_CODE_EXECUTION_ENABLED=true`.
- 운영자 CLI: `python -m app.cli create-company --name ...`, `create-recruiter --company-id ... --email ... --name ...`

## 6. 팀·법률 확인이 필요한 항목

`docs/open-questions.md` 중 특히: OQ-04/39(동의 문구·보관 기간, 채용절차법 180일과 PRD 365일 충돌), OQ-25~28(PRD 7.1 표 밖 전이·결정 복귀), OQ-29(Supertonic 모델 라이선스), OQ-30·31(STT GPU 배치, 5초 예산), OQ-32(샌드박스 미검증 시 시작 차단). 화면 이탈 방지 강화 의견은 PRD X-06(경고·기록만) 변경이 필요하므로 팀 결정 사항이다.
