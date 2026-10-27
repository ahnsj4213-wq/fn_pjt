# ADR-0010: 배포 토폴로지, HTTPS, 오프라인 운영, 운영 Docker 보안 모드
- 상태: 채택(임시) — 운영 호스트 검증(S-1·SBX)과 사내 인증서 배포 방식은 운영 확인이 필요하다
- 날짜: 2026-10-27
- 작성: 에이전트 A / 통합 F
- 관련: SEC-05·06, NFR-8.1·8.4, INT-17, COD-06, AC-08·15·22, R-07·08·09·21, OQ-18·32, consolidation-round1 C1·T13·T25, ADR-0001·0007

## 맥락

- 개발은 Windows 10 + Docker Desktop(WSL2)이고, 운영은 온프레미스 Linux 단일 호스트(GPU 1장)를 가정한다.
- 이 배포는 다음 조건을 함께 만족해야 한다.
  1. 브라우저 마이크는 HTTPS 또는 localhost에서만 허용된다(8.4, R-09).
  2. aiortc(aioice)는 UDP 포트를 OS가 고르게 두고 포트 범위 옵션이 없다. 브리지망 컨테이너의 host 후보에는 브라우저가 닿지 않는다.
  3. 외부 인터넷을 끊어도 전체 흐름이 동작해야 한다(AC-22).
  4. 코딩테스트 샌드박스에는 커널 공유 이상의 격리가 필요하다(R-06, E 설계안).
- 리뷰에서 E의 "운영 Docker는 rootless 또는 userns-remap 필수"와 app의 host 네트워크 요구가 충돌했다(Critical C1).
  - userns-remap 데몬은 `--network=host` 공유와 호환되지 않는다(컨테이너별 `--userns=host`로만 해제) [문서].
  - rootless 데몬은 Engine 29.5 미만에서 host 네트워크가 RootlessKit 안에 갇힌다 [문서].

## 결정

1. **네트워크**
   - 운영에서 `app`과 `caddy`는 `network_mode: host`를 쓴다.
     - caddy는 443(80→443)을, app은 HTTP를 127.0.0.1:8000에만 바인딩한다.
     - WebRTC UDP는 app의 OS 임시 포트를 쓴다. 호스트 방화벽이 사내 서브넷에서만 허용한다.
   - worker·ai·ollama·runner·postgres는 내부 브리지망에 둔다. app이 접근할 포트는 127.0.0.1에만 publish한다.
2. **이그레스 차단**: 운영 호스트 방화벽(Docker의 DOCKER-USER 체인 포함)이 외부 인터넷 행 연결을 막는다. 앱 계층에서는 다음을 함께 쓴다.
   - AI·runner 엔드포인트 사설 주소 허용목록(기동 시 검사)
   - aiortc `iceServers=[]`
   - `HF_HUB_OFFLINE=1`
3. **HTTPS**
   - 운영은 Caddy가 종단한다. 기본은 `tls internal`로 사내 CA를 쓰고, 루트 CA를 응시·시연 PC에 설치한다. 회사 CA 인증서가 있으면 파일 마운트로 교체한다.
   - 개발은 `http://localhost:5173`(Vite 프록시, 보안 컨텍스트)이다. 다른 PC에서 접속하는 시연은 HTTPS로만 한다.
4. **운영 Docker 보안 모드**
   - 강화는 샌드박스 실행에만 적용한다. 순서: runsc(gVisor) 런타임 권장 → 불가 시 샌드박스 전용 rootless 데몬 → 그것도 불가 시 같은 강화 옵션 + 별도 전용 호스트.
   - runner는 샌드박스용 데몬·런타임만 쓴다.
   - 주 데몬에는 userns-remap을 켜지 않는다. 샌드박스 옵션의 세부는 ADR-0007을 따른다.
5. **GPU**: ollama·ai 컨테이너에 NVIDIA Container Toolkit으로 GPU를 할당한다. 샌드박스에는 `--gpus`·장치 마운트를 두지 않는다.
6. **기동 순서와 재시작**
   - 순서: postgres → `migrate`(1회성 `alembic upgrade head`) → ollama → ai(LLM 상주·STT 적재 완료 후 ready) → runner(SBX 자체 점검) → app·worker → caddy.
   - 모든 서비스는 `restart: unless-stopped`.
7. **오프라인 반입 묶음(AC-22)**
   - 이미지: `docker save` tar + SHA-256.
   - 모델: Ollama 모델 디렉터리(`qwen3.5:4b`, `qwen3-embedding:0.6b`), faster-whisper turbo·small, Supertonic-3 + SHA-256 목록.
   - 프런트: `dist/`를 caddy 이미지에 포함한다.
   - Python 의존성은 이미지 안에 넣는다. 운영 서버에서 `pip`·`npm`·`ollama pull`은 하지 않는다.
8. **오프라인 시험 프로필(`offline-test`)**
   - 모든 서비스를 `internal: true` 망에 둔다. 이 프로필에서는 app도 일반 컨테이너다.
   - 같은 망의 Playwright Chromium으로 가짜 장치 E2E를 돌린다.
   - 내부 DNS 질의를 기록하고, 외부 질의·연결 시도가 0건이어야 통과다.
   - 시험 시작 시 외부 egress가 실패하는지부터 확인한다.
9. **운영 표면 축소**
   - `env=prod`에서 OpenAPI 문서(`/api/docs`, `/api/openapi.json`)와 테스트 훅 라우트를 노출하지 않는다(T25, `app/main.py` 구현됨).
   - 설정 검증에 실패하면 기동을 거부한다(Secure 쿠키, runner 토큰, AI 엔드포인트 허용목록, `app/core/config.py`).
   - 비밀은 compose secrets 파일 마운트로 전달한다.
10. **출시 조건**(모두 운영 호스트에서 실행)
    - (a) S-1 재검증: 다른 PC 브라우저 → WebRTC 연결, 게이트 이전 발화 폐기, 자막 2초.
    - (b) 운영 런타임에서 SBX-01~14 통과(ADR-0007).
    - (c) `offline-test` 프로필 AC-22 통과.
    - (d) 카나리아 로그 검사 0건(QG-07).
    - (e) OpenAPI·테스트 훅 비노출 확인.
    - (f) Ollama·postgres·ai·runner 포트가 외부 인터페이스에 열려 있지 않음.

## 검토한 대안 (장단점)

| 대안 | 장점 | 단점 |
|---|---|---|
| 주 데몬 userns-remap + app만 `userns_mode: host` | 모든 컨테이너에 기본 재매핑 | 해제한 app이 가장 노출된 프로세스라 이득이 작다. 볼륨 소유권 재매핑 부작용 |
| 주 데몬 rootless(Engine ≥ 29.5) | 데몬 탈취 피해 축소 | Engine 버전 의존(29.5 미만은 host 네트워크 불가). GPU 컨테이너·host 네트워크 조합이 미검증 |
| app을 systemd 네이티브 서비스로 | 네트워크 네임스페이스 문제 없음 | 배포 방식이 둘로 갈린다(이미지 vs 호스트 설치) |
| app 컨테이너 브리지망 + 포트 publish | 네트워크 격리 | aioice 포트 범위 지정 불가로 publish할 포트를 정할 수 없다 |
| coturn TURN 경유 | 고정 포트 범위, 외부망 응시 가능 | 구성 요소 추가, 사내망 시연에는 불필요. 외부망은 범위 밖(OQ-18) → 구성안만 문서화 |
| Nginx + mkcert/사내 CA | 표준·자료 많음 | 인증서 발급·갱신 수동 |

## 근거 (실측·문서·재현 가능한 위험)

- **aiortc 포트**: aioice `Connection`에는 포트 범위 인자가 없고 포트 0으로 바인딩한다(소스 확인, A 설계안 2.6).
- **ICE 서버**: 기본 ICE 설정은 Google STUN을 써서 외부 통신이 생긴다. 첫 프레임 10.2초, `iceServers=[]`면 0.17초 [실측 `results-rtc-loopback.txt`]. AC-22 위반 경로라 빈 목록을 강제한다.
- **userns-remap**: "Sharing PID or NET namespaces with the host (`--pid=host` or `--network=host`)"가 비호환 항목이다 [문서: Docker userns-remap known limitations].
- **rootless**: Engine 29.5 미만에서 `--net=host`는 RootlessKit 안에 격리되어 실제 호스트에서 닿지 않는다 [문서: Docker rootless troubleshooting].
- **샌드박스**: 강화 옵션 컨테이너가 12/12 차단, 기동 약 0.45초 [실측 `results-sandbox-escape.txt`]. 다만 Docker Desktop WSL2·runc 결과다. 운영 Linux·runsc 결과가 아니다(R-07) → 출시 조건 (b).
- **마이크 보안 컨텍스트**: 브라우저는 HTTPS 또는 localhost에서만 getUserMedia를 허용한다(8.4) [문서]. 사내 CA 루트를 설치하지 않은 PC에서는 점검 1단계가 막힌다(D 설계안 FR-11).
- **모델 콜드 로드**: 약 40초 [실측] → ai가 ready가 된 뒤에 app이 시작 게이트(INT-14)를 연다.

## 결과와 영향 (코드·스키마·운영)

- 작성할 배포 파일(예정): `deploy/docker-compose.prod.yml`, `deploy/docker-compose.offline-test.yml`, `deploy/caddy/Caddyfile`, `deploy/env/*.env.example`, 반입·적재 스크립트. 개발 의존성은 기존 `deploy/docker-compose.dev.yml`(postgres 127.0.0.1:55432)을 쓴다.
- Caddy 설정:
  - SPA 정적 파일, `/api/*` → 127.0.0.1:8000
  - 요청 본문 상한(이력서 10 MiB + 여유)
  - 보안 헤더(CSP `default-src 'self'`, `Permissions-Policy: microphone=(self), camera=(self)` 등은 ADR-0012·E 설계안 3.3)
- app 설정: `iceServers=[]`를 코드 기본값으로 고정한다. 외부망 TURN을 도입하면 사설 주소 TURN만 허용하고 ADR을 갱신한다.
- 운영 방화벽 규칙(사내 서브넷 UDP 허용, 외부 egress 차단)은 운영 체크리스트로 관리한다. 규칙 내용은 운영 담당 확인이 필요하다.
- 로그: Docker `local` 드라이버 순환(10 MB × 5 [추정 적정값])을 쓰고, 샌드박스 컨테이너는 `--log-driver none`(ADR-0007).
- 백업: postgres 일일 덤프를 backup 볼륨에 남긴다. 보관 기간 정책(8.5, OQ-11)에 맞춰 순환한다.

## 남은 위험과 재검토 조건

- 운영 호스트에서 다른 PC로 한 S-1이 실패하면 출시하지 않는다. 대안은 다음 둘이다.
  - coturn을 사내 TURN으로 두고 고정 포트 범위로 통과
  - LiveKit 자가 호스팅 재검토(ADR-0001)
- runsc를 설치할 수 없는 운영 서버
  - 샌드박스 전용 rootless 데몬(Engine 버전 고정)으로 대체하고 SBX를 다시 통과해야 한다.
  - 그것도 불가하면 코딩테스트 실행을 비활성으로 두고, 코딩 켠 공고의 면접 시작을 막는다(T13, OQ-32).
- `internal: true` 망에서는 포트 publish가 되지 않는 것으로 알려져 있어 운영은 일반 브리지 + 호스트 방화벽으로 이그레스를 막는다 [확인 필요]. 운영 compose 작성 시 실제 동작을 확인한다.
- 사내 CA 루트 배포가 어려운 응시 환경이면 회사 공인 인증서 사용을 운영과 협의한다. 외부망 응시 자체는 범위 밖이다(OQ-18).
- Docker Engine·Caddy·Ollama 버전을 올리면 출시 조건 (a)~(f)를 다시 실행한다.
