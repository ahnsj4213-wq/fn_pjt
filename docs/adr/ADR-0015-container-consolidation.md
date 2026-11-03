# ADR-0015: 컨테이너 통합 (12개 → 8개)

- 상태: 채택 (2026-10-30, 사용자 결정 "6~8개 정도로 줄여서 재구성")
- 대체 범위: ADR-0014의 서비스 목록 중 `migrate`·`scheduler`·`llm-rt`·`llm-eval`·`stt`·`tts`·`embed`의 컨테이너 배치만. 망 구성, 샌드박스, 작업 큐, AI 레인, `/v1/stt`·`/v1/tts`·llama.cpp HTTP 계약은 그대로다.
- 관련: ADR-0003(레인), ADR-0004(STT·TTS), ADR-0007(샌드박스), ADR-0013(Redis), ADR-0014, `deploy/README.md`, `docs/container-architecture.md`
- 구현: `deploy/docker-compose.yml`, `deploy/docker-compose.verify.yml`, `backend/Dockerfile`(target `ai`), `backend/scripts/ai_entrypoint.sh`, `backend/app/worker_main.py`

## 맥락

ADR-0014 구성은 상시 컨테이너 12개와 1회성 `migrate` 1개였다. 컨테이너가 많아 띄우기·확인·로그 보기가 번거롭다. 메모리와 GPU 사용량은 서비스 수가 아니라 모델이 정하므로, 같은 일을 하는 서비스를 묶어도 자원 사용은 거의 같다.

## 결정

| 새 컨테이너 | 합친 것 | 방법 |
|---|---|---|
| frontend | 그대로 | |
| api | api + migrate | 명령을 `alembic upgrade head && exec uvicorn ...` 으로 바꿨다. 마이그레이션이 실패하면 api가 뜨지 않는다 |
| worker | worker + scheduler | `worker_main`이 Celery 워커 4그룹과 함께 Celery beat를 띄운다(`AIHIRE_WORKER_BEAT=0`이면 끔). worker는 1개뿐이라 beat가 겹쳐 돌지 않는다 |
| postgres | 그대로 | |
| redis | 그대로 | |
| llm-gpu | llm-rt (이름만 변경) | llama.cpp CUDA, RT·INTERACTIVE 레인 |
| ai | stt + tts + llm-eval + embed | 한 이미지에서 서버 4개(STT :8201, TTS :8202, 평가 LLM :8082, 임베딩 :8083). `ai_entrypoint.sh`가 넷 중 하나라도 끝나면 나머지를 멈추고 컨테이너를 끝내서 Docker가 넷을 함께 재시작한다 |
| mailer | 그대로 | 유일하게 밖으로 나가는 서비스 |

- `ai` 이미지는 기존 Python AI 이미지에 공식 llama.cpp CPU 이미지(`ghcr.io/ggml-org/llama.cpp:${LLAMACPP_CPU_TAG}`)의 `/app`을 `/opt/llama`로 복사하고 `libgomp1`을 더했다. llama.cpp 이미지는 Ubuntu 24.04(glibc 2.39), Python 이미지는 Debian 13(glibc 2.41)이라 그대로 실행된다.
- 헬스체크는 네 서버를 모두 확인한다. 하나라도 응답이 없으면 unhealthy다.
- minio는 지금처럼 선택 사항이다(`COMPOSE_PROFILES=minio` + `STORAGE_BACKEND=s3`일 때 9번째 컨테이너).
- 1회성 컨테이너가 없어져 `docker compose ps`에 Exited 항목이 남지 않는다. 코드 실행 샌드박스는 지금처럼 실행마다 잠깐 뜨는 임시 컨테이너다.

## 합치지 않은 것

| 후보 | 이유 |
|---|---|
| llm-rt + llm-eval (같은 모델) | GPU 6 GB 중 5 GB를 이미 쓴다(측정: 2026-10-30 `nvidia-smi` 5,013 MiB / 6,144 MiB). 평가 LLM까지 GPU에 올리면 메모리가 모자라고, 긴 평가가 면접 중 꼬리질문 응답을 늦춘다(ADR-0003) |
| redis → worker | api도 작업을 넣는다. worker가 재시작할 때마다 api의 작업 등록이 끊긴다 |
| mailer | 외부망(egress)에 붙는 유일한 컨테이너다. 다른 컨테이너와 합치면 그 컨테이너도 밖으로 나갈 수 있게 된다 |

## 결과

- 좋은 점: 상시 컨테이너 12개 → 8개, 1회성 컨테이너 없음. 메모리·GPU 사용량은 같다.
- 나쁜 점: `ai` 안의 서버 하나가 죽으면 STT·TTS·평가·임베딩이 함께 재시작한다(검증 때 약 10초). 면접 중이면 그 문항은 기존 음성 장애 복구 흐름(INTERRUPTED → 재접속)을 탄다. 평가 작업은 인프라 게이트(ADR-0006)가 시도 횟수를 쓰지 않고 미룬다.
- 로그: `docker compose logs ai` 하나에 네 서버 로그가 섞인다. STT·TTS는 JSON 한 줄 로그, llama.cpp는 자체 형식이라 구분된다.

## 검증 (2026-10-30, 이 PC: GTX 16xx 6 GB, Docker Desktop)

- `docker compose up -d --build --remove-orphans --wait`: 8개 모두 healthy. 마이그레이션 0007 적용 확인(`alembic_version`).
- worker 로그에 `celery beat v5.6.3 ... is starting` 확인.
- api·worker 컨테이너에서 `ai:8201/8202/8082/8083`, `llm-gpu:8080` 헬스 200.
- ai 컨테이너 안에서 실제 요청: 임베딩 1024차원(0.1초), 평가 LLM 응답(0.7초), TTS 합성 → 그 음성을 STT로 되돌려 같은 문장 인식(large-v3-turbo, cuda, 1.5초).
- 임베딩 서버만 강제 종료 → 컨테이너 재시작(RestartCount 1) → 약 10초 뒤 healthy.
- 백엔드 pytest 133개 통과(컨테이너 밖, 개발 DB).
- 미검증: 면접 진행 중 ai 재시작 시 브라우저 쪽 복구 흐름(E2E는 사용자 요청 전까지 보류).
