# 컨테이너로 전체 실행하기

`docker-compose.yml` 하나로 플랫폼 전체를 띄운다. Redis는 없다. 작업 큐는 PostgreSQL에 있다(ADR-0006).

| 서비스 | 내용 | GPU |
|---|---|---|
| `postgres` | PostgreSQL 16 (유일한 상태 저장소) | |
| `migrate` | 기동 시 1회 `alembic upgrade head` | |
| `ollama` | LLM `qwen3.5:4b`, 임베딩 `qwen3-embedding:0.6b` | ○ |
| `ollama-pull` | 기동 시 1회 두 모델이 있는지 확인하고 없으면 받는다 | |
| `ai` | LLM 게이트웨이, STT(faster-whisper large-v3-turbo), TTS(Supertonic) | ○ |
| `runner` | 코드 실행 샌드박스. 첫 기동 때 샌드박스 이미지를 빌드하고 자체 점검(SBX-01~14) | |
| `app` | API + WebRTC 음성(UDP 40000~40019 공개) | |
| `worker` | 작업 큐·주기 작업(평가, 채점, 자동 제출, 보관 삭제 등) | |
| `web` | 빌드된 화면 + `/api` 프록시(Caddy) → **http://localhost:8080** | |

## 1. 준비 (Windows + Docker Desktop)

1. Docker Desktop: WSL2 백엔드 사용(기본값). NVIDIA 드라이버가 최신이면 GPU가 컨테이너에 그대로 보인다.
   확인: `docker run --rm --gpus all ubuntu nvidia-smi` 가 GPU를 보여야 한다.
2. **이미 PC에서 직접 돌리던 것들을 끈다.** GPU 메모리(6 GB)와 포트가 겹친다.
   - 작업 표시줄 트레이의 Ollama → Quit Ollama
   - `python -m app.aisvc`, uvicorn(app), worker, runner, `npm run dev` 창 → Ctrl+C
3. 디스크: 이미지 약 6 GB + 모델 약 6 GB.

## 2. 실행

```powershell
cd C:\big21\final_test\deploy
copy .env.example .env
docker compose up -d --build
docker compose ps          # ai가 (healthy)가 될 때까지: 첫 실행은 모델 다운로드로 10분 이상 걸릴 수 있다
docker compose exec app python scripts/seed_demo.py   # 시연용 회사·담당자·공고(코딩 포함) 1회 생성
```

브라우저에서 http://localhost:8080 → 회원가입(지원자) → 공고 지원 → 장비 점검 → 면접.
담당자 계정: `rec@demo.example` / `Str0ng-pass!` (시연용. 실제 계정은 `docker compose exec app python -m app.cli create-recruiter ...`).

### 이미 받아 둔 모델 다시 쓰기 (다운로드 생략)

`.env`에서 주석을 풀고 사용자 이름을 넣는다(슬래시 `/` 사용).

```
OLLAMA_MODELS_DIR=C:/Users/<사용자>/.ollama/models
MODEL_CACHE_DIR=C:/Users/<사용자>/.cache
```

`MODEL_CACHE_DIR`에는 `huggingface`(Whisper)와 `supertonic3` 폴더가 있어야 한다. 모델을 못 찾으면 이 두 줄을 지우고 받게 둔다.

## 3. 확인

```powershell
docker compose ps                                   # 모두 running/healthy, migrate·ollama-pull은 Exited (0)
docker compose logs ai --tail 20                    # "ai_ready" 이벤트
docker compose logs runner --tail 5                 # "sandbox self-test: VERIFIED"
..\.venv\Scripts\python ..\backend\scripts\check_container_rtc.py   # 음성(WebRTC) 경로: PASS
```

`check_container_rtc.py`는 서버 응답에서 127.0.0.1 후보만 남기고 연결해 본다. 브라우저 음성이 안 되면 이것부터 확인한다.

## 4. 자주 겪는 문제

| 증상 | 원인·조치 |
|---|---|
| `could not select device driver "nvidia"` | Docker에서 GPU가 안 보인다. 드라이버·WSL2 확인. 당장은 `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d`(CPU, 느림) |
| ai가 계속 unhealthy, 로그에 CUDA out of memory | 트레이 Ollama 등 다른 GPU 프로그램을 끈다(6 GB를 LLM+STT가 거의 다 쓴다) |
| 면접 시작 시 "AI 서버를 사용할 수 없어…" | ai가 아직 healthy가 아님. `docker compose logs ai` |
| 장비 점검에서 음성 연결이 계속 실패 | UDP 40000~40019를 다른 프로그램이 쓰거나 방화벽이 막음. `check_container_rtc.py`로 확인 |
| 코드 [실행]이 "실행 환경을 사용할 수 없습니다" | `docker compose logs runner`에서 자체 점검 결과 확인 |
| 이미지 빌드 중 `CERTIFICATE_VERIFY_FAILED` | 회사 TLS 검사 프록시. 그 CA를 `deploy/build-ca.local.pem`으로 두고 `.env`에 `BUILD_CA_FILE=./build-ca.local.pem` |

## 5. 다른 PC에서 접속 (시연)

마이크는 보안 컨텍스트(HTTPS 또는 localhost)에서만 켜진다. 다른 PC는 HTTPS가 필요하다(TRD 01 §6.2, Caddy `tls internal`).
그 전 단계로 같은 PC 안에서는 위 설정 그대로 동작한다. 다른 PC용으로는 `.env`에서 `WEB_BIND=0.0.0.0`,
`TRUSTED_ORIGINS`·`RTC_ANNOUNCE_IPS`에 이 PC의 LAN IP를 추가하고 HTTPS를 붙인다.

## 6. GPU·모델 없이 구성만 점검 (CI·클라우드)

```bash
docker compose -f docker-compose.yml -f docker-compose.verify.yml up -d --build   # ai = 가짜 AI, Ollama 없음
docker compose -f docker-compose.yml -f docker-compose.verify.yml exec app python scripts/seed_demo.py --title "E2E 백엔드 개발자"
cd ../frontend && E2E_BASE_URL=http://localhost:8080 E2E_RECRUITER=rec@demo.example npx playwright test --project=chromium
```

## 7. 정리

```powershell
docker compose down          # 컨테이너만 정리(데이터·모델 볼륨 유지)
docker compose down -v       # 데이터·모델 볼륨까지 삭제
```
