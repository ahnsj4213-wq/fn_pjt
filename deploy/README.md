# 컨테이너로 전체 실행하기

`docker-compose.yml` 하나로 구성도(ADR-0014)의 모든 컴포넌트를 띄운다.

| 서비스 | 내용 | 망 | GPU |
|---|---|---|---|
| `frontend` | Next.js(:3000). 화면 + `/api` 를 api로 전달 → **http://localhost:8080** | front | |
| `api` | FastAPI + WebRTC 음성(aiortc). 127.0.0.1:8000, UDP 40000~40019 | front, internal | |
| `mailer` | Mailpit. 팀 PC는 받은편지함(http://127.0.0.1:8025), 기준 PC는 Gmail로 중계 | internal, egress | |
| `migrate` | 기동 시 1회 `alembic upgrade head` | internal | |
| `worker` | Celery: 평가·채점·리포트·메일 작업, 코드 실행 샌드박스 | internal | |
| `scheduler` | Celery beat: 주기 작업(자동 제출, 보관 삭제, 샌드박스 재점검 등) | internal | |
| `llm-rt` | llama.cpp, 면접 실시간 LLM(꼬리질문·질문 생성) | internal | ○ |
| `llm-eval` | llama.cpp, 채점용 LLM(답변 평가·리포트·코드 리뷰) | internal | CPU |
| `stt` | faster-whisper large-v3-turbo | internal | ○ |
| `tts` | Supertonic | internal | CPU |
| `embed` | llama.cpp, BGE-M3 임베딩(질문 중복 검사, pgvector) | internal | CPU |
| `postgres` | PostgreSQL 16 + pgvector | internal | |
| `redis` | Redis 7: 작업 대기줄(Celery)과 화면 실시간 알림(SSE) | internal | |
| `minio` | 파일 저장(이력서·답변 음성·TTS), 서버 측 암호화 | internal | |

- `internal` 망은 `internal: true`라 밖으로 나가는 길이 없다. 모델은 `../models` 를 읽기 전용으로만 본다.
- 코드 실행 샌드박스는 서비스가 아니다. worker가 docker.sock으로 실행마다 `python:3.11-slim` 기반 컨테이너를
  네트워크 없이(network none) 띄우고 지운다. 처음 기동할 때 샌드박스 이미지를 빌드하고 자체 점검(SBX-01~14)한다.
- 공개 포트는 모두 127.0.0.1에만 묶인다. 비밀값은 `.env`.

## 1. 준비 (Windows + Docker Desktop, 인터넷 되는 상태에서 1회)

1. Docker Desktop: WSL2 백엔드(기본값). 확인: `docker run --rm --gpus all ubuntu nvidia-smi` 가 GPU를 보여야 한다.
2. **PC에서 직접 돌리던 것들을 끈다.** GPU 메모리(6 GB)와 포트가 겹친다.
   - 작업 표시줄 트레이의 Ollama → Quit Ollama (모델 파일은 그대로 둔다. 2번에서 복사해 쓴다)
   - `python -m app.aisvc`, uvicorn, worker, runner, `npm run dev` 창 → Ctrl+C
   - `docker compose -f docker-compose.dev.yml down` (개발용 postgres·redis가 떠 있으면)
3. 모델 폴더 채우기 (Ollama에 받아 둔 모델을 복사, Whisper·Supertonic은 캐시에서 복사하거나 받는다):

   ```powershell
   cd C:\big21\final_test\backend
   ollama pull bge-m3                       # 임베딩 모델이 없으면 (트레이 Ollama를 잠시 켜서)
   ..\.venv\Scripts\python scripts\prepare_models.py --out ..\models
   ```

   마지막 줄이 `llm=OK  embed=OK  stt=OK  tts=OK` 이어야 한다. 결과:
   `models/llm/qwen3.5-4b.gguf`, `models/embed/bge-m3.gguf`, `models/hf/hub/...`, `models/supertonic3/...`
4. MinIO 이미지: MinIO는 Docker Hub 공식 이미지 배포를 중단했다. 쓸 이미지를 정해 `.env`의 `MINIO_IMAGE`에 넣는다.
   정하기 전까지는 `.env`에서 `STORAGE_BACKEND=local`, `COMPOSE_PROFILES=` (비움)으로 두면 MinIO 없이
   `appdata` 볼륨에 파일을 저장해 동작한다(암호화 저장은 MinIO를 켰을 때만).
5. 디스크: 이미지 약 8 GB + 모델 약 6 GB.

## 2. 실행

```powershell
cd C:\big21\final_test\deploy
copy .env.example .env
docker compose up -d --build
docker compose ps          # llm-rt·stt가 (healthy)가 될 때까지 1~3분
docker compose exec api python scripts/seed_demo.py   # 시연용 회사·담당자·공고(코딩 포함) 1회 생성
```

브라우저에서 http://localhost:8080 → 회원가입(지원자) → 공고 지원 → 장비 점검 → 면접.
담당자 계정: `rec@demo.example` / `Str0ng-pass!` (시연용. 실제 계정은 `docker compose exec api python -m app.cli create-recruiter ...`).
보낸 메일(지원 접수 확인, 결과 공개 알림)은 http://127.0.0.1:8025 에서 본다.

### 기준 PC: Gmail로 실제 발송

`.env`에서 `MAIL_RELAY_*` 주석을 풀고 Gmail 주소와 앱 비밀번호(Google 계정 → 보안 → 앱 비밀번호)를 넣는다.
`MAIL_FROM`도 같은 Gmail 주소로 둔다. `docker compose up -d mailer` 후 로그에
`[relay] enabling message relaying via smtp.gmail.com:587` 이 보이면 된다.

## 3. 확인

```powershell
docker compose ps                                   # 모두 running/healthy, migrate는 Exited (0)
docker compose logs worker --tail 50                # "sandbox self-test" VERIFIED, 4개 워커 ready
docker compose exec redis redis-cli get aihire:sandbox:status   # "sandbox_verified": true
..\.venv\Scripts\python ..\backend\scripts\check_container_rtc.py   # 음성(WebRTC) 경로: PASS
```

`check_container_rtc.py`는 서버 응답에서 127.0.0.1 후보만 남기고 연결해 본다. 브라우저 음성이 안 되면 이것부터 확인한다.

## 4. 자주 겪는 문제

| 증상 | 원인·조치 |
|---|---|
| `could not select device driver "nvidia"` | Docker에서 GPU가 안 보인다. 드라이버·WSL2 확인 |
| llm-rt·stt가 unhealthy, 로그에 `out of memory` | 트레이 Ollama 등 다른 GPU 프로그램을 끈다. 그래도 모자라면 `.env`에 `LLM_RT_CTX=8192` |
| llm-rt 로그에 `unknown model architecture` / `failed to load model` | Ollama의 qwen3.5 파일을 이 llama.cpp 버전이 못 읽는다. `LLAMACPP_*_TAG`를 최신으로 올리거나 Hugging Face의 Qwen3.5-4B GGUF(Q4_K_M)를 `models/llm/qwen3.5-4b.gguf`로 둔다 |
| 면접 시작 시 "AI 서버를 사용할 수 없어…" | llm-rt 또는 stt가 아직 healthy가 아님. `docker compose logs llm-rt stt` |
| 장비 점검에서 음성 연결이 계속 실패 | UDP 40000~40019를 다른 프로그램이 쓰거나 방화벽이 막음. `check_container_rtc.py`로 확인 |
| 코드 [실행]이 "실행 환경을 사용할 수 없습니다" | `docker compose logs worker` 에서 샌드박스 자체 점검 결과 확인 |
| minio 이미지를 못 받음 | 1절 4번. `MINIO_IMAGE` 지정 또는 `STORAGE_BACKEND=local` + `COMPOSE_PROFILES=` |
| 이미지 빌드 중 `CERTIFICATE_VERIFY_FAILED` | 회사 TLS 검사 프록시. 그 CA를 `deploy/build-ca.local.pem`으로 두고 `.env`에 `BUILD_CA_FILE=./build-ca.local.pem` |

## 5. 다른 PC에서 접속 (시연)

마이크는 보안 컨텍스트(HTTPS 또는 localhost)에서만 켜진다. 다른 PC는 HTTPS가 필요하다(TRD 01 §6.2).
구성도대로 포트는 127.0.0.1에만 열려 있으므로, 다른 PC용으로는 앞단에 HTTPS 프록시를 두고
`TRUSTED_ORIGINS`·`RTC_ANNOUNCE_IPS`에 이 PC의 LAN IP를 추가한다.

## 6. GPU·모델 없이 구성만 점검 (CI·클라우드)

AI는 앱 안의 가짜 AI(`AI_MODE=fake`), 파일은 로컬 볼륨. llm-rt·llm-eval·stt·tts·embed·minio는 띄우지 않는다.

```bash
docker compose -f docker-compose.yml -f docker-compose.verify.yml up -d --build --wait
docker compose exec api python scripts/seed_demo.py --title "E2E 백엔드 개발자"
cd ../frontend && E2E_BASE_URL=http://localhost:8080 E2E_RECRUITER=rec@demo.example npx playwright test e2e/candidate-flow.spec.ts --project=chromium
```

MinIO까지 점검: `STORAGE_BACKEND=s3 docker compose -f docker-compose.yml -f docker-compose.verify.yml --profile minio up -d`.

## 7. 정리

```powershell
docker compose down          # 컨테이너만 정리(데이터 볼륨 유지, models 폴더는 그대로)
docker compose down -v       # 데이터 볼륨(pgdata, redisdata, miniodata, appdata)까지 삭제
```
