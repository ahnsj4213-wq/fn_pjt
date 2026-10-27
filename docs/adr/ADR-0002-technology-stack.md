# ADR-0002: 기술 스택과 DB 드라이버(asyncpg)
- 상태: 채택
- 날짜: 2026-10-27
- 작성: 에이전트 A / 통합 F
- 관련: SEC-04·05·06, NFR-8.1·8.4, AC-22, R-10, R-21, consolidation-round1 T1·T2, TRD 02

## 맥락

- 백엔드는 한 asyncio 이벤트 루프에서 세 가지를 함께 다룬다.
  - REST API
  - aiortc WebRTC(UDP·DTLS·Opus)
  - asyncio 하위 프로세스(PDF 파싱 상한 실행, 도구 호출)
- 개발 장비는 Windows 10이고 운영은 Linux다. 개발 장비에서 실제 면접 흐름(브라우저↔aiortc)을 돌려야 하므로 두 OS에서 같은 코드가 같은 루프로 동작해야 한다.
- B 설계안은 DB 드라이버로 psycopg 3 async를 제안했다.
- 프런트엔드는 운영망(외부 인터넷 차단)에서 동작해야 한다.

## 결정

| 영역 | 선택 | 버전(고정) |
|---|---|---|
| 언어·런타임 | Python 3.12, Node 24(빌드 전용) | `requires-python >=3.12,<3.13` |
| 웹 프레임워크 | FastAPI + uvicorn[standard] | 0.162.0 / 0.54.0 |
| 데이터 모델·검증 | Pydantic v2 + pydantic-settings | 2.14.1 / 2.16.1 |
| ORM·마이그레이션 | SQLAlchemy 2(async) + Alembic. 스키마 원천은 마이그레이션 SQL(CHECK·트리거 포함) | 2.0.55 / 1.20.1 |
| DB | PostgreSQL 16 | `postgres:16-alpine` |
| **DB 드라이버** | **asyncpg** | 0.32.0 |
| WebRTC | aiortc + PyAV | 1.15.0 / 17.1.0 |
| 비밀번호 | argon2-cffi(Argon2id) | 25.1.0 |
| PDF | pypdf(자식 프로세스에서 실행) | 6.21.2 |
| 내부 HTTP | httpx | 0.28.1 |
| AI 런타임(ai 프로세스 전용 extra) | faster-whisper, ctranslate2, onnxruntime, supertonic | 1.2.1 / 4.8.4 / 1.31.0 / 1.3.1 |
| LLM 서빙 | Ollama | 0.40.7 |
| 프런트엔드 | React 19 + TypeScript + Vite SPA, React Router, TanStack Query, CodeMirror 6 | TRD 02 4.2 |
| 리버스 프록시 | Caddy 2 | ADR-0010 |

- 무거운 모델 런타임은 `[ai]` extra로 분리한다. app·worker 설치물에는 들어가지 않는다.
- 버전은 `backend/pyproject.toml`에 정확히 고정한다. 프런트는 lockfile로 고정한다.

## 검토한 대안 (장단점)

| 대안 | 장점 | 단점 |
|---|---|---|
| psycopg 3 async | 동기·비동기 같은 드라이버, `LISTEN/NOTIFY` 지원(B 제안 근거) | **Windows 기본 Proactor 이벤트 루프를 지원하지 않는다**. Selector 루프로 바꾸면 Windows에서 asyncio 하위 프로세스를 쓸 수 없다. aiortc·하위 프로세스와 한 루프에서 공존 불가 |
| asyncpg | Proactor·Selector 모두 동작, 빠름, SQLAlchemy async 기본 지원, `LISTEN` 지원 | 동기 드라이버가 없어 Alembic 온라인 마이그레이션을 async 엔진으로 실행해야 한다(`migrations/env.py`) |
| Django 5 + DRF/Ninja | 인증·admin·마이그레이션 내장, admin을 운영자 UI(OQ-19)로 쓸 수 있음 | 비동기 ORM 제약, aiortc·비동기 워커와 실행 모델 이원화 |
| Litestar | FastAPI와 비슷, DTO 계층 | 자료·커뮤니티가 적어 학습 비용 |
| Next.js / Vue 3 | SSR·파일 라우팅 / 작은 번들 | Node 서버 추가, SSR 이점 없음, 빌드 시 외부 접근 기능 / 팀 숙련도 가정과 다름 |

## 근거 (실측·문서·재현 가능한 위험)

- **asyncpg**
  - 조정자가 이 장비(Windows 10, Python 3.12)에서 실측했다(consolidation T2).
    - psycopg async는 Proactor 루프에서 동작하지 않는다.
    - asyncpg + aiortc + asyncio subprocess는 한 루프에서 함께 동작한다.
  - Windows asyncio 하위 프로세스가 Proactor 루프에서만 지원된다는 것은 Python 표준 문서의 제약이다 [문서].
  - `app/core/db.py`가 이 결정을 반영해 asyncpg 엔진을 만든다. 서버 쪽 `statement_timeout` 60초, `idle_in_transaction_session_timeout` 30초, `timezone=UTC`.
- **aiortc**
  - Windows에서 Opus 송수신·데이터채널·16 kHz 리샘플이 동작한다 [실측 `results-rtc-loopback.txt`].
  - 브라우저↔서버 하향 TTS 트랙, 서버 게이트, 자막 1.46~1.70초도 확인했다 [실측 S-1].
  - 기본 설정이 Google STUN을 써서 첫 프레임 10.2초 + 외부 통신이 생겼다. `iceServers=[]`로 0.17초가 됐다 [실측] → 서버 설정에 빈 목록을 강제한다(AC-22).
- **FastAPI + Pydantic**: `response_model`과 `extra="forbid"`로 지원자 응답 화이트리스트(SEC-04)와 가입 금지 필드(USR-02, AC-01)를 스키마로 강제한다(`app/auth/router.py`).
- **오프라인**: 런타임 다운로드가 필요 없는 구성만 고른다. 모델은 사전 반입하고 `HF_HUB_OFFLINE=1`을 쓴다. 프런트 번들에는 CDN을 쓰지 않는다(R-21, AC-22).
- **라이선스**: 백엔드 의존성은 MIT·BSD·Apache다. PyAV에 동봉된 FFmpeg는 LGPL이라 동적 링크 상태를 유지하고 고지한다. Supertonic 모델 가중치(OpenRAIL-M)는 법률 확인 대상이다(OQ-29). 전체 표는 TRD 02 4절.

## 결과와 영향 (코드·스키마·운영)

- 모든 DB 접근은 SQLAlchemy async + asyncpg로 한다. 원시 SQL은 마이그레이션과 큐 집기 쿼리에만 쓴다. 담당자 모듈의 원시 SQL은 정적 검사로 금지한다(ADR-0012).
- Alembic은 async 엔진으로 실행한다. 운영 배포에서는 앱 기동 전에 1회성 `migrate` 단계로 실행한다(ADR-0010).
- 작업 큐 깨우기(`LISTEN/NOTIFY`)는 asyncpg 연결의 리스너로 구현한다. 2초 폴링을 보조로 둔다.
- 개발 장비 테스트도 실제 PostgreSQL 컨테이너(`deploy/docker-compose.dev.yml`, 127.0.0.1:55432)를 쓴다. SQLite로 대체하지 않는다.
- ai 프로세스 설치물에는 nvidia-cublas-cu12·nvidia-cudnn-cu12 휠이 들어간다. 기동 스크립트가 해당 경로를 PATH에 넣는다(ctranslate2는 PATH로만 찾는다 [실측 S-1]).

## 남은 위험과 재검토 조건

- **이벤트 루프 정책**
  - uvicorn은 Windows에서 `--reload`·`--workers` 같은 하위 프로세스 실행 모드일 때 Selector 루프 정책을 강제하는 버전이 있었다 [확인 필요, 0.54.0에서 미검증].
  - 그 경우 asyncio 하위 프로세스가 실패한다.
  - 완화: 개발에서도 app을 `--reload` 없이 실행한다. 기동 시 실행 중 루프 종류를 검사해 Windows에서 Proactor가 아니면 기동을 거부한다(fail-closed).
- **aiortc 장시간 안정성**: 30분 연결, 재연결, 실제 마이크, 다른 PC 조건은 미검증이다(R-10). 운영 호스트 S-1 재검증이 출시 조건이다(ADR-0010). 실패하면 LiveKit 자가 호스팅을 재검토한다.
- **버전 상향**: 고정 버전을 올릴 때는 다음을 다시 확인한다.
  - asyncpg + aiortc + subprocess 공존 시험
  - SBX(ADR-0007)
  - 오프라인 시험 프로필(AC-22)
- **TypeScript 7.x**: typescript-eslint 호환이 확인되면 올린다(D 설계안).
