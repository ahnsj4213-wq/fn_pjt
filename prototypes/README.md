# 기술 위험 프로토타입(스파이크) 결과

Phase 1에서 TRD를 확정하기 전에 이 장비(GTX 1660 SUPER 6GB, Ryzen 5 PRO 4650G, RAM 64GB, Windows 10)에서 직접 실행해 측정했다. 원시 출력은 같은 폴더의 `results-*.txt`에 있다. 측정일 2026-10-26.

| 스파이크 | 스크립트 | 결론 |
|---|---|---|
| LLM 지연·JSON·VRAM | `llm_bench.py` | `qwen3.5:4b`(Q4_K_M)가 GPU에 전부 적재(Ollama 보고 2,995 MiB, 장비 전체 4,172 MiB). 생성 약 53 tok/s. 꼬리질문 판단 웜 0.56~0.57초, 문항 평가(약 500토큰) 6~11초. 콜드 로드 약 40초 → 서버 기동 시 미리 적재(keep_alive) 필요. JSON 스키마 강제 출력 6/6 유효 |
| 인용 정규화 | `quote_norm_check.py` | 모델이 "APM 으로", "API 의"처럼 영문·한글 사이에 공백을 넣어 **원문 그대로 비교 시 6/20만 일치**. NFKC + 공백·일부 문장부호 제거 후 비교하면 **20/20 일치** → 인용 검증은 정규화 비교로 한다 (ADR-0008) |
| STT CPU | `stt_bench.py` | faster-whisper int8, 6스레드. `small`: 구간 길이와 무관하게 **2.0~2.6초**(30초 창 고정 비용), CER 0.087. `large-v3-turbo`: **9~11초**, CER 0.081 → CPU에서는 8.2 "말 끊긴 뒤 2초 내 자막" 미달 (turbo 측정 일부는 TTS 측정과 CPU를 나눠 써서 과대 가능) |
| STT GPU | `stt_bench.py --device cuda` | LLM이 GPU에 상주한 상태에서 int8_float16. `small` 0.3~1.1초, `large-v3-turbo` **1.1~1.4초**, 인식 품질은 turbo가 더 좋음("쿼리"를 정확히 인식). cuBLAS·cuDNN pip 휠 필요 |
| TTS | `tts_bench.py` | Supertonic-3(`supertonic` 1.3.1, ONNX CPU, 코드 MIT·모델 OpenRAIL-M) 한국어 합성 RTF **0.25~0.32**(5.2초 음성 1.5초). Whisper로 되읽은 CER **0.000 (3/3)**. 본질문 10개 사전 생성 추정 15~25초. Windows SAPI `Microsoft Heami`는 13.5초 음성을 0.12초에 합성(Windows 전용, 개발 대체 경로) |
| WebRTC(aiortc) | `rtc_loopback.py` | aiortc 1.15.0이 Windows에서 Opus 송수신·데이터채널·16kHz mono 리샘플 동작. **기본 설정은 Google 공개 STUN을 사용**해 첫 프레임까지 10.2초 + 외부 통신 발생 → `RTCConfiguration(iceServers=[])`로 0.17초. 서버는 반드시 ICE 서버를 명시 (AC-22) |
| Ollama 상주·선점 (S-3) | `ollama_sched_check.py` | (1) `num_gpu=0` 임베딩(`qwen3-embedding:0.6b`, 1024차원, 2.15초)은 LLM을 내리지 않고 공존. (2) 요청의 `num_ctx`가 바뀌면 **약 5.1초 재적재**(4096→8192에서 VRAM 2,995→3,209 MiB) → `num_ctx`는 전역 고정. (3) 긴 스트리밍 생성을 연결 종료로 끊으면 직후 짧은 요청이 **0.49초**에 처리(취소가 실제로 동작). (4) 끊지 않으면 600토큰 생성 뒤에서 짧은 요청이 **11.06초** 대기 → 꼬리질문(5초) 보호를 위해 배치 요청 선점이 필요 |
| 코드 샌드박스 | `sandbox/escape_tests.py` | Docker Desktop(WSL2)·runc에서 강화 옵션 컨테이너가 **12/12 차단**: 네트워크(공인 IP·호스트 게이트웨이), 읽기 전용 루트, tmp noexec, 비루트 uid 10001·CapEff 0, 환경변수 비밀 없음, 포크 폭탄(pids 64), 메모리 폭탄(256MB OOM), 무한 루프(5초 kill), tmpfs 16MB, 출력 64KB 절단. 컨테이너 1회 기동 약 0.45초. gVisor(runsc)는 이 장비에 없음 |

| 브라우저↔aiortc (S-1) | `rtc_browser/server.py`, `index.html` | 내장 브라우저(Chromium)↔aiortc 연결 성공. 서버→브라우저 TTS 하향 트랙 5.2초 송출 후 `tts_end`(5.5초), 300ms 뒤 `mic_open`. **게이트 이전 발화 5.46초를 서버가 폐기**(AC-09 서버 측 동작). 에너지 VAD(0.5초 휴지) → GPU turbo STT → 데이터채널 자막이 **휴지 후 1.46~1.70초**에 도착(8.2 목표 2초 이내). 주의: ctranslate2는 cuBLAS·cuDNN을 PATH로 찾으므로 `os.add_dll_directory`만으로는 실패한다. 첫 자막이 어긋난 것은 시험 페이지가 같은 음성을 두 번 겹쳐 재생한 시험 설계 탓이다. 30분 장시간 연결·재연결·실제 마이크는 미검증 |

## 측정의 한계

- 음성 샘플은 사람 발화가 아니라 Windows SAPI 합성음 4문장(총 32.5초)이다. 실제 마이크 잡음·억양·말 더듬기 조건의 정확도는 측정하지 않았다.
- LLM 평가는 한 개 답변으로만 측정했다. 평가 품질(골든셋 Kappa)은 측정하지 않았다.
- 동시 응시 부하(여러 STT·LLM 요청 경합)는 측정하지 않았다. Phase 6에서 통합 상태로 측정한다.
- 샌드박스는 Docker Desktop의 WSL2 VM 커널 위 결과다. 운영 Linux 호스트에서 다시 실행해야 한다.

## 이 스파이크로 내려받은 것

| 대상 | 출처 | 크기(대략) | 라이선스 |
|---|---|---|---|
| `Systran/faster-whisper-small` | Hugging Face | 0.5 GB | MIT |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | Hugging Face | 1.6 GB | MIT |
| `Supertone/supertonic-3` | Hugging Face | 0.3 GB 내외 | OpenRAIL-M (모델), MIT (코드) |
| `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` | PyPI | 약 1 GB | NVIDIA 재배포 라이선스 |
| `postgres:16-alpine`, `python:3.12-slim` | Docker Hub | 이미지 | PostgreSQL / PSF |
