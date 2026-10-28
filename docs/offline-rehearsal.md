# AC-22 오프라인 시험 절차

AC-22: 외부 인터넷이 끊긴 상태에서 지원부터 리포트·결과까지 전체 흐름이 동작해야 한다(SEC-06, TRD 07 §12, TRD 08 §5).
세 단계로 확인한다. 1·2는 자동, 3은 시연 장비에서 사람이 한다.

## 1. 정적 검사 (자동, 매 커밋)

| 검사 | 위치 |
|---|---|
| AI·Ollama·runner 주소는 루프백·사설 대역·compose 서비스 이름만 (기동 시 거부) | `backend/app/core/config.py`, `tests/test_offline_static.py` |
| 클라우드 AI SDK(openai, anthropic, google-genai 등) 의존성 없음 | `tests/test_offline_static.py` |
| 소스(backend·runner·frontend)에 외부 호스트 URL 없음 | `tests/test_offline_static.py` |
| 빌드 결과물(`frontend/dist`)에 외부 URL 없음 | `npm run build && npm run check:offline` |
| WebRTC는 서버·브라우저 모두 `iceServers=[]` (공용 STUN 금지) | `tests/test_offline_static.py`, `e2e/offline-checks.spec.ts`(ICE 수집 1초 미만·host 후보만) |
| 모델 라이브러리 오프라인(`HF_HUB_OFFLINE=1`, 텔레메트리 끔). 모델 자동 다운로드는 기본 꺼짐 | `app/aisvc/offline.py`, `AIHIRE_MODEL_AUTO_DOWNLOAD` |

## 2. 외부망 차단 E2E (자동, Linux)

```bash
tools/offline/run.sh        # PostgreSQL이 /tmp 유닉스 소켓(포트 55432)으로 떠 있어야 한다. iproute2(ip) 필요.
```

- 새 네트워크 네임스페이스(루프백 + 기본 경로 없는 사설 LAN `10.77.0.10/24`) 안에서 backend·worker·Vite·Chromium을 모두 띄우고
  브라우저 E2E 전체(지원 → 장비 점검 → 음성 면접 10문항 → 코딩 실행·제출 → 평가 → 담당자 결정 → 결과 확인)를 돌린다.
- 외부로 나가는 경로 자체가 없으므로 외부 의존이 있으면 흐름이 실패한다. 동시에 시도 자체를 기록한다.
  - backend·worker: Python 감사 훅(이름 조회·연결 시도) → `frontend/e2e/.fixtures/offline/backend-egress.log`
  - 브라우저: Chromium net-log → 앱 페이지가 보낸 요청과 WebRTC UDP가 루프백·사설 주소 밖으로 나갔는지 판정
- 판정은 `frontend/e2e/.fixtures/offline/ac22-report.json` (`verdict: PASS/FAIL`).
- AI는 가짜(E2E 서버)다. 실제 모델이 오프라인으로 뜨는지는 3단계에서 본다.
- Chromium 자체의 제조사 서비스(로그인·컴포넌트 업데이트 등)는 플랫폼이 아니므로 `browser_vendor_hosts`로 기록만 한다.

2026-10-28 결과: **PASS** — backend 외부 시도 0건, 앱 요청·WebRTC 모두 사설/루프백, ICE 수집 1초 미만(host 후보만).

## 3. 시연 장비 수동 리허설 (사람, GPU 장비)

준비: 모델(Ollama `qwen3.5:4b`·`qwen3-embedding:0.6b`, faster-whisper `large-v3-turbo`, Supertonic)이 미리 받아져 있어야 한다.

1. 랜선을 뽑고 Wi-Fi를 끈다(또는 Windows 방화벽에서 아웃바운드 차단). `ping 8.8.8.8`이 실패하는지 확인.
2. `AIHIRE_MODEL_AUTO_DOWNLOAD`를 설정하지 않은 채(기본 false) `python -m app.aisvc` → `/v1/health`의 llm·stt·tts가 모두 up.
3. backend(`uvicorn app.main:app`), worker(`python -m app.jobs.runtime`), runner, 프런트(`npm run preview` 또는 dev)를 띄운다.
4. 실제 브라우저(Chrome·Edge)와 실제 마이크로 가입 → 지원 → 장비 점검 → 면접 → 코딩 → 제출, 담당자 계정으로 리포트 확인·결정,
   지원자 계정으로 결과 확인까지 진행한다.
5. 기록: 각 단계 성공 여부, 면접 중 자막 지연 체감, 오류 메시지. 실패하면 화면과 backend 로그를 남긴다.
