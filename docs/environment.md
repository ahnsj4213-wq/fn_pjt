# Phase 0 — 저장소·실행 환경 조사 기록

조사일: 2026-10-26. 아래 값은 모두 이 장비에서 명령을 실제로 실행해 확인한 것이다.

## 1. 저장소 상태

| 항목 | 확인 결과 |
|---|---|
| 작업 디렉터리 | `C:\big21\final_test` — 조사 시점에 `.claude/worktrees/`(빈 폴더) 외 파일 없음 |
| Git | 상위 `C:\big21`이 Git 저장소(브랜치 `main`, **커밋 0개**). `final_test`는 그 하위 폴더이며, 상위에는 사용자 폴더(`agentic_ai/`, `dl-dev/` 등 11개)가 미추적 상태로 있다 |
| 기존 코드 | 없음. 덮어쓸 사용자 파일 없음 |
| 처리 | 상위 저장소의 다른 폴더는 건드리지 않는다. 커밋은 사용자 승인 후에만 한다 (별도 저장소로 분리할지 사용자 결정 필요 — `open-questions.md` OQ-20) |

## 2. 하드웨어

| 항목 | 값 | 비고 |
|---|---|---|
| OS | Windows 10 Pro 10.0.19045 | 운영 서버(Linux) 아님. 개발·시연 장비 |
| CPU | AMD Ryzen 5 PRO 4650G, 6코어 12스레드 | STT·TTS·임베딩 CPU 실행 대상 |
| RAM | 63.9 GB | PRD X-03 기준(64GB)과 일치 |
| GPU | NVIDIA GeForce GTX 1660 SUPER, **6 GB VRAM** (Turing, CC 7.5, Tensor Core 없음) | PRD X-03 기준(6GB)과 일치. 데스크톱 사용으로 상시 약 0.4 GB 점유 |
| 드라이버/CUDA | Driver 616.56, CUDA UMD 13.4 | |
| 디스크 | C: 여유 255 GB, D: 여유 238 GB | |

## 3. 설치된 도구

| 도구 | 버전 | 비고 |
|---|---|---|
| Python | 3.12 (기본 `py`), 3.11.9 (`python`), 3.10 | 3.12를 백엔드 기준으로 사용 |
| Node.js / npm | v24.18.0 / 11.16.0 | pnpm 없음 |
| Docker Desktop | Client 29.6.1, Compose v5.3.0 | 조사 시점 데몬 **정지** → Phase 0에서 Docker Desktop을 기동함 |
| WSL | `docker-desktop` 배포판(WSL2) | Ubuntu 등 일반 배포판 없음 |
| Ollama | 0.40.7, `localhost:11434` 실행 중 | 로컬 LLM 서빙 런타임 |
| PostgreSQL | 로컬 설치 없음 (`psql` 없음) | Docker 컨테이너로 실행 |
| pdftotext | `/mingw64/bin/pdftotext` (Git for Windows 동봉) | pdftoppm 없음 |

## 4. 로컬 모델 자산 (이미 디스크에 있는 것)

| 모델 | 위치 | 크기 | 라이선스 | 메모 |
|---|---|---|---|---|
| `qwen3.5:4b` (Q4_K_M, 4.2B) | Ollama | 3.3 GB | Apache-2.0 | 상업 사용 가능 |
| `exaone3.5:7.8b` (Q4_K_M) | Ollama | 4.8 GB | **EXAONE AI Model License 1.1 – NC (비상업)** | 기업 채용 서비스에 쓸 수 없음 → 후보에서 제외 (ADR 참고) |
| `qwen3-embedding:0.6b` | Ollama | 639 MB | Apache-2.0 | 임베딩 후보 |
| `snowflake-arctic-embed2` | Ollama | 1.2 GB | Apache-2.0 | 임베딩 후보 |
| `openai/whisper-large-v3-turbo` | `~/.cache/huggingface` (transformers 형식) | 1.6 GB | MIT | faster-whisper용 CTranslate2 변환본은 없음 |

PRD 11.2(X-11)가 언급한 Qwen3-8B, BGE-M3는 디스크에 없다. 다운로드 여부와 실측 결과는 `docs/trd/05-ai-pipeline.md`와 `docs/adr/`에 기록한다.

## 5. 이 환경에서 할 수 없는 검증

- 운영 OS(Linux) 서버에서의 직접 실행: Docker 컨테이너로 대신한다.
- 외부망(NAT 너머) WebRTC/TURN: 같은 장비·같은 네트워크만 검증 가능.
- 다중 사용자 동시 응시 부하: 단일 장비에서 모의 부하로만 측정 가능.
- 실제 마이크 음성을 사람이 말하는 E2E: 자동화 환경에서는 합성 음성·파일 입력으로 대신한다.
