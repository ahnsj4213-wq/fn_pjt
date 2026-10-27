# 에이전트 A 설계안 — 아키텍처·서비스 경계·배포 토폴로지 (1단계 독립안)

- 작성: 에이전트 A(아키텍처·기술 리드), 2026-10-26. 상태: **제안(미합의)**. 다른 에이전트 설계안은 보지 않고 작성했다.
- 기준: 통합 PRD v1.0 초안, `docs/requirements-traceability.md`, `docs/open-questions.md`, `docs/risks.md`, `docs/environment.md`.
- 표기: **[추정]** = 이 장비에서 실측하지 않은 값. **[OQ-nn 권장안]** = 미결정 사항의 임시 해석이며 확정이 아니다.
- PRD는 How를 TRD로 넘겼다. 따라서 이 문서의 프레임워크·런타임·도구는 모두 **PRD 원문 외** 기술 선택이다. PRD가 직접 언급한 기술은 WebRTC(X-01), TURN, 4bit 양자화 LLM 1개, X-11 후보(Qwen3-8B·Whisper 계열·BGE-M3), vLLM(C PRD의 가정), HTTPS뿐이다.

## 1. 범위와 전제

### 1.1 이 문서가 정하는 것과 정하지 않는 것

| 정한다 (A) | 정하지 않는다 (담당) |
|---|---|
| 프로세스 경계, 포트, 프로세스 간 통신 방식, 배포 토폴로지(개발·운영·오프라인 시험) | DB 스키마 세부, 상태 전이 구현 (B) |
| 스택 후보 비교와 권장안 | 모델 선정·프롬프트·VAD 파라미터 (C, X-11) |
| GPU 공유 스케줄링 원칙, 동시 응시 상한과의 관계 | 화면·컴포넌트 설계 (D) |
| 설정·관측성 원칙, 장애 대체 경로 개괄 | 샌드박스 플래그·위협 모델·인증 세부 (E) |
| 다른 에이전트용 인터페이스 초안 (4장) | 일정·릴리스·ADR 등록 (F) |

### 1.2 전제

| 항목 | 값 | 근거 |
|---|---|---|
| GPU | GTX 1660 SUPER 6GB, Turing CC 7.5, Tensor Core 없음. 개발 장비는 데스크톱이 상시 0.4GB 점유 | environment.md (실측) |
| CPU·RAM | Ryzen 5 PRO 4650G 6코어 12스레드, 64GB | environment.md (실측) |
| LLM | 4bit 1개를 질문 생성·꼬리질문·평가에 공용 (8.1). 1순위 후보 `qwen3.5:4b` Q4_K_M 3.3GB. `exaone3.5`는 비상업 라이선스라 제외 (R-02) | 8.1, R-01, R-02 |
| 동시 응시 | 6GB 장비에서 1~2명으로 시작 (8.3). 설정 기본값 2 [OQ-15 권장안] | 8.3 |
| 운영 | 온프레미스 Linux 단일 호스트(GPU 1장), docker compose. 개발은 Windows 10 + Docker Desktop(WSL2) | 과제 전제 |
| 네트워크 | 시연은 같은 사내망 (8.1). 외부망 TURN은 미검증 [OQ-18 권장안] | 8.1, R-08 |
| 브라우저 | PC Chrome·Edge 최신. 마이크는 HTTPS 또는 localhost에서만 허용 | 8.4, 8.7, R-09 |

### 1.3 설계 목표 순서

1. 면접이 멈추지 않는다 (8.3: AI 장애 시 질문은행·텍스트·'답변 없음'으로 대체).
2. 제출 건은 평가 누락 0건 (7.2: 제출과 같은 트랜잭션으로 작업 등록, 실패를 끝 상태로 두지 않음).
3. 실시간 지연 목표 (8.2: 본질문 음성 1초, 자막 2초, 다음 질문 5초, 리포트 3분/최대 10분).
4. 격리와 데이터 경계 (SEC-03·04·06, COD-06).
5. 단순성: 1~2명 규모다. 분산 시스템을 만들지 않고 호스트 1대 + compose로 끝낸다.

## 2. 설계 대안 비교

### 2.1 프로세스 경계

| 안 | 구성 | 장점 | 단점·위험 |
|---|---|---|---|
| M1 모놀리스 | uvicorn 1개에 API + aiortc + STT + 평가 | 배포가 가장 단순 | Opus 디코딩·VAD·STT 같은 CPU 작업이 이벤트 루프를 막아 API 응답이 흔들린다. 평가 워커만 멈췄다 켜는 AC-18 시험이 불가. UDP 노출과 Docker 소켓 권한이 API 프로세스에 섞인다 |
| **M2 역할별 프로세스, 코드베이스 1개 (권장)** | api / rtc / worker-ai / worker-sys / ai-gw + 외부 런타임(ollama, speech, runner, postgres, caddy) | 장애·자원·권한 격리, 도메인 로직은 공유 패키지 하나, AC-18을 프로세스 단위로 시험 | 프로세스 10개 운영. 개발 기동 스크립트가 필요 |
| M3 도메인별 마이크로서비스 | auth·posting·interview 등 서비스별 DB | 독립 배포 | 1~2명 규모에 과하다. "제출 + 평가 작업 등록" 트랜잭션이 서비스 경계를 넘어 7.2를 지키기 어렵다 |

### 2.2 프런트엔드

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **React + Vite + TypeScript SPA (권장)** | 정적 번들을 Caddy가 서빙해 Node 서버 프로세스가 없다. 외부 CDN 없이 오프라인 동작(AC-22)이 쉽다. getUserMedia·WebRTC는 어차피 클라이언트 전용 | SSR 없음(로그인 뒤 화면이라 필요 없음) |
| Next.js | 라우팅·SSR 내장 | Node 서버 1개 추가. SSR 이점이 없고 미디어 코드가 서버 렌더 경계와 충돌. `next/font` 같은 빌드 시 외부 접근 기능을 끄는 관리가 필요 |
| Vue 3 / SvelteKit + Vite | 번들이 작다 | 팀 익숙도·자료량은 D가 판단 |

코드 편집기(COD-02)는 CodeMirror 6(가벼움)과 Monaco(무겁고 워커 설정 필요) 중 D가 고른다. 조건은 CDN 금지, 번들 포함.

### 2.3 백엔드

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **FastAPI + Pydantic v2 + SQLAlchemy 2(async, asyncpg) + Alembic (권장)** | asyncio 하나로 aiortc와 같은 패키지·도메인 서비스를 공유. `response_model`로 지원자 응답 화이트리스트(SEC-04). OpenAPI에서 TS 타입 생성(D와의 계약) | 인증·운영자 화면을 직접 만든다 |
| Django 5 + DRF/Ninja | 인증·admin(운영자 UI 대용, OQ-19)·마이그레이션 내장 | 비동기 ORM 제약. aiortc·비동기 워커와 실행 모델이 둘로 갈린다 |
| Litestar | FastAPI와 비슷, DTO 계층 | 자료·커뮤니티가 적어 학습 비용 |

### 2.4 DB

| 안 | 판단 |
|---|---|
| **PostgreSQL 16 (권장)** | `SKIP LOCKED`·`LISTEN/NOTIFY`(작업 큐), CHECK·트리거(가중치 합 100, QST-06 UPDATE 금지), JSONB(이력서 스냅샷), 부분 유니크 인덱스(INT-12). 단점은 백업 운영 부담뿐 |
| MySQL 8 | `SKIP LOCKED`는 있으나 트리거·CHECK·JSON 표현력이 약하고 팀 전제와 다르다 |
| SQLite | 다중 프로세스 동시 쓰기와 `SKIP LOCKED`가 없어 제외 |

질문은행 유사도(QST-01)는 은행 규모가 수천 건 이하 [추정]라 앱에서 코사인 계산으로 충분하다. pgvector는 선택 사항(원문 외 추가 의존성)으로 둔다.

### 2.5 작업 큐

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **자체 `jobs` 테이블 + SKIP LOCKED + 임대·펜싱 (권장)** | 제출과 같은 트랜잭션으로 등록(7.2, 8.3)이 그대로 된다. 인프라 추가 0. "LLM 불가 시 작업을 집지 않음"(OQ-23), 레인별 동시성, 펜싱(R-14)을 원하는 대로 | 재시도·주기 작업을 직접 구현. 300~500줄 [추정] |
| Procrastinate (Postgres 기반 Python 라이브러리) | 같은 트랜잭션 등록, 재시도·주기 작업 제공 | 임대 만료·펜싱 토큰·인프라 게이트를 라이브러리 밖에서 덧대야 한다 |
| Redis + Celery / RQ / Dramatiq | 성숙 | Redis 추가. DB 커밋과 큐 등록이 원자적이지 않아 결국 outbox 테이블이 필요. Celery는 Windows 비공식 |

### 2.6 WebRTC 서버 측 (X-01 확정, WebSocket 업로드는 대안이 아니다)

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **aiortc를 쓰는 독립 rtc 프로세스 (권장)** | 같은 Python 코드베이스. 프레임 단위 마이크 게이트(AC-09), VAD 분할, STT 호출을 직접 제어. 미디어 서버 추가 없음 | (1) aioice는 포트 0으로 바인딩하며 **포트 범위 옵션이 없다**(aioice `Connection.__init__` 인자 확인). 브리지 네트워크 컨테이너의 host 후보에 브라우저가 닿지 못하므로 운영은 host 네트워크가 필요하다. (2) 서버 쪽 후보를 모두 모은 뒤 answer를 돌려준다(non-trickle). (3) Windows·장기 연결 안정성 미검증(R-10). (4) 파이썬 RTP 처리 CPU |
| LiveKit 서버 자가 호스팅 + livekit-agents(Python) | 포트 범위·내장 TURN·재연결이 성숙. 음성 에이전트용 구조 | Go 서버 + 에이전트 워커가 추가된다. 플러그인 기본값이 클라우드라 로컬 STT·TTS 어댑터를 직접 쓴다. 반이중 게이트를 SFU 밖에서 구현. 학습 비용 |
| Janus / mediasoup | 성숙한 미디어 서버 | Janus는 GPL-3.0. mediasoup은 Node 앱 + plain RTP로 Python에 다시 보내는 이중 구조. 1~2명 규모에 과하다 |

결론: aiortc 1순위. 스파이크 S-1(6장)이 실패하면 LiveKit로 바꾼다. 교체 비용을 줄이려고 rtc 내부를 "오디오 프레임 공급자"와 "면접 진행기(conductor)"로 나누고, 진행기는 프레임 공급자 인터페이스만 안다.

### 2.7 LLM 런타임 (6GB Turing)

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **Ollama 0.40.7 (권장: 개발·초기 운영)** | 이미 설치·실행 중. GGUF Q4_K_M, JSON schema 구조화 출력(`format`), `keep_alive`로 상주 | 요청 우선순위 없음(FIFO 대기열). `OLLAMA_NUM_PARALLEL`은 모델 적재 시 고정. 요청마다 `num_ctx`가 다르면 재적재될 수 있다 [S-3 확인]. 진행 중 생성의 취소는 클라이언트 연결 종료로 한다 [S-3 확인] |
| llama.cpp `llama-server` (운영 대안) | 같은 GGUF 파일. `--parallel`·`-c` 고정, 슬롯·프롬프트 캐시 제어, `/metrics`, JSON schema 문법 제약 | 모델 관리 수동, 임베딩 인스턴스 별도, CUDA 빌드 버전 관리 |
| vLLM | 다중 사용자 처리량 | Linux 전용(Windows 개발 불가), VRAM을 미리 점유, GGUF는 실험적이라 AWQ/GPTQ 변환 필요, Turing에서는 FlashAttention-2 이점이 없다 [추정]. 1~2명 규모에서 처리량 이점이 의미 없다 |

결론: 앱 코드는 LLM 런타임을 직접 부르지 않고 **ai-gw만** 부른다. Ollama와 llama-server 교체는 ai-gw 어댑터 설정으로 한다.

### 2.8 코드 샌드박스 (배치 관점, 보안 세부는 E)

| 안 | 장점 | 단점·위험 |
|---|---|---|
| **runner 서비스 + 실행마다 일회용 Docker 컨테이너 (권장)** | 개발(Docker Desktop)과 운영이 같은 방식. 운영은 `runtime: runsc`(gVisor)로 커널 격리 보강(R-06). 자원 제한이 표준 플래그 | Docker 소켓 = 호스트 root (R-06) → runner만 접근. 컨테이너 기동 0.5~1.5초 [추정] |
| nsjail / bubblewrap (runner 안에서) | 기동 수 ms, 소켓 불필요 | Linux 전용이라 Windows 개발에서 못 돌린다. runner 컨테이너에 CAP_SYS_ADMIN 등 특권 필요 |
| Firecracker microVM | 격리가 가장 강하다 | KVM 필요, 구성 복잡, 이 규모에 과하다 |

### 2.9 리버스 프록시

| 안 | 판단 |
|---|---|
| **Caddy 2 (권장)** | `tls internal`로 사내 CA와 인증서를 오프라인에서 자동 발급. 설정이 짧다. 회사 CA 인증서를 받으면 파일로 교체 |
| Nginx | 표준·자료 많음. 인증서는 사내 CA·mkcert로 수동 |
| Traefik | 컨테이너 라벨 기반 자동 구성. 서비스 10개 고정 규모에서는 이점이 적다 |

## 3. 권장안과 근거

### 3.1 프로세스 구성

| 프로세스 | 역할 | 기술 | 포트 | 네트워크·권한 | 동시성 |
|---|---|---|---|---|---|
| `caddy` | HTTPS 종단, SPA 정적 파일, `/api/*`→api, `/rtc/*`→rtc(시그널링 HTTP만) | Caddy 2 | 443, 80(→443) | edge | — |
| `api` | REST 전부. 인증·권한·공고·지원·이력서 업로드·면접 시작·코딩 저장/실행 중계·리포트·결정 | FastAPI, uvicorn | 8000 | app, DB | 워커 2 |
| `rtc` | aiortc 연결, 반이중 마이크 게이트, VAD 분할, 자막 DataChannel, 면접 진행기, 답변 즉시 저장, 하트비트 | aiortc + FastAPI(시그널링) | 8100/tcp + UDP(OS 임시 포트) | 운영은 host 네트워크, DB | 단일 프로세스·단일 루프. 연결 상한 = 응시 상한 + 점검 2 |
| `worker-ai` | `resume_extract`, `interview_prep`(질문 구성+사전 TTS), `eval_answer`(EVL-05), `evaluate_application`, `code_review` | 같은 backend 패키지 | — | app, DB | 1 (GPU가 어차피 직렬) |
| `worker-sys` | `grade_code`, `coding_autosubmit`(COD-05), `posting_autoclose`(JOB-03), `attempt_expire`, `slot_reaper`, `retention`(SEC-07) | 같은 backend 패키지 | — | app, DB | 2 |
| `ai-gw` | LLM 우선순위 스케줄러, 선점, 호스트 허용목록(SEC-06), 모델 digest 보고(EVL-08), LLM 헬스 | FastAPI 소형 | 8200 | app+ai, **DB 없음** | 레인별 큐 |
| `ollama` | LLM 서빙(GPU), 임베딩(CPU 강제) | Ollama 0.40.7 고정 | 11434 | ai만 | `NUM_PARALLEL=1` 시작 |
| `speech` | STT·TTS (모델은 C가 X-11로 선정) | Python | 8300 | ai만 | CPU 스레드 상한 설정 |
| `runner` | 코드 실행·채점, 샌드박스 자체 점검 | Python 소형 + Docker Engine API | 8400 | exec, Docker 소켓, **DB 없음** | 세마포어 2 |
| `postgres` | 유일한 상태 저장소(파일은 볼륨) | PostgreSQL 16 | 5432 | app만 | — |

경계를 이렇게 나눈 이유:

- **rtc를 api와 분리**: Opus 디코딩·리샘플·VAD가 CPU를 쓰고 UDP와 host 네트워크가 필요하다. api 지연과 권한 범위를 지키려고 떼어 낸다. 도메인 로직(상태 전이, 답변 저장)은 `app/interview` 공유 모듈을 둘 다 import한다.
- **면접 진행기를 rtc 안에**: "TTS 끝 → 게이트 열림 → 프레임 → 자막 → [답변 완료] → 저장 → 꼬리질문 → 다음 질문" 루프가 미디어와 강하게 묶여 있다. 프로세스 간 왕복을 없애 5초 예산을 지킨다. 다만 **마감 시각의 원본은 DB**(`answer_deadline_at`)다. rtc는 그것을 집행할 뿐이라 텍스트 답변 대체 경로(INT-18)에서는 api가 같은 값으로 검증한다.
- **ai-gw를 따로 두는 이유**: LLM 호출자가 셋(rtc 실시간, worker-ai 배치·준비, api는 없음)이다. 우선순위와 선점은 전역 대기열 하나에서만 결정할 수 있다. Ollama는 FIFO라 앱 쪽 게이트가 필요하다.
- **워커 두 개**: 10분 걸릴 수 있는 평가가 자동 제출(COD-05)·마감 처리 같은 시간 민감 작업을 막지 않게 한다.
- **runner·ai-gw는 DB 자격 증명이 없다**: 가장 위험한 두 경계(임의 코드, 모델 입출력)에서 데이터 접근을 원천 차단한다.

### 3.2 데이터 흐름

| 단계 | 처리 위치 | 동작 | 관련 ID |
|---|---|---|---|
| 1 동의·지원 | api | 동의 기록(문구 버전·항목·일시) → 지원 생성(`UNIQUE(posting_id, candidate_id)`) | SEC-01·02, JOB-02 |
| 2 이력서 | api → worker-ai | PDF를 크기·매직바이트 검사 후 `files` 볼륨에 UUID 이름으로 저장. 같은 트랜잭션에서 `resume_extract` 등록. 워커가 자식 프로세스(시간·메모리 제한)에서 텍스트 추출 → 글자 수 임계값(RES-04) → ai-gw INTERACTIVE 레인으로 구조화 → 원문 부분 문자열 검증 → 초안 저장. 화면은 폴링 | RES-02~05 |
| 3 지원 확정 | api | 표준 항목 JSON 스냅샷 + PDF 사본 참조를 지원 건에 고정 | RES-07 |
| 4 준비 진입 | api → worker-ai | READY→PREPARING과 같은 트랜잭션에서 `interview_prep` 등록. 워커가 본질문 10개 구성(은행 선택, 이력서 질문 근거 검증, 커스텀 질문) → `speech`로 10개 사전 TTS → `tts` 볼륨 저장 → prep READY. 생성 실패는 1회 재시도 후 은행 질문 | QST-01~03·07, 8.2 |
| 5 환경 점검 | 브라우저 + rtc | 마이크·카메라는 브라우저. WebRTC 점검은 `purpose=precheck` 연결로 짧은 발화를 STT해 인식 결과를 돌려주고 rtc가 통과 기록 저장 | INT-01·17 |
| 6 면접 시작 | api | 한 트랜잭션: 공고 OPEN, 점검 통과, AI 상태(LLM·STT 필수, TTS는 경고 [OQ-08 권장안]), prep READY, 슬롯 확보 [OQ-15·16 권장안], PREPARING→INTERVIEWING, 공고 설정 잠금 [OQ-05 권장안], 질문 스냅샷 동결(QST-06) | INT-13·14, JOB-06 |
| 7 실시간 루프 | rtc | 3.3절 | INT-04~11·15, 6.2 |
| 8 코딩 | api, runner, worker-sys | INTERVIEWING→CODING에서 `coding_deadline_at` 기록 + `coding_autosubmit`(run_after=마감) 등록. 자동 저장, [실행]은 api→runner(공개 예시만) | COD-01~06 |
| 9 제출 | api 또는 rtc | 한 트랜잭션: 코드 스냅샷, →SUBMITTED, `eval_status=PENDING` [OQ-06 권장안], `grade_code`·`evaluate_application` 등록 | 7.2, 8.3 |
| 10 평가 | worker-ai, worker-sys | 채점 완료 후, 문항별 판정(EVL-05로 미리 끝난 것은 재사용) → 인용 검증·1회 재생성 → 종합·담당자 리포트 → 지원자용 피드백(별도 테이블) → 코드 리뷰(P1) → 서버 코드 점수 계산(Decimal) → 펜싱 조건부 저장 → DONE | EVL-01~08, RPT-02·03 |
| 11 결정·공개 | api | 담당자 결정·이력 → 지원자 결과 API가 6.6 범위로만 응답 | ADM-04, RPT-03, SEC-04 |

### 3.3 실시간 면접 루프와 5초 예산

1. rtc가 DataChannel `question` 전송과 동시에 사전 생성 TTS를 **서버→브라우저 오디오 트랙**으로 재생한다. 8.1 "음성은 브라우저와 서버가 WebRTC로 직접 주고받는다"를 양방향으로 해석했다. 서버가 송출 종료 시각을 정확히 알아 게이트 판정이 서버 기준이 된다.
2. 송출 종료 + 보호 구간 300ms [추정, 지터 버퍼 고려] 뒤 게이트를 열고 `mic_open{deadline_at}`을 보낸다. 게이트 이전 프레임은 버린다(AC-09). 브라우저의 `tts_playback_ended`는 참고만 한다.
3. 프레임(48kHz Opus)을 16kHz mono PCM으로 바꾸고 VAD로 끊긴 구간을 잘라 `speech /v1/stt`로 보낸다. 결과는 `caption`으로 전송(목표: 말이 끊긴 뒤 2초 안).
4. `answer_complete` 수신 또는 `deadline_at + 유예 1.5초` [추정]에 확정한다. 침묵으로는 끝내지 않는다(X-02). 남은 시간 10초 시점(본질문 50초, 꼬리질문 35초)에 `warn` 전송.
5. 마지막 구간 STT를 마치고 한 트랜잭션에서 답변(텍스트, 음성 파일 경로, 시각, 입력 방식)을 저장하고 `eval_answer`를 등록한다(INT-09, EVL-05).
6. 규칙 판정(내용 없음 INT-10, 횟수 1/4) → 통과 시 ai-gw RT 레인에 `deadline_ms`를 붙여 꼬리질문 판단을 요청한다 → 기한 초과나 불가면 건너뛴다(6.2).

| 구간 (답변 종료 → 다음 질문 표시) | 예산 |
|---|---|
| 마지막 구간 STT | ≤ 1.0초 [추정, C 실측] |
| 답변 저장 트랜잭션 | ≤ 0.1초 [추정] |
| LLM 꼬리질문 판단 (RT 하드 타임아웃 3.2초) | ≤ 3.0초 [추정: 입력 1.2k 토큰 prefill 0.8~1.5초 + 출력 ≤80 토큰 1.5~2.3초] |
| 꼬리질문 TTS 즉시 생성 (넘치면 텍스트 먼저 [OQ-22 권장안]) | ≤ 0.8초 [추정] |
| 본질문으로 넘어갈 때 (사전 TTS) | ≤ 0.3초 |
| 합계 | ≤ 5.0초 |

### 3.4 GPU 6GB 공유 스케줄링

VRAM 예산 [모두 추정, S-3에서 실측]:

| 항목 | 개발(Windows) | 운영(헤드리스 Linux) |
|---|---|---|
| 데스크톱 점유 | 0.4GB (실측) | ~0 |
| LLM 가중치 `qwen3.5:4b` Q4_K_M | 3.1~3.4GB | 3.1~3.4GB |
| KV 캐시 + 연산 버퍼 (`num_ctx` 8192, parallel 1) | 0.6~1.2GB | 0.6~1.2GB |
| CUDA 컨텍스트 | 0.2~0.3GB | 0.2~0.3GB |
| 남는 여유 | 0.7~1.7GB | 1.1~2.1GB |

원칙:

1. **레인 3개, 엄격 우선순위**: RT(꼬리질문 판단) > INTERACTIVE(이력서 구조화, 질문 구성 — 지원자가 기다림) > BATCH(평가, 코드 리뷰, 은행 보충).
2. **선점**: `NUM_PARALLEL=1`에서 RT 요청이 오면 진행 중인 BATCH·INTERACTIVE 요청의 HTTP 스트림을 끊어 취소하고 대기열 맨 앞에 다시 넣는다. 선점은 EVL-06의 실패 시도로 세지 않는다(ai-gw가 `PREEMPTED`로 응답).
3. **기한 판정**: RT는 `deadline_ms`를 받는다. 대기 시간 + 예상 실행 시간이 기한을 넘으면 즉시 `DEADLINE_EXCEEDED`를 돌려 rtc가 건너뛴다. 면접은 멈추지 않는다.
4. **모델 상주**: LLM 1개만 GPU에 둔다(`OLLAMA_MAX_LOADED_MODELS=1`, `keep_alive=-1`). 임베딩은 CPU로 강제(`num_gpu=0` 또는 별도 CPU 인스턴스)해 LLM이 내려가 재적재되는 일(수 초 [추정])을 막는다. `num_ctx`는 모든 요청에서 같은 값으로 고정한다.
5. **배치는 잘게**: 평가는 답변 1개당 LLM 1회로 쪼개 선점 손실을 1회분(약 10초 [추정])으로 제한한다.
6. **STT를 GPU에 올릴지는 C의 실측 뒤**: 여유가 0.8GB 이상 남을 때만 [추정 기준]. 올리면 이 표를 갱신하고 LLM `num_ctx`를 줄이지 않는 것이 우선이다.

동시 응시 상한과의 관계 [모두 추정]:

| 항목 | 1명당 | 상한 2일 때 |
|---|---|---|
| 면접 길이 (최악) | 10×(TTS 8초+답변 60초+전환 5초) + 꼬리 4×55초 ≈ 16분 | — |
| RT 점유 | 최대 14회 × 3초 ≈ 42초 → 약 4~5% | 약 9% |
| BATCH 점유 (판정 14회×9초 + 종합·피드백 30초 + 코드 리뷰 15초) | ≈ 3분 → 16분 중 약 19% | 약 38% |
| RT 충돌 (두 답변 종료가 3초 안에 겹칠 확률) | — | 약 10% → 그 경우 뒤 사람의 꼬리질문이 생략될 수 있음 |

결론: 상한 2에서 GPU는 병목이 아니고, 병목은 CPU STT일 가능성이 크다(R-04). 상한은 `min(GPU 기준, CPU STT 기준, rtc CPU 기준)`으로 정하고 S-4·S-5 측정 뒤 확정한다(8.3). 상한 3 이상은 RT 충돌과 리포트 지연이 같이 커진다. RT 충돌이 문제가 되면 `NUM_PARALLEL=2`(슬롯 하나를 RT 전용으로 운용)를 S-3에서 비교한다.

리포트 3분(8.2, P0)은 **EVL-05(P1) 문항별 선평가에 사실상 의존**한다. 선평가 없이 제출 후 전부 평가하면 약 3분 [추정]으로 경계선이다. 선평가하면 제출 후 마지막 문항 + 종합만 남아 약 1분 [추정]이다. P1인 EVL-05를 P0 일정 안에 넣기를 권한다(우선순위 변경이 아니라 구현 순서 제안).

### 3.5 배포 토폴로지

**개발 (Windows 10 + Docker Desktop)**

| 위치 | 구성 |
|---|---|
| Windows 네이티브 | Ollama(11434, 이미 실행 중, GPU 사용), Python 3.12 venv에서 api(8000)·rtc(8100)·ai-gw(8200)·speech(8300)·worker-ai·worker-sys, Vite 개발 서버(5173, `/api`·`/rtc` 프록시) |
| Docker Desktop | postgres(127.0.0.1:5432), runner(127.0.0.1:8400, Docker 소켓 마운트)와 샌드박스 컨테이너 |
| 브라우저 | `http://localhost:5173` — localhost는 보안 컨텍스트라 마이크 허용. 미디어는 브라우저↔네이티브 rtc 직접 |

rtc를 네이티브로 두는 이유: Docker Desktop 컨테이너 IP는 Windows 브라우저에서 닿지 않고, aioice 임의 포트는 publish할 수 없다. 기동은 `deploy/dev/up.ps1` 하나로 묶는다.

**운영 (Linux 단일 호스트, docker compose)**

```
[사내망 PC 브라우저] --HTTPS 443--> caddy --(edge)--> api:8000 / rtc:127.0.0.1:8100(host-gateway)
        \--UDP(OS 임시 포트, 사내 서브넷만 허용)--> rtc (network_mode: host)
 app 네트워크(internal: true): api, worker-ai, worker-sys, ai-gw, postgres
 ai 네트워크(internal: true):  ai-gw, ollama(GPU), speech
 exec 네트워크(internal: true): api·worker-sys ↔ runner  (runner만 /var/run/docker.sock)
 샌드박스 컨테이너: network_mode none, runtime runsc(설치 시)
 rtc(host 네트워크) → postgres·ai-gw·speech는 127.0.0.1에만 publish된 포트로 접근
 볼륨: /srv/airec/{pgdata, files, audio, tts, models, backup}
```

- 모든 서비스 `restart: unless-stopped`. 기동 순서: postgres → `migrate`(1회성) → ollama(워밍업) → ai-gw·speech·runner → api·workers·rtc → caddy.
- HTTPS: 회사 CA 인증서가 있으면 파일로 마운트, 없으면 Caddy `tls internal` 루트 CA를 시연 PC에 설치. 다른 PC에서 `http://<IP>`로 들어오면 마이크가 막힌다(R-09).
- 외부망 응시는 범위 밖. coturn 구성안만 문서화 [OQ-18 권장안]. aiortc는 TURN 클라이언트를 지원하므로 코드 변경 없이 설정으로 붙는다.

**오프라인 시험 (AC-22, `offline-test` 프로필)**: 모든 네트워크 `internal: true`. Playwright Chromium 컨테이너를 같은 내부망에 두고 `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<합성 음성>.wav`로 E2E를 돌린다. 브리지망 안이라 rtc 컨테이너의 host 후보에 닿으므로 이 프로필에서는 rtc도 일반 컨테이너로 둔다. 시험 시작 시 외부 egress(DNS 조회·외부 IP 연결)가 **실패하는지 먼저 확인**한다.

AC-22 충족 장치: (1) 프런트 번들에 외부 CDN·폰트 URL 없음(빌드 산출물 grep 검사), (2) 모델은 `models` 볼륨에 사전 반입하고 SHA-256 목록으로 확인, `HF_HUB_OFFLINE=1`, (3) 이미지는 `docker save` 묶음으로 반입, (4) ai-gw·speech 엔드포인트 설정은 localhost·사설 대역만 허용하고 위반 시 기동 거부(SEC-06), (5) 운영 호스트 방화벽 egress 차단.

### 3.6 디렉터리 구조 제안 (추적표의 경로 이름과 맞춤)

```
final_test/
  backend/                 # 하나의 Python 패키지, 진입점만 다름
    pyproject.toml         # extras: [rtc] (aiortc, av), [worker], [dev]
    app/
      core/                # settings, constants(PRD 고정값), logging, db, errors
      auth/ consent/ postings/ applications/ resumes/ questions/
      interview/           # 상태 전이, 슬롯, 진행기(conductor) 순수 로직
      voice/               # rtc.py(aiortc), gate.py, vad.py, dc_protocol.py, stt_stream.py
      coding/ evaluation/ reports/ recruiter/ audit/ retention/
      ai/                  # ai-gw 클라이언트, health, 허용목록
      aigw/                # ai-gw 서비스 (python -m app.aigw)
      worker/              # jobs 테이블 접근, 핸들러 등록 (python -m app.worker --lanes ...)
      cli.py               # 운영자 CLI (OQ-19)
    migrations/  tests/
  speech/                  # 별도 패키지·이미지 (무거운 의존성 분리, C 담당)
  runner/                  # 별도 패키지·이미지, DB 의존성 없음
  sandbox/                 # sandbox-py312 이미지 Dockerfile, 채점 하니스
  frontend/                # Vite + React + TS (src/pages/candidate, src/pages/recruiter)
  deploy/
    compose.yaml compose.dev.yaml compose.offline-test.yaml
    caddy/Caddyfile  env/*.env.example  dev/up.ps1  scripts/(bundle, load-models, backup)
  tools/                   # ai_bench(실측), latency_report, golden_set
  docs/
```

### 3.7 설정과 관측성

- **PRD 고정값은 설정이 아니라 코드 상수**(`app/core/constants.py`, 단위 테스트로 고정): 본질문 10, 유형 2/2/2/4, 60·45초, 10초 경고, 꼬리 1/4, 5초, 10MB, 기본 가중치. 설정으로 열어 두면 PRD 수치가 몰래 바뀐다.
- **설정값**(pydantic-settings, `.env`): 동시 응시 상한(기본 2), 보관 기간(30/365일), 짧은 답변 기준 [OQ-09 권장안], 모델 이름, 각종 인프라 타임아웃, AI 엔드포인트(허용목록 검사). 비밀(DB 암호, 세션 키, runner 토큰)은 권한 600 파일 또는 compose secrets.
- **로그**: 모든 프로세스가 stdout JSON 한 줄 로그. 필드 `ts, level, service, event, request_id, attempt_id(UUID), job_id, lane, latency_ms, model_digest, error_code`. Caddy가 `X-Request-ID`를 만들고 api→ai-gw까지 전달.
- **금지**: 답변·자막 텍스트, 이력서 본문, 프롬프트·LLM 출력 본문, 이름·이메일·전화번호. 키 거부 목록 + 정규식 마스킹(이메일, `010-\d{4}-\d{4}`) 필터를 공통 로거에 둔다. ai-gw는 프롬프트 길이·토큰 수만 기록한다.
- **누출 검증**: E2E 시험 데이터에 카나리 문자열(예: 이력서에 `CANARY-7731`, 답변 음성에 고유 단어)을 넣고, 시험 후 전체 로그에서 검색해 0건이어야 통과.
- **지연 측정**: `rt.followup`, `stt.segment`, `tts.first_audio`, `eval.application` 이벤트의 시간 필드를 `tools/latency_report.py`가 집계해 8.2 목표 대비 실측표를 만든다. Prometheus·Grafana는 원문 외이고 MVP에서 제외.
- **감사 로그**(SEC-08)는 앱 로그가 아니라 DB 테이블이다(B).
- 로그 보관: Docker `local` 드라이버, 10MB×5 순환.

### 3.8 장애 시 대체 경로 (8.3)

| 장애 | 면접 전 | 면접 중 | 제출 후 |
|---|---|---|---|
| LLM(ollama·ai-gw) | 시작 차단(INT-14) | 꼬리질문 생략, 본질문은 사전 구성이라 계속 | 워커가 작업을 집지 않음, 시도 횟수 소모 없음, 복구 후 자동 진행 [OQ-23 권장안] (AC-18) |
| 질문 생성 실패 | 1회 재시도 → 질문은행(QST-07) | — | — |
| STT | 시작 차단 | 구간 실패는 확정 시 1회 재시도, 그래도 비면 '답변 없음' 후 진행(INT-09) | — |
| TTS | 경고 후 진행 [OQ-08 권장안] | 텍스트만 표시(INT-04) | — |
| WebRTC 끊김 | 시작 차단(INT-17) | INTERRUPTED → 재연결 시 끊긴 문항부터 [OQ-21 권장안]. 재연결 실패는 텍스트 답변(INT-18, P1) | — |
| runner·샌드박스 미검증 | — | [실행] 비활성 안내, 코드 저장은 계속 | `grade_code`가 대기, 인프라 장애로 0점 처리하지 않음 |
| worker 중지 | — | 답변 저장은 rtc가 하므로 영향 없음 | PENDING 유지, 재기동 시 임대 만료 작업 회수 (AC-18) |
| postgres | 전체 중단 | rtc가 "일시 장애" 표시 후 연결 종료 → INTERRUPTED | 복구 후 재개 |
| 호스트 재부팅 | — | 하트비트 만료로 INTERRUPTED, 슬롯 반환 | 임대 만료 작업 회수 |

## 4. 다른 에이전트에게 필요한 인터페이스·계약 (초안)

### 4.1 B (백엔드·DB)

- **작업 테이블 계약**: `jobs(id, kind, lane, payload jsonb, dedupe_key UNIQUE NULL, status ∈ {queued, running, done, dead}, priority, run_after, attempts, max_attempts, lease_owner, lease_until, run_no, requires text[], last_error_code, created_at, updated_at)`.
  - `requires`: `{'llm'}`·`{'runner'}`. 워커는 해당 인프라가 정상일 때만 집는다(OQ-23).
  - `run_no`: 집을 때마다 1 증가하는 펜싱 토큰. 결과 반영은 `WHERE job_id=? AND run_no=?` 조건부(R-14).
  - 임대 2분 + 하트비트 연장. 평가는 시도당 하드 기한 10분(EVL-06), `max_attempts=2`. `dead`로 가는 트랜잭션에서 `eval_status=ON_HOLD`로 바꾼다. 재평가(ADM-06)는 새 작업 + `ON_HOLD→PENDING`.
  - 깨우기는 `LISTEN/NOTIFY`, 2초 폴링을 보조로 둔다.
- **상태 전이 소유**: 전이 함수는 `app/applications/states.py` 한 곳에 두고 api·rtc·worker가 공유한다. 프로세스별로 전이를 따로 구현하지 않는다.
- **rtc가 쓰는 칼럼**: `attempts.current_seq`, `answer_deadline_at`(서버 UTC), `last_heartbeat_at`(10초 주기), 답변 행(텍스트, 음성 경로, `input_mode ∈ {voice, text}`, `no_answer`, 재출제 횟수).
- 모든 시각은 서버 UTC `timestamptz`. 클라이언트 시각을 저장 기준으로 쓰지 않는다.

### 4.2 C (온프레미스 AI·음성)

```
POST ai-gw:8200/v1/chat
  { lane: "rt"|"interactive"|"batch", task: "followup"|"resume_struct"|"question_gen"|"judge"|"summary"|"feedback"|"code_review",
    deadline_ms?: int (rt 필수), messages: [...], json_schema?: {...}, max_tokens: int, temperature: 0, seed?: int,
    prompt_id: "judge@v3", request_id: str }
  200 { content, parsed|null, model, model_digest, runtime: "ollama@0.40.7", timings: {queue_ms, prefill_ms, gen_ms}, usage }
  409 { code: "PREEMPTED" }          # 호출자는 시도 횟수 소모 없이 다시 넣는다
  504 { code: "DEADLINE_EXCEEDED" }  # rt 호출자는 꼬리질문 생략
  503 { code: "LLM_UNAVAILABLE" }
POST ai-gw:8200/v1/embed   # CPU 경로 고정
GET  ai-gw:8200/v1/health  → { llm: { ok, loaded, model, digest } }

POST speech:8300/v1/stt   Content-Type: audio/L16; rate=16000; channels=1  (≤30초)  ?lang=ko
  → { text, segments: [{start, end, text}], rtf }
POST speech:8300/v1/tts   { text, voice, sample_rate: 24000 } → audio/wav
GET  speech:8300/v1/health
```

- 지연 예산(3.3절): STT 구간 결과 ≤1.0초(10초 이하 구간 기준), RT LLM ≤3.0초, 꼬리질문 TTS 첫 오디오 ≤0.8초. 넘으면 C가 모델·구간 길이로 조정하고 A는 예산표를 갱신한다.
- 사고 모드가 있는 모델이면 RT 요청에서 끈다(`think: false`). `num_ctx`는 레인과 무관하게 하나로 고정.
- C의 모델 선정(X-11)은 위 VRAM 표에 들어가야 한다. GPU를 쓰는 STT·임베딩은 A·C 합의 대상.

### 4.3 D (프런트엔드·UX)

- 시그널링: `POST /rtc/v1/sessions` `{ application_id, purpose: "precheck"|"interview", sdp }` → `{ session_id, sdp }`. 같은 오리진 쿠키 세션으로 인증(rtc가 api와 같은 세션 테이블 검증). 브라우저는 ICE 수집 완료 후 offer를 보낸다(서버 non-trickle). 트랜시버: 오디오 `sendrecv`(위: 마이크, 아래: TTS), **비디오 트랙은 보내지 않는다**(INT-08, 로컬 미리보기만).
- DataChannel `ctl`(순서 보장·신뢰, JSON, `v:1`):
  - 서버→브라우저: `hello{server_now, resume_from}`, `question{seq, main_idx 1..10, followup, text, tts, max_sec}`, `tts_end{seq}`, `mic_open{seq, deadline_at}`, `warn{seq, remaining_sec:10}`, `caption{seq, part, text}`, `answer_final{seq, text, no_answer, reason: "button"|"timeout"}`, `next{to: "coding"|"done"}`, `error{code}`
  - 브라우저→서버: `answer_complete{seq}`, `tts_playback_ended{seq}`(참고용), `ping{client_now}` → `pong{server_now}`
- 남은 시간 표시 = `deadline_at − (client_now + offset)`. offset은 `hello`·`pong`으로 계산. 3-2-1 카운트다운 없음(INT-06).
- 면접 시작 응답: `200 {attempt_id}` / `409 {code:"CAPACITY_FULL", message:"이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요."}` / `503 {code:"AI_UNAVAILABLE"}` / `409 {code:"PRECHECK_REQUIRED"}` / `409 {code:"PREP_IN_PROGRESS"}`(준비 작업 진행 중, 폴링).
- 재진입: `GET /api/candidate/applications/{id}/interview/state` → 새 PeerConnection.

### 4.4 E (보안·품질)

- 신뢰 구역: Z0 브라우저 / Z1 edge(caddy, rtc UDP) / Z2 앱(api, rtc, workers, ai-gw) / Z3 AI 런타임(ollama, speech, egress 없음) / Z4 실행(runner + 샌드박스) / Z5 데이터(postgres, 볼륨).
- runner API (exec 네트워크 전용, `X-Runner-Token` 헤더):
  - `POST /v1/run {code, cases(공개 예시만), limits{wall_ms, mem_mb, cpus}}` → `{status: ok|error|timeout|oom, cases: [{stdout ≤64KB, stderr ≤16KB, exit_code, ok}]}`
  - `POST /v1/grade {code, tests: [{id, input, expected, hidden}]}` → `{passed, total, results: [{id, passed, status}]}` — 숨김 테스트의 입력·기대값·출력은 응답에 넣지 않는다.
  - `GET /v1/health` → `{sandbox_verified, runtime: "runsc"|"runc"}`. `false`면 api가 [실행]을 막는다.
- rtc는 host 네트워크라 호스트 방화벽(UDP 사내 서브넷만, egress 차단)이 유일한 경계다. E가 규칙을 정한다.
- PDF 파싱은 worker-ai 자식 프로세스(시간·메모리 제한)에서만 한다. api는 크기·매직바이트만 본다.

### 4.5 F (통합·릴리스)

- compose 프로필 3개(`dev-infra`, `full`, `offline-test`), 모든 서비스 `GET /healthz`(생존)·`GET /readyz`(의존성 포함). api `GET /api/system/ai-health`가 ai-gw·speech를 모아 INT-14에 쓴다.
- 반입 묶음: 이미지 tar, `models/` + SHA-256 목록, 프런트 `dist`는 caddy 이미지에 포함. 버전 고정: Ollama 0.40.7, 모델 digest.
- ADR 후보: A1 프로세스 경계(M2), A2 aiortc + 운영 host 네트워크, A3 ai-gw와 선점 스케줄링, A4 자체 DB 작업 큐, A5 runner·gVisor, A6 Caddy 사내 CA, A7 TTS의 WebRTC 하향 트랙 송출.

## 5. PRD 요구사항 대응

| ID | 아키텍처 대응 |
|---|---|
| USR-01~07, SEC-03·05 | api 단일 인증 계층, rtc도 같은 세션 검증 함수. Argon2id(B·E). HTTPS는 caddy |
| JOB-03·04·06, STATE-7.4 | `posting_autoclose` 주기 작업 + 조회 시점 판정. 설정 잠금은 면접 시작 트랜잭션 [OQ-05 권장안] |
| RES-02~04·07 | api 1차 검사, worker-ai 자식 프로세스 추출, INTERACTIVE 레인 구조화, 스냅샷 고정 |
| QST-01~03·06·07, 6.1 | `interview_prep` 작업, 임베딩 CPU, 스냅샷 동결, 실패 시 은행 |
| QST-05, 6.2 | rtc 진행기 + ai-gw RT 레인 + 기한 초과 시 생략 |
| INT-01·17 | 브라우저 장치 점검 + rtc `purpose=precheck` 실연결·짧은 STT |
| INT-04·05, AC-09 | 사전 TTS를 WebRTC 하향 트랙으로 송출, 서버 게이트로 이전 프레임 폐기 |
| INT-06, AC-11 | 마감 원본은 DB `answer_deadline_at`, rtc가 집행, 10초 경고 |
| INT-09·11, AC-13 | 답변마다 저장 트랜잭션, 재진입 시 DB에서 진행기 복원 |
| INT-13, AC-14 | 시작 트랜잭션에서 슬롯 확보, 하트비트, `slot_reaper` [OQ-15·16 권장안] |
| INT-14 | `/api/system/ai-health` = ai-gw + speech 상태 [OQ-08 권장안] |
| INT-15 | VAD 구간 STT → DataChannel `caption`, 확정 시 `answer_final` |
| INT-18 (P1) | DB 마감 기준 텍스트 답변 REST 경로 |
| COD-03~06, AC-15 | api→runner 동기 실행, `coding_autosubmit`, 일회용 컨테이너, 숨김 테스트 비노출 응답 |
| EVL-01~08, 6.4 | worker-ai, 인용 검증, JSON schema, Decimal 점수, `model_digest`·`prompt_id` 기록 |
| EVL-06, STATE-7.2, AC-18 | jobs 임대·펜싱, 인프라 게이트, `dead`→ON_HOLD, 재평가 |
| RPT-01 | 제출 후 서버 작업이라 브라우저 이탈 무관 |
| RPT-03, SEC-04, AC-19 | 지원자 응답 전용 스키마(api), 별도 테이블 |
| SEC-06, AC-22 | ai-gw 허용목록, internal 네트워크, 오프라인 묶음, `offline-test` 프로필 |
| SEC-08, NFR-8.4 | 공통 로거 마스킹 + 카나리 검사, 감사 테이블 |
| NFR-8.1 | LLM 1개 GPU 상주, STT·TTS·임베딩 CPU 기본 |
| NFR-8.2 | 3.3절 5초 예산, 사전 TTS, EVL-05 구현 순서 제안 |
| NFR-8.3 | 3.4절 상한 산정, 3.8절 대체 경로 |
| NFR-8.5 | `audio` 볼륨 30일, 카메라는 서버로 보내지 않음, `retention` 작업 [OQ-11 권장안] |

PRD와의 긴장(바꾸지 않고 기록만):

- **8.2 리포트 3분(P0) ↔ EVL-05 선평가(P1)**: 선평가 없이 3분이 경계선 [추정]. 대안: EVL-05를 P0 일정 안에서 함께 구현(우선순위 표기는 그대로).
- **8.1 "WebRTC로 직접 주고받는다" ↔ INT-05 "브라우저 음성을 서버로"**: 하향(TTS) 경로가 WebRTC여야 하는지 명시가 없다. A는 하향도 WebRTC로 해석했다. 대안: 사전 TTS 파일을 HTTPS로 받아 브라우저가 재생하고 서버는 `tts_playback_ended` + 최소 재생 시간으로 게이트를 연다. F가 OQ로 올릴지 판단.
- **6.2 "5초 안에 못 정하면 건너뜀"과 공정성**: 부하 때문에 생략되면 지원자마다 꼬리질문 수가 달라진다. 점수는 문항 평균이라 감점은 없다(6.2). 리포트에 "생략 사유: 시간 초과" 기록을 권장.

## 6. 위험과 검증 방법

| ID | 위험 | 등급 | 완화 | 검증 (통과 기준) |
|---|---|---|---|---|
| A-R1 | aioice 포트 범위 미지원 → 컨테이너 브리지망에서 연결 불가, 운영은 host 네트워크·방화벽에 의존 | H | 운영 host 네트워크 + 사내 서브넷 UDP 허용. 대안: aioice 바인딩 패치 또는 coturn 경유, 최후에 LiveKit | **S-1**: Windows 네이티브와 Linux host 네트워크 양쪽에서 Chrome↔rtc 2세션 30분 연속, 끊김 0, 재연결 성공 |
| A-R2 | Ollama가 다른 모델·다른 `num_ctx` 요청 때문에 LLM을 내리고 다시 올림 → 실시간 지연 수 초 | H | 모델 1개 상주, `num_ctx` 고정, 임베딩 CPU 강제 | **S-3**: 임베딩·평가·RT 요청을 섞어 1시간 실행하는 동안 `/api/ps` 재적재 0회 |
| A-R3 | 연결 종료로 생성이 실제로 멈추지 않으면 선점이 무의미 | H | 실패 시 `NUM_PARALLEL=2`(RT 전용 슬롯) 또는 llama-server로 전환 | **S-3**: BATCH 생성 중 RT 도착 → GPU가 RT 처리를 시작하기까지 ≤300ms [목표] |
| A-R4 | RT 꼬리질문 판단이 3초를 넘음 | M | 짧은 프롬프트·고정 접두부 캐시·출력 80토큰 제한, 넘으면 생략 | **S-4**: 단독 p95 ≤3.0초, BATCH 동시 p95 ≤3.3초 |
| A-R5 | CPU 경합(STT 2세션 + Opus + 샌드박스 채점)으로 자막 2초 초과 | H | speech 스레드 우선, 샌드박스 `cpus=1`·낮은 cpu_shares, 상한 조정 | **S-5**: 2세션 + 채점 동시 자막 p95 ≤2초 |
| A-R6 | 리포트 3분 미달 | M | EVL-05 선평가, 문항 단위 작업 | **S-6**: 14답변 기준 제출→DONE p95 ≤3분, 최악 ≤10분 |
| A-R7 | 개발(Windows 네이티브 rtc, runc)과 운영(host 네트워크, runsc) 차이로 운영에서만 나는 결함 | M | `offline-test` 프로필을 Linux(또는 WSL2 Docker)에서 정기 실행, 차이표 유지 | **S-7**: 외부 egress 실패 확인 후 지원→리포트 E2E 통과(AC-22) |
| A-R8 | ai-gw가 단일 장애점 | M | 무상태, 재기동 수 초 [추정], 호출자는 RT 생략·배치 재시도로 흡수 | **S-9**: ai-gw 강제 종료 중 면접 지속, 평가는 복구 후 완료 |
| A-R9 | 운영 Linux에서 gVisor 미설치·비호환 | M | `sandbox_verified=false`면 [실행] 비활성(ADR-0007 취지), runc 강화 플래그로 대체할지는 E 판단 | **S-8**: runsc로 탈출 시험 스위트 통과 |
| A-R10 | 서버 기준 마감과 네트워크 지연 차이로 마지막 말이 잘림 | L | 유예 1.5초 [추정], 마감 후 프레임 폐기 기준을 서버 수신 시각으로 | 단위(가짜 시계) + 지연 주입 통합 |

검증 순서 제안: S-1(WebRTC) → S-3(선점·상주) → S-4·S-5(지연) → S-9·AC-18 → S-6 → S-7·S-8. S-1·S-3이 실패하면 2.6·2.7의 대안으로 바꾸고 이 문서를 갱신한다.

## 7. 이견이 예상되는 지점

| 쟁점 | A의 입장 | 예상 반대 | 절충안 |
|---|---|---|---|
| rtc를 api에서 분리 | 분리 (CPU·UDP·권한) | B·F: 프로세스가 늘어 개발 기동이 번거롭다 | 코드는 한 패키지, 진입점만 둘. 개발은 `up.ps1` 한 번 |
| 면접 진행기 위치 | rtc 안 (지연) | B: 상태 전이는 api가 가져야 한다 | 전이 함수는 공유 모듈 하나, 마감 원본은 DB. rtc는 실행자 |
| ai-gw 추가 | 필요 (우선순위·선점·허용목록) | C: Ollama 직접 호출이 단순 | 처음엔 얇은 프록시 + 레인 큐만. 선점은 S-3 결과로 켜거나 끈다 |
| TTS 하향 경로 | WebRTC 오디오 트랙 | D: HTTPS 파일 재생이 단순·디버깅 쉬움 | 기본 WebRTC, 실패 시 파일 재생 대체를 같은 프로토콜(`tts:false` + URL)로 지원 |
| 작업 큐 | 자체 jobs 테이블 | B: Procrastinate·Celery로 구현량 줄이기 | 4.1 계약(임대·펜싱·인프라 게이트)을 지키면 라이브러리도 수용 |
| 샌드박스 기반 | Docker 일회용 + 운영 gVisor | E: nsjail이 더 빠르고 소켓 위험이 없다 | runner API는 같게 두고 백엔드만 교체 가능하게. 운영 Linux에서 nsjail 비교 |
| STT 위치 | CPU 기본, GPU는 VRAM 여유 확인 후 | C: 자막 2초를 위해 GPU 필요 | S-3·S-5 실측 후 VRAM 표로 결정 |
| `NUM_PARALLEL` | 1 + 선점으로 시작 | C: 2가 RT 충돌에 강하다 | S-3에서 둘 다 측정, VRAM 여유와 RT p95로 결정 |
| 질문 구성 시점 | PREPARING 진입 시 작업 시작 | B·C: 지원 직후 미리 구성해 대기 제거 | 지원 직후 BATCH 레인으로 미리 만들고, 시작 시 공고 설정 버전이 바뀌었으면 재구성 |
| 개발 토폴로지 | 하이브리드(네이티브 + Docker) | F: 전부 Docker로 재현성 확보 | 개발은 하이브리드, 재현성은 `offline-test` 프로필로 보장 |
| 리버스 프록시 | Caddy | E·F: Nginx가 익숙 | 기능 요구(HTTPS·경로 프록시)는 같아 교체 비용 작음. 사내 CA 처리 편의로 Caddy |

F에게 open-questions 등록을 제안하는 새 질문: (1) TTS 하향 경로 해석(5장), (2) 질문 구성 시점과 준비 작업이 늦을 때 대기 상한(예: 3분 [추정] 후 은행 질문으로 대체), (3) 부하로 생략된 꼬리질문의 리포트 표시 여부.
