# ADR-0014: 컨테이너 구성도(전면 컨테이너화)

- 상태: 채택 (2026-10-29, 사용자 결정 "구성도 그대로 전면 교체")
- 대체 범위:
  - ADR-0001: 7개 프로세스 구성 중 `ai`(단일 AI 프로세스)·`ollama`·`runner` 서비스 분리 방식
  - ADR-0006·0013: worker 실행 방식만. 작업 기록·outbox·펜싱·인프라 게이트는 그대로다.
  - ADR-0010: web(Caddy) 앞단을 Next.js 프런트 컨테이너로 바꾼다.
- 관련: ADR-0003(레인), ADR-0004(STT·TTS), ADR-0007(샌드박스), ADR-0013(Redis), TRD 01, `deploy/README.md`
- 구현: `deploy/docker-compose.yml`, `docker-compose.verify.yml`, `backend/Dockerfile`(target app·worker·ai), `frontend/Dockerfile`, `app/celery_app.py`, `app/jobs/celery_tasks.py`, `app/worker_main.py`, `app/ai/direct.py`, `app/aisvc/llamacpp.py`, `app/aisvc/speech_server.py`, `app/coding/sandbox_local.py`, `app/core/storage.py`, `app/notify/mail.py`, `backend/scripts/prepare_models.py`, 마이그레이션 0004(pgvector)·0005(LLM_EVAL)

## 맥락

팀 구성도는 내부망의 모든 컴포넌트와 코딩테스트 샌드박스를 컨테이너로 둔다. 개발 중 실제로 컨테이너로 뜬 것은 postgres·redis 두 개뿐이었다. 구성도와 실제 구성을 맞춘다.

## 결정

| 서비스 | 내용 | 망 | 비고 |
|---|---|---|---|
| frontend | Next.js :3000, 127.0.0.1:8080 공개. `/api` 를 api로 전달. SSE는 route handler로 버퍼링 없이 중계 | front | 기존 화면(react-router)을 그대로 호스팅 |
| api | FastAPI·aiortc. 127.0.0.1:8000, UDP 40000~40019 | front, internal | `[실행]`은 Celery로 worker에 보낸다(`runner_mode=celery`) |
| mailer | Mailpit. 팀 PC는 받은편지함, 기준 PC는 Gmail 중계(`MP_SMTP_RELAY_*`) | internal, egress | 유일하게 밖으로 나가는 서비스 |
| worker | Celery. 자원별 큐 그룹(llm c1, llm_eval c1, runner+sandbox c2, 나머지 c2) | internal | docker.sock으로 샌드박스 컨테이너 실행 |
| scheduler | Celery beat. 1초마다 분배·이벤트 중계, 주기 작업 | internal | |
| llm-rt | llama.cpp(GPU), RT·INTERACTIVE 레인, 슬롯 2개 | internal | |
| llm-eval | llama.cpp(CPU), BATCH 레인(사전평가·종합평가) | internal | 새 자원 `LLM_EVAL` |
| stt / tts | faster-whisper(GPU) / Supertonic(CPU) | internal | `speech_server`, 기존 `/v1/stt`·`/v1/tts` 계약 유지 |
| embed | llama.cpp BGE-M3(CPU), 1024차원 | internal | pgvector HNSW, 중복 검사 코사인 ≥ 0.85 |
| postgres | PostgreSQL 16 + pgvector | internal | |
| redis | Redis 7(AOF), Celery 브로커·실시간 알림 | internal | |
| minio | 파일 저장, SSE-S3 기본 암호화(`MINIO_KMS_SECRET_KEY`) | internal | 프로필 `minio`, 없으면 로컬 볼륨 |

1. **작업 기록은 계속 PostgreSQL `jobs`다.** `enqueue`는 호출자 트랜잭션 안이고, beat가 실행 가능한 행을 Celery `jobs.run` 메시지로 자원별 큐에 보낸다. 메시지를 받은 worker는 인프라 탐침 → 조건부 집기(`lease_token`) → 실행 순서로 처리한다. 메시지가 중복되거나 유실돼도 DB가 기준이다(30초 재전송).
2. **LLM을 두 서버로 나눈다.** 면접 중 꼬리질문(RT)은 GPU의 llm-rt, 채점(BATCH)은 CPU의 llm-eval이 맡는다. 서로 기다리지 않으므로 ADR-0003의 BATCH 선점은 같은 서버 안에서만 의미가 있다. RT와 INTERACTIVE는 llm-rt의 슬롯 2개를 나눠 쓴다.
3. **모델은 읽기 전용 `../models`** 에서만 읽는다. 컨테이너는 내려받지 않는다. `prepare_models.py`가 Ollama에 받아 둔 GGUF와 Whisper·Supertonic 캐시를 복사한다.
4. **샌드박스는 서비스가 아니다.** worker가 실행마다 `python:3.11-slim` 기반 이미지(digest 고정)로 network none 컨테이너를 만들고 지운다. 자체 점검(SBX-01~14) 결과는 Redis `aihire:sandbox:status`로 api에 알린다. Celery 자식 프로세스는 각자 점검을 갖고, 공개된 결과가 통과일 때만 자기 점검을 1회 돌린다.
5. **메일**: 지원 접수 확인, 결과 공개 알림. `send_email` 작업(자원 NONE)으로 보내고, 받는 주소는 보낼 때 조회한다. SMTP 실패는 인프라 장애로 취급한다(시도 미소모).
6. **망**: 모든 서비스는 `internal`(internal: true)에 있다. 공개 포트는 127.0.0.1에만 묶는다. 밖으로 나가는 길은 mailer의 `egress`뿐이다.
7. **점검용 구성**(`docker-compose.verify.yml`): `AI_MODE=fake`, 로컬 파일 저장. 모델 컨테이너와 minio는 띄우지 않는다. CI·클라우드에서 같은 compose로 E2E를 돌린다.

## 결과

- 구성도의 모든 컴포넌트가 compose 서비스로 존재한다. 클라우드 세션에서 점검용 구성으로 확인한 것:
  - 지원 → 음성 면접 → 코딩(`[실행]`은 Celery 경유) → 평가 → 결정 → 결과 브라우저 E2E 통과
  - 샌드박스 자체 점검 VERIFIED
  - 127.0.0.1 후보만으로 WebRTC 연결 PASS
  - Mailpit에 접수·결과 메일 도착
  - internal 망 `Internal=true`
- 사용자 PC(GPU)에서 확인이 필요한 것:
  - llama.cpp가 Ollama의 qwen3.5 GGUF를 적재하는지
  - 6 GB VRAM에 llm-rt(ctx 16384, 슬롯 2)와 STT turbo가 함께 올라가는지
  - Gmail 중계
  - MinIO 이미지 선택: Docker Hub 공식 배포가 중단됐다.
- 비용:
  - 서비스가 14개로 늘었다.
  - Celery·llama.cpp 이미지 의존이 생겼다.
  - LLM 가중치를 GPU·CPU 두 곳에 올린다(RAM 약 3 GB 추가).
- 단일 `ai` 서비스(`ai_mode=service`)와 HTTP runner(`runner_mode=http`)는 네이티브 개발용으로 남긴다.
