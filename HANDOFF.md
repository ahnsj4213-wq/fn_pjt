# 작업 인수인계 (2026-10-27)

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

### 3.1 검증 완료 (backend, 실제 PostgreSQL에서 48개 테스트 통과)

| 영역 | 파일 | 테스트 |
|---|---|---|
| 스키마·트리거(41개 테이블, 상태 전이 표, 불변·append-only) | `backend/migrations/versions/` | 전 테스트에서 사용 |
| 인증(가입·역할 로그인·세션 2시간·CSRF+Origin·Argon2id·로그인 제한) | `backend/app/auth/` | `tests/test_auth.py` (AC-01, USR-04 등) |
| 공고·평가 설정(가중치 합 100, 잠금)·동의·이력서 업로드(PDF 하위 프로세스 파싱)·지원 | `app/postings`, `consent`, `resumes`, `applications` | `tests/test_postings_apply.py` (AC-02~06, AC-21) |
| 담당자 지원자 관리·리포트·결정 이력·재평가·메모·통계 | `app/recruiter/router.py` | `tests/test_recruiter.py` (AC-03, AC-19, AC-20, ADM-06/07) |
| 점수 계산(6.4)·인용 검증(ADR-0008) | `app/evaluation/` | `tests/test_scoring_quotes.py` (AC-17, AC-16 규칙) |
| 영속 작업 큐(임대·펜싱·인프라 장애 시 시도 미소모·재시작 복구) | `app/jobs/` | `tests/test_job_queue.py` (AC-18 기반) |

### 3.2 작성했지만 아직 실행·테스트하지 않은 코드

- `backend/app/interview/`(router·turns·slots·infra): 면접 준비·시작(슬롯 상한, 설정 잠금)·상태·재개·텍스트 모드·턴 엔진·꼬리질문 판단. **`app/main.py`에 라우터 미등록**, `app.voice.registry`(미작성)를 import하므로 등록 전에 voice 모듈이 필요하다. `decide_followup`에 anchor 원문 대조 추가 필요.

### 3.3 중단된 작업 (사용량 한도로 끊김, 부분 구현)

| 담당(에이전트) | 범위 | 상태 |
|---|---|---|
| C | `backend/app/aisvc/`(ai 서비스), `backend/app/ai/`(작업 T1~T8, 클라이언트, 가짜 클라이언트) | `app/ai/` 규칙·루브릭·작업 정의 일부 작성. **`aisvc/` 비어 있음, `app/ai/client.py`·`fake.py` 없음, 테스트 없음.** 계약은 TRD 05 §4~6 |
| E | `runner/`(샌드박스 실행 서비스, SBX 자체 점검) | 코드 초안 작성, **테스트·실행 안 됨**. 계약은 ADR-0007, TRD 07 §13 |
| D | `frontend/` 지원자·담당자 화면 | 골격·인증 화면·공통 계층 완료(build·lint·test 23개 통과, 중단 직전 기준). 화면 구현 진행 중 |

## 4. 다음 할 일 (권장 순서)

1. **ai 서비스 완성** (TRD 05): `app/ai/client.py` 인터페이스(아래), `app/ai/fake.py`, `app/aisvc/`(레인·선점, STT, TTS, embed, health), 단위 테스트
   ```
   AIClient.llm(task, input, *, lane, deadline_ms=None) -> LLMResult(status, output, flags, usage, manifest_hash)
   AIClient.stt(pcm16k, *, purpose) -> STTResult(text, segments, model)
   AIClient.tts(text) -> TTSResult(pcm48k, duration_ms, voice_model)
   AIClient.embed(texts) -> list[list[float]];  AIClient.health() -> dict
   get_ai_client() / set_ai_client(c);  AIUnavailable 예외
   ```
2. **작업 핸들러**: `compose_questions`(ADR-0005), `tts_pregen`, `extract_resume`(T1), `pre_evaluate`·`evaluate_application`(T5~T8, `app/evaluation/scoring.py` 사용, 펜싱 완료는 `worker.fenced_complete`), `grade_coding`. `ON_CLAIM`/`ON_EXHAUSTED` 훅으로 eval_status PENDING→RUNNING, RUNNING→ON_HOLD
3. **음성 런타임** `app/voice/`: aiortc(iceServers=[]), TTS 하향 트랙, 서버 게이트, VAD→STT→데이터채널 자막, `turns.py` 호출. 동작 확인된 원형은 `prototypes/rtc_browser/server.py`. 프로토콜은 TRD 06
4. interview 라우터 등록 + 테스트(AC-08, 09, 10, 11, 12, 13, 14)
5. 코딩테스트 API(TRD 04 §5.3) + runner 연동 + 자동 제출 주기 작업(AC-15)
6. 프런트 면접·코딩 화면, Playwright E2E(가짜 미디어 장치)
7. 보관 기간 자동 삭제(SEC-07), 탈퇴(USR-07), AC-22 오프라인 시험, S-10 실측
8. 추적표 상태 갱신, TRD 09(구현 계획) 작성

E의 구현 차이 목록 G-01~16(TRD 07 §14) 중 G-05(프록시 뒤 IP), G-06(가입 속도 제한), G-09/10(DB 역할 분리), G-11(라우트 정책 선언·IDOR 자동 생성), G-12(TrustedHost)가 남아 있다.

## 5. 개발 환경 재현

```bash
# 1) PostgreSQL (Docker Desktop 필요)
docker compose -f deploy/docker-compose.dev.yml up -d --wait
# 2) Python 3.12 가상환경 (저장소 루트에 .venv)
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e "backend[ai,dev]"
# 3) 마이그레이션과 테스트
cd backend && ../.venv/Scripts/python -m alembic upgrade head
../.venv/Scripts/python -m pytest -q -p no:logging
# 4) 프런트
cd ../frontend && npm ci && npm run build && npm test
```

- 모델: Ollama에 `qwen3.5:4b`, `qwen3-embedding:0.6b`. faster-whisper `large-v3-turbo`/`small`, Supertonic-3은 처음 실행 시 Hugging Face에서 받는다(운영은 사전 반입). GPU STT는 `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*` 휠을 설치하고 그 `bin` 폴더를 **PATH**에 넣어야 한다.
- 개발용 비밀번호·토큰은 모두 `dev-...-change-me` 같은 예시값이다. 운영 프로필(`AIHIRE_ENV=prod`)은 기본값이면 기동을 거부한다.
- 운영자 CLI: `python -m app.cli create-company --name ...`, `create-recruiter --company-id ... --email ... --name ...`

## 6. 팀·법률 확인이 필요한 항목

`docs/open-questions.md` 중 특히: OQ-04/39(동의 문구·보관 기간, 채용절차법 180일과 PRD 365일 충돌), OQ-25~28(PRD 7.1 표 밖 전이·결정 복귀), OQ-29(Supertonic 모델 라이선스), OQ-30·31(STT GPU 배치, 5초 예산), OQ-32(샌드박스 미검증 시 시작 차단). 화면 이탈 방지 강화 의견은 PRD X-06(경고·기록만) 변경이 필요하므로 팀 결정 사항이다.
