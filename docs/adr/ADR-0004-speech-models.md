# ADR-0004: STT·TTS·임베딩 모델 선정과 배치 (X-11 임시)
- 상태: 채택(임시) — X-11은 PRD 미결정. 팀 확인 OQ-03, STT GPU 배치 해석 OQ-30, TTS 모델 라이선스 OQ-29(법률)
- 날짜: 2026-10-27
- 작성: 에이전트 C / 통합 F
- 관련: 8.1, 8.2, 8.3, INT-04·09·14·15·17, AC-09·10·22, X-11, R-03·R-04, consolidation-round1 T5·T6·T7·T20

## 맥락

- 8.1: STT·TTS·임베딩은 CPU에서 돌 수 있는 모델을 고르고, 모델은 6GB 장비 실측 후 TRD에서 정한다.
- 8.2: 자막은 말이 끊긴 뒤 2초 안, 본질문 음성은 1초 안(사전 합성), 답변 종료→다음 질문 5초 안.
- 장비: GTX 1660 SUPER 6GB(Tensor Core 없음, 데스크톱 454 MiB 상주), Ryzen 5 PRO 4650G 6코어. LLM `qwen3.5:4b`가 GPU 3,209 MiB(@8192)를 쓴다.

## 결정

| 역할 | 결정 | 대체 |
|---|---|---|
| STT | faster-whisper `large-v3-turbo` **GPU int8_float16**, `language=ko`, `beam_size=1`, `condition_on_previous_text=False`, 외부 VAD로 자른 구간 단위 인식 | CPU `small` int8(자막 지연 저하 모드), 그다음 GPU `small` |
| VAD | 에너지 VAD(`energy-v1`: RMS 0.01, 무음 0.5초에서 구간 닫기, 최대 15초) — 자막 분할 전용 | Silero VAD(실제 녹음 S-16에서 비교) |
| TTS | **Supertonic-3**(`supertonic` 1.3.1, ONNX, CPU), 음성 스타일 1종 고정, 48kHz로 리샘플해 WebRTC 하향 트랙으로 송출 | 개발 장비 전용 Windows SAPI `Heami`. 엔진은 어댑터로 교체 가능 |
| TTS 시점 | 본질문 10개는 PREPARING 중 사전 합성(시작 조건 아님, T20). 미완료·꼬리질문은 제시 시점 합성, 실패 시 텍스트만(INT-04) | — |
| 임베딩 | `qwen3-embedding:0.6b`(Ollama, `num_gpu=0` CPU), 코사인 0.85 초기값 | 임베딩 불가 시 정확 중복만 검사 |

- GPU 우선순위: LLM 100% GPU 적재가 STT보다 우선. `ai` 기동 순서 Ollama 워밍업 → STT → TTS. `size_vram == size`가 깨지면 STT를 GPU small → CPU small로 내린다.
- GPU 공유 정책 `gpu_policy`: `shared`(기본) / `stt_preempts_batch`. S-10 결과로 고른다.

## 검토한 대안 (장단점)

| 대안 | 실측·근거 | 판정 |
|---|---|---|
| CPU faster-whisper small int8 | 구간당 2.0~2.6초(30초 창 고정 비용), CER 0.087 → 휴지 0.5초를 더하면 자막 2초 미달 | 대체 경로로만 |
| CPU large-v3-turbo int8 | 9~11초 | 기각 |
| GPU small int8_float16 | 0.3~1.1초, "쿼리"→"퀄이" 오인식 | turbo VRAM 부족 시 대체 |
| **GPU large-v3-turbo int8_float16** | 1.1~1.4초(LLM 상주·유휴), CER 0.081, S-1 종단 자막 휴지 후 1.46~1.70초 | **채택** |
| sherpa-onnx 한국어 스트리밍, SenseVoice | 미측정. 가중치·학습 데이터 라이선스 확인 필요 | 보류 |
| **Supertonic-3** | RTF 0.25~0.32, Whisper 되읽기 CER 0.000(3/3), 코드 MIT·모델 OpenRAIL-M | **채택**(법률 확인 조건) |
| MeloTTS-KR(MIT) | 미측정, Windows 의존성(mecab-ko 등) 설치 위험 | 예비 후보 |
| Piper·Kokoro | 공식 한국어 음성 없음 | 기각 |
| XTTS-v2, MMS-TTS-kor | 비상업 라이선스 | 기각 |
| TTS를 HTTP 파일로 전달 | 서버가 재생 종료를 몰라 마이크 게이트가 클라이언트 신호에 기댐 | 기각(T7, ADR-0009) |
| BGE-M3 임베딩 | 다운로드 필요, 이점 미확인 | 비교 후보(S-14) |

## 근거

- `prototypes/results-stt-cpu.txt`, `results-stt-gpu.txt`, `results-tts-supertonic.txt`, `README.md` S-1(브라우저↔aiortc), S-3(임베딩 `num_gpu=0`이 LLM과 공존).
- turbo는 CPU에서도 동작하므로(9~11초) 8.1 "CPU에서 돌 수 있는 모델" 조건은 충족한다고 해석하되, 배치를 GPU로 하는 것이 PRD 의도와 맞는지는 팀 확인(OQ-30).

## 결과와 영향

- `ai` 프로세스에 STT(GPU)·TTS·임베딩 프록시. 운영 compose에서 `ai`에 GPU 할당. Windows 개발은 cuBLAS·cuDNN 휠 경로를 PATH에 넣는다(ctranslate2는 `os.add_dll_directory`만으로 못 찾음).
- `app`은 구간 PCM을 `ai /v1/stt`로 보낸다(TRD 05 §4). 자막·확정 규칙은 TRD 06 §6.
- 반입 묶음: 모델 파일·SHA-256·Ollama digest, `HF_HUB_OFFLINE=1`. manifest(TRD 05 §11)에 STT·TTS 엔진·모델·설정 기록.
- TTS 실패는 텍스트 진행(INT-04), STT 불가는 시작 차단(INT-14, OQ-08 임시).

## 남은 위험과 재검토 조건

- **S-10 미측정**: LLM 생성 중 GPU STT 지연과 turbo VRAM. STT p95 > 1.5초 또는 여유 < 300 MiB면 `stt_preempts_batch` → GPU small 순으로 바꾼다.
- 실측 음성은 Windows SAPI 합성음 4문장이다. 실제 마이크·잡음·억양 정확도는 S-16(팀 녹음 CER ≤ 0.15 제안).
- Supertonic-3 모델 OpenRAIL-M(용도 제한 조항 승계)과 제작사 유지보수 상태(미확인 주장)는 법률·팀 확인(OQ-29). 불가 판정 시 MeloTTS를 측정해 교체한다. `supertonic` 패키지의 실행 중 다운로드 여부는 S-15로 확인.
- 꼬리질문 TTS 1.5~2.2초가 5초 예산의 최대 항목이다. S-12에서 `total_steps` 축소의 품질을 재고, 넘치면 텍스트 먼저(OQ-22).
