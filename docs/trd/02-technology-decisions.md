# TRD 02 — 기술 선택과 근거

- 작성: 에이전트 A. 기준: 통합 결정(`docs/design/consolidation-round1.md`), 실측(`prototypes/README.md`, `prototypes/results-*.txt`, 2026-10-26), `backend/pyproject.toml`의 고정 버전, D 설계안 3.1(프런트 버전).
- 관련 ADR: ADR-0002(스택·asyncpg), ADR-0003(LLM 공유), ADR-0004(STT·TTS·임베딩, X-11 임시), ADR-0007(샌드박스), ADR-0010(배포).
- 표기: **[실측]** = 이 장비(GTX 1660 SUPER 6GB, Ryzen 5 PRO 4650G, 64GB, Windows 10)에서 측정한 값(출처 파일 명시), **[추정]** = 측정하지 않은 값, **[문서]** = 공식 문서·라이선스 파일로 확인한 사실.

## 1. 선택 원칙

1. **오프라인 동작(AC-22)**: 런타임에 외부 네트워크를 요구하는 구성 요소는 쓰지 않는다. CDN·원격 모델 조회·텔레메트리·공용 STUN이 여기에 든다. 모든 의존성은 이미지·번들·모델 묶음으로 반입한다.
2. **라이선스**: 기업 채용 서비스에 쓸 수 있는 허용형(MIT·BSD·Apache·PSF·ISC·OFL)을 우선한다. 비상업(NC)·AGPL은 제외하고, 용도 제한 조항(OpenRAIL-M)은 법률 확인으로 넘긴다(OQ-29).
3. **실측 우선**: 6GB 장비 실측이 추정과 다르면 실측을 따른다. 실측이 없는 값은 [추정]으로 표시하고 검증 항목(TRD 01 10절)에 올린다.
4. **규모에 맞춤**: 동시 응시 1~2명(8.3)이다. 분산 처리량용 구성 요소는 도입하지 않는다(Redis, vLLM, SFU 등).

## 2. 영역별 후보 비교와 선정

| 영역 | 선정 | 검토한 대안 | 선정 근거 | 남은 위험 |
|---|---|---|---|---|
| 프런트엔드 | React 19 + TypeScript + Vite SPA | Next.js, React Router 프레임워크 모드, Vue 3 | 정적 파일만 배포(Node 서버 없음). 전 화면이 로그인 뒤라 SSR이 필요 없다. 미디어 코드는 클라이언트 전용. Next.js는 `next/font`·텔레메트리 등 빌드 시 외부 접근을 따로 꺼야 한다 | 팀 React 숙련도 가정 |
| 코드 편집기 | CodeMirror 6 | Monaco | Monaco의 React 래퍼는 기본값이 CDN 로더라 사내망에서 뜨지 않을 수 있다. CodeMirror는 번들이 작고 브라우저 IME를 그대로 쓴다 | 한글 입력 수동 확인 |
| 백엔드 | FastAPI + Pydantic v2 + SQLAlchemy 2(async) + Alembic | Django 5(+DRF/Ninja), Litestar | aiortc와 같은 asyncio 루프·패키지를 공유한다. `response_model`로 지원자 응답 화이트리스트(SEC-04). OpenAPI → TS 타입 생성 | 인증·운영 화면 직접 구현 |
| DB | PostgreSQL 16 | MySQL 8, SQLite | `SKIP LOCKED`·`LISTEN/NOTIFY`(작업 큐), CHECK·트리거(가중치 합, 불변 스냅샷, 허용 전이), JSONB(이력서 스냅샷), 부분 유니크 인덱스 | 백업 운영 |
| DB 드라이버 | asyncpg | psycopg 3 async | psycopg async는 Windows 기본 Proactor 루프를 지원하지 않는다. Windows의 asyncio 하위 프로세스는 Proactor에서만 동작한다. asyncpg + aiortc + subprocess가 한 루프에서 동작함을 조정자가 실측했다(통합 결정 T2, ADR-0002) | uvicorn의 루프 정책(ADR-0002) |
| 작업 큐 | 자체 `jobs` 테이블(영속 기록·outbox, 임대·펜싱, 인프라 게이트) + **Redis sorted set 분배**(ADR-0013, 2026-10-29) | Procrastinate, Redis + Celery/RQ | 제출과 작업 등록을 같은 트랜잭션에서 한다(7.2, 8.3 누락 0). 인프라를 추가하지 않는다. "LLM 불가 시 집지 않음"(OQ-23)을 그대로 구현할 수 있다 | 직접 구현 분량 |
| WebRTC 서버 | aiortc 1.15.0(app 프로세스 안) | LiveKit 자가 호스팅, Janus(GPL-3.0), mediasoup | 같은 Python 코드에서 프레임 게이트·VAD·STT를 직접 제어한다. Windows에서 Opus 송수신·데이터채널·리샘플이 동작한다 [실측 `results-rtc-loopback.txt`]. 브라우저↔서버 게이트도 S-1에서 확인 | 포트 범위 지정 불가 → 운영 host 네트워크(ADR-0010). 장시간·재연결 미검증 |
| LLM 런타임 | Ollama 0.40.7(설치됨) + 자체 게이트웨이 | llama.cpp `llama-server`, vLLM | GGUF Q4_K_M, JSON 스키마 강제 출력 6/6 유효 [실측]. 스트림 연결 종료로 취소가 동작한다 [실측]. Ollama에는 우선순위가 없어 게이트웨이가 레인을 갖는다(ADR-0003) | 요청마다 `num_ctx`가 다르면 재적재 → 전역 고정 |
| LLM 모델 | `qwen3.5:4b` Q4_K_M (Apache-2.0) | Qwen3-8B, Qwen3-4B-2507, Kanana-1.5-8B, `exaone3.5:7.8b` | 6GB에 전 층 적재, 53 tok/s, 꼬리질문 판단 0.57초 [실측]. 8B급은 일부 층이 CPU로 내려가 실시간 경로에 부적합 [추정, R-01] | 평가 품질(Kappa)은 골든셋 전까지 미지수(OQ-13) |
| STT | faster-whisper large-v3-turbo, GPU int8_float16. 대체 small CPU int8 | small CPU 기본(C 원안), sherpa-onnx zipformer, SenseVoice | 구간 STT: GPU turbo 1.1~1.4초, CPU small 2.0~2.6초, CPU turbo 9~11초 [실측]. 8.2 자막 2초는 GPU에서만 충족 | LLM 생성 중 GPU 경합 미측정(S-10). 8.1 해석(OQ-30) |
| VAD | 에너지 VAD(스파이크) → Silero VAD 비교 | webrtcvad | S-1은 에너지 VAD 0.5초 휴지로 동작 [실측]. 잡음 내성은 Silero 쪽이 낫다는 C 판단 [추정] | 실제 마이크 잡음 미측정(R-20) |
| TTS | Supertonic-3(`supertonic` 1.3.1, ONNX CPU) | MeloTTS-Korean, Windows SAPI, CosyVoice2 | 한국어 RTF 0.25~0.32, Whisper 되읽기 CER 0.000(3/3) [실측 `results-tts-supertonic.txt`]. MeloTTS는 미측정이고 의존성 설치 위험이 있다. SAPI는 Windows 전용 | 모델 라이선스 OpenRAIL-M·제작사 상태(OQ-29). 어댑터로 교체 가능 |
| 임베딩 | `qwen3-embedding:0.6b`(Ollama, `num_gpu=0`) | BGE-M3, `snowflake-arctic-embed2` | 1024차원 2.15초, LLM과 공존하며 재적재 없음 [실측 `results-ollama-scheduling.txt`]. 이미 설치됨 | 중복 판정 임계값 보정(C) |
| 인용 검증 | NFKC + 공백 전부 제거 + 따옴표 통일 + 앞뒤 문장부호만 무시, 원문 범위 저장 | 원문 그대로, 공백 축약(E), 문장부호 전부 제거(C qn-1) | 실제 인용 47/48 통과, 위조 0/7 통과 [실측 `results-quote-rules.txt`]. 문장부호 전부 제거는 위조 7/7 통과 | ADR-0008 |
| 샌드박스 | runner + 실행마다 일회용 Docker 컨테이너(강화 옵션), 운영은 runsc 권장 | nsjail, Firecracker, 하위 프로세스 + rlimit | 강화 옵션 컨테이너가 12/12 차단, 기동 약 0.45초 [실측 `results-sandbox-escape.txt`, Docker Desktop·runc]. 개발·운영 방식이 같다 | 운영 Linux·runsc 미검증(ADR-0007) |
| PDF 파서 | pypdf 6.21.2 (BSD-3) | PyMuPDF(AGPL), pdfminer.six | 순수 Python이라 메모리 손상 부류를 피한다. 라이선스가 허용형이다. 자식 프로세스 상한 안에서 실행 | 레이아웃 복잡한 PDF의 추출 품질 |
| 비밀번호 해시 | Argon2id(argon2-cffi 25.1.0) | bcrypt, scrypt | SEC-05, E SC-01. 표준 저메모리 프로필 | 이 장비 검증 시간 실측 필요 |
| 리버스 프록시 | Caddy 2 | Nginx, Traefik | `tls internal`로 사내 CA·인증서를 오프라인에서 자동 발급. 설정이 짧다 | 사내 루트 CA를 응시 PC에 배포 |

## 3. 실측과 추정 구분표

| 항목 | 값 | 구분 | 출처 |
|---|---|---|---|
| LLM VRAM(`num_ctx` 4096 / 8192) | 2,995 / 3,209 MiB | 실측 | `results-llm-qwen3.5-4b.txt`, `results-ollama-scheduling.txt` |
| 장비 VRAM 최대(LLM + 데스크톱) | 4,172 MiB(기준선 454) | 실측 | `results-llm-qwen3.5-4b.txt` |
| LLM 생성 속도 | 약 53 tok/s | 실측 | 같은 파일 |
| 꼬리질문 판단(웜, 출력 24토큰) | 0.56~0.57초 | 실측 | 같은 파일 |
| 문항 평가 1건(출력 약 500토큰) | 6~11초(중앙값 9.94초) | 실측(답변 1개로만 측정) | 같은 파일 |
| LLM 콜드 로드 | 약 40초 | 실측 | 같은 파일 |
| `num_ctx` 변경 시 재적재 | 약 5.1초 | 실측 | `results-ollama-scheduling.txt` |
| 선점(스트림 종료) 후 짧은 요청 | 0.49초 / 선점 없이 11.06초 | 실측 | 같은 파일 |
| STT 구간당(GPU turbo / CPU small / CPU turbo) | 1.1~1.4초 / 2.0~2.6초 / 9~11초 | 실측(SAPI 합성음 4문장) | `results-stt-gpu.txt`, `results-stt-cpu.txt` |
| 자막 종단(휴지 → 브라우저 도착) | 1.46~1.70초 | 실측(내장 Chromium, 같은 장비) | `prototypes/README.md` S-1 |
| 게이트 이전 발화 폐기 | 5.46초 분량 폐기 | 실측 | S-1 |
| aiortc 첫 프레임(기본 STUN / `iceServers=[]`) | 10.2초 / 0.17초 | 실측 | `results-rtc-loopback.txt`, README |
| TTS RTF | 0.25~0.32(5.2초 음성 1.5초) | 실측 | `results-tts-supertonic.txt` |
| 본질문 10개 사전 합성 | 15~25초 | 추정(RTF 실측 기반) | README |
| 샌드박스 기동 | 약 0.45초, 12/12 차단 | 실측(runc, WSL2) | `results-sandbox-escape.txt` |
| 인용 규칙 | 실제 47/48, 위조 0/7 | 실측(인용 55개) | `results-quote-rules.txt` |
| 답변 종료 → 다음 질문 | 3.5~4.6초 | 추정(위 실측값의 합, 경합 미포함) | TRD 01 4.2 |
| 제출 → 리포트(선평가 없이 14답변) | 약 1.4~2.6분 + 종합 | 추정 | 문항 평가 실측 × 14 |
| LLM + STT turbo 동시 적재 VRAM | 동작은 확인, 수치 미기록 | 미측정 | `results-stt-gpu.txt`(LLM 상주 상태) |
| LLM 생성 중 STT 지연(S-10) | — | 미측정 | 통합 측정 예정 |
| 동시 응시 2명 부하 | — | 미측정 | Phase 6 |

## 4. 버전·라이선스

### 4.1 백엔드 (`backend/pyproject.toml`, Python 3.12)

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| fastapi | 0.162.0 | MIT | |
| uvicorn[standard] | 0.54.0 | BSD-3 | |
| sqlalchemy[asyncio] | 2.0.55 | MIT | |
| asyncpg | 0.32.0 | Apache-2.0 | ADR-0002 |
| alembic | 1.20.1 | MIT | |
| pydantic / pydantic-settings | 2.14.1 / 2.16.1 | MIT | |
| argon2-cffi | 25.1.0 | MIT | |
| pypdf | 6.21.2 | BSD-3 | |
| python-multipart | 0.0.32 | Apache-2.0 | |
| httpx | 0.28.1 | BSD-3 | 내부 서비스 호출 |
| aiortc | 1.15.0 | BSD-3 | |
| av (PyAV) | 17.1.0 | BSD-3 | LGPL FFmpeg 동봉 → 동적 링크 유지, 재배포 시 고지 |
| numpy / soundfile | 2.5.4 / 0.14.0 | BSD-3 | |
| [ai] faster-whisper / ctranslate2 | 1.2.1 / 4.8.4 | MIT | ai 프로세스 전용 |
| [ai] onnxruntime | 1.31.0 | MIT | |
| [ai] supertonic | 1.3.1 | MIT(코드) | 모델 가중치는 4.4 |
| [dev] pytest / pytest-asyncio / reportlab | 9.1.1 / 1.4.1 / 5.1.0 | MIT / Apache-2.0 / BSD | 테스트 PDF 픽스처는 로컬 생성 |

### 4.2 프런트엔드 (D 설계안 3.1, 2026-10-26 조회값, 착수 시 lockfile로 고정)

| 패키지 | 버전 | 라이선스 |
|---|---|---|
| react, react-dom | 19.3.1 | MIT |
| typescript | 6.0.3 (7.x는 typescript-eslint 호환 확인 후) | Apache-2.0 |
| vite / @vitejs/plugin-react | 8.3.5 / 6.1.2 | MIT |
| react-router | 8.5.0 | MIT |
| @tanstack/react-query | 5.105.0 | MIT |
| react-hook-form / zod / @hookform/resolvers | 7.91.0 / 4.6.7 / 6.0.0 | MIT |
| codemirror / @codemirror/lang-python | 6.0.2 / 6.2.1 | MIT |
| radix-ui(필요 프리미티브만) | 1.8.1 | MIT |
| openapi-typescript / openapi-fetch | 7.13.0 / 0.17.0 | MIT |
| vitest / @testing-library/react / msw / jsdom | 5.0.5 / 16.3.3 / 3.0.3 / 30.1.3 | MIT |
| @playwright/test | 1.64.1 | Apache-2.0 |
| @axe-core/playwright | 4.14.0 | MPL-2.0(개발 의존성만, 배포물 미포함) |
| eslint-plugin-boundaries 또는 dependency-cruiser | 7.3.0 / 18.7.0 | MIT |
| pretendard(선택, 자체 호스팅) | 1.3.9 | OFL-1.1 |

### 4.3 런타임·이미지

| 대상 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| Python | 3.12 | PSF | [문서] `environment.md` |
| Node.js / npm | 24.18.0 / 11.16.0 | MIT 등 | 빌드 전용, 운영 반입물은 `dist/`만 |
| Ollama | 0.40.7 | MIT | 운영도 같은 버전 고정 |
| PostgreSQL 이미지 | `postgres:16-alpine` | PostgreSQL | 다이제스트 고정(반입 시) |
| 샌드박스 베이스 | `python:3.12-slim` | PSF 등 | 다이제스트 고정, pip·setuid 제거(ADR-0007) |
| Docker Desktop(개발) | Client 29.6.1, Compose v5.3.0 | Docker 구독 약관 | 운영 Linux는 Docker Engine(버전 고정, ADR-0010) |
| Caddy | 2.x | Apache-2.0 | 정확한 버전은 운영 compose 작성 시 고정 [미정] |
| nvidia-cublas-cu12 / nvidia-cudnn-cu12 | pip 휠 | NVIDIA 재배포 라이선스 | ctranslate2 GPU 실행용 |

### 4.4 모델 자산 (사전 반입, SHA-256 목록으로 확인)

| 모델 | 용도 | 크기 | 라이선스 |
|---|---|---|---|
| `qwen3.5:4b` Q4_K_M | LLM | 3.3 GB | Apache-2.0 |
| `qwen3-embedding:0.6b` | 임베딩 | 639 MB | Apache-2.0 |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | STT 기본 | 1.6 GB | MIT |
| `Systran/faster-whisper-small` | STT 대체 | 0.5 GB | MIT |
| `Supertone/supertonic-3` | TTS | 약 0.3 GB | OpenRAIL-M(용도 제한 조항 승계) → 법률 확인(OQ-29) |

## 5. 제외한 것과 이유

| 제외 대상 | 이유 |
|---|---|
| `exaone3.5:7.8b` | EXAONE AI Model License 1.1 – NC(비상업). 기업 채용 서비스에 쓸 수 없다(R-02) |
| Gemma, Llama, HyperCLOVA X SEED | 자체 이용약관·커뮤니티 라이선스. 허용형이 아니다(C 판단, 팀이 원하면 법률 검토 후 재검토) |
| Qwen3-8B 등 8B급 | 6GB에 전 층이 올라가지 않아 일부 층이 CPU로 내려간다 → 꼬리질문 5초와 충돌 [추정]. PRD 8.1 "LLM 하나"라 4B + 8B 동시 운용도 불가 |
| vLLM | Linux 전용(Windows 개발 불가), VRAM 선점유, GGUF는 실험적 지원이라 변환이 필요, Turing에서 FlashAttention-2 이점 없음 [추정]. 1~2명 규모에서는 처리량 이점이 없다 |
| PyMuPDF | AGPL. 서비스 배포 조건이 맞지 않는다 |
| XTTS-v2, MMS-TTS-kor | 비상업(CPML, CC-BY-NC) |
| Piper, Kokoro | 공식 한국어 음성 없음(R-03) |
| MeloTTS-Korean | 실측하지 않았고 Windows 의존성(mecab-ko 등)과 고정 버전 설치 위험이 있다. Supertonic이 실측으로 기준을 넘었다. 대체 후보로만 기록 |
| SenseVoice-Small | 비OSI 모델 라이선스(상업 조항 확인 필요) |
| Windows SAPI 한국어 음성 | Windows 전용 → Linux 운영 불가. 개발 시 비교용으로만 |
| psycopg 3 async | Windows Proactor 루프 미지원(ADR-0002) |
| Redis + Celery/RQ | DB 커밋과 등록이 원자적이지 않아 결국 outbox가 필요. 반입 구성 요소 +1. Celery는 Windows 비공식 |
| LiveKit·Janus·mediasoup | 미디어 서버 추가, Janus는 GPL-3.0, mediasoup은 Node + Python 이중 구조. aiortc 실측 통과로 불필요(S-1 실패 시 LiveKit 재검토) |
| Next.js | Node 서버 프로세스 추가, SSR 이점 없음, 빌드 시 외부 접근 기능 |
| Monaco Editor | CDN 로더 기본값, 무거운 번들, 한글 IME 이슈 이력 |
| nsjail, Firecracker | 개발 장비(Windows)에서 동작 불가, 운영 구성 부담. 기록만 |
| 공용 STUN/TURN | 외부 통신(AC-22). `iceServers=[]` 명시. 외부망 응시용 coturn은 구성안만(OQ-18) |
| Prometheus·Grafana 등 외부 모니터링 | MVP 범위 밖. 구조화 로그 + 집계 도구로 8.2 측정 |
| Docker Desktop Enhanced Container Isolation | 유료 구독 기능, 운영 Linux와 무관 |

## 6. X-11 임시 결정 요약

PRD 11.2 X-11(LLM·STT·TTS·임베딩 모델 선정)은 **미결정**이다. 아래는 실측을 근거로 한 기술적 임시 결정이며, 팀 확인과 법률 확인(OQ-29)을 거쳐 확정한다. 상세 비교·프롬프트·측정 계획은 `docs/trd/05-ai-pipeline.md`와 ADR-0004(C 작성)에 둔다.

| 역할 | 임시 결정 | 핵심 설정 | 근거 요약 |
|---|---|---|---|
| LLM | `qwen3.5:4b` Q4_K_M | `think=false`, `num_ctx=8192` 고정, `keep_alive` 상주, 게이트웨이 동시 1개 | 전 층 GPU, 0.57초 판단 [실측] |
| STT | large-v3-turbo GPU int8_float16 / 대체 small CPU int8 | `language=ko`, `beam_size=1` | 자막 2초 [실측], OQ-30 |
| TTS | Supertonic-3 CPU | 음성 1종 고정(공정성) | RTF 0.25~0.32, CER 0 [실측], OQ-29 |
| 임베딩 | `qwen3-embedding:0.6b` CPU | `num_gpu=0` | 공존 [실측] |

## 7. 재검토 조건

- 골든셋 Kappa가 0.6에 못 미침(8.6, P1) → 프롬프트 개선 후 8B 오프로딩 지연을 다시 재서 팀에 "품질 대 지연" 결정을 올린다(ADR-0004).
- S-10에서 LLM 생성 중 자막 p95가 2초를 넘음 → STT 요청 시 BATCH 선점을 넓히거나 STT 모델을 조정한다(ADR-0003·0004).
- Supertonic 라이선스가 불가로 판정됨(OQ-29) → TTS 어댑터를 대체 후보로 교체하고 다시 실측한다.
- 운영 Linux에서 aiortc 장시간·다른 PC 연결이 실패함 → LiveKit 자가 호스팅을 재검토한다(ADR-0001·0010).
- 운영 Engine·런타임에서 SBX가 실패함 → 실행 기능 비활성을 유지하고 런타임 조합을 재선정한다(ADR-0007).
