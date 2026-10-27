# TRD 06 — 실시간 면접: WebRTC·턴 상태 기계·데이터채널 프로토콜

- 작성: 에이전트 C. 기준: `docs/design/consolidation-round1.md`(통합 결정 C3·C4·C7, T7~T12, T27), PRD v1.0 초안, F 실측 `prototypes/rtc_browser/server.py`(S-1).
- 관련: ADR-0009(이 문서의 결정 기록), ADR-0004(STT·TTS), ADR-0003(꼬리질문 RT 레인), TRD 05(AI 계약), TRD 01(프로세스 `app`·`ai`), TRD 04(B, REST 계약 — 경로는 7절 표와 일치해야 한다).
- 표기: [실측] / [추정] / [PRD] / [TRD] 는 TRD 05와 같다.

## 1. 원칙

1. 음성은 브라우저와 `app`이 WebRTC로 주고받는다: 상향 = 마이크, 하향 = 질문 TTS(X-01, 8.1, T7). 카메라 영상은 서버로 보내지 않는다(INT-08).
2. **권위는 서버 시계와 서버 상태**다. 마이크 개방·마감·확정·재출제는 `app`의 턴 상태 기계만 정한다. 클라이언트 신호로 게이트가 앞당겨지지 않는다(AC-09, SC-45).
3. 클라이언트→서버 **명령은 HTTPS**(쿠키 세션 + CSRF + Origin), 서버→클라이언트 **이벤트는 데이터채널**(T8). 데이터채널 상향은 진단용 `ping`·`client_ready`뿐이다.
4. 문항 제시 1회 = 턴 1개 = `turn_id`(`interview_turns` 행, C3). 모든 명령·이벤트·멱등 판정은 `turn_id`로 한다. "attempt"는 응시(지원 건)에만 쓴다.
5. 침묵으로 답변을 끝내지 않는다. VAD는 자막 구간 분할에만 쓴다(X-02, AC-10).

## 2. 구성

| 위치 | 담당 |
|---|---|
| 브라우저 | `getUserMedia`(오디오: echoCancellation·noiseSuppression·autoGainControl, 모노), `RTCPeerConnection({iceServers: []})`, 원격 오디오 요소 재생, 데이터채널 수신, HTTPS 명령, 클라이언트 상태 기계(9절) |
| `app`(FastAPI + aiortc, uvicorn 워커 1) | 시그널링, 지원 건당 PeerConnection 1개, 턴 상태 기계·게이트·VAD·자막 이벤트·확정, 꼬리질문 판단 요청, 슬롯 임대 갱신 |
| `ai` | 구간 STT(`/v1/stt`), 꼬리질문 TTS(`/v1/tts`), T4 판단(RT 레인) |
| `worker` | 본질문 사전 TTS(`tts_pregen`, CPU), 문항 평가(`eval_answer`) |

## 3. 시그널링

- 방식: 비-trickle. 브라우저는 ICE 수집 완료 뒤 offer를 한 번 보내고, `app`도 수집을 끝낸 answer를 돌려준다(aiortc 동작). 경로는 7절 IV-3(`purpose=PRECHECK` 점검, `purpose=INTERVIEW` 면접).
- ICE: 서버 `RTCConfiguration(iceServers=[])`, 브라우저 `iceServers: []`(사내망 host 후보). aiortc 기본값은 Google 공용 STUN이라 첫 프레임 10.2초 + 외부 통신이 생긴다 → 빈 목록으로 0.17초[실측]. 운영 기동 검증에서 ICE 서버 목록이 비었거나 사설 주소인지 확인한다(SC-61·80).
- 인증: `app`의 공통 인증·CSRF·Origin 미들웨어를 시그널링에도 적용(D-06, E #6). 지원 건 소유자가 아니면 404.
- 연결 조건: 점검 연결 = 지원 건 PREPARING(슬롯 불필요), 서버 전체 동시 점검 연결 4개 상한(C4). 면접 연결 = INTERVIEWING + 슬롯 보유. 
- 한 지원 건의 활성 PeerConnection은 1개다. 같은 지원 건의 새 offer는 기존 연결에 `session_superseded`를 보내고 닫는다. 기존 연결의 진행 중 턴(PRESENTING·ANSWERING)은 ABANDONED가 된다(재출제 횟수에 포함).
- SDP 검사: 오디오 m-line 1개(sendrecv) + 데이터채널(application) 1개만 허용. 비디오 m-line이 있으면 422 `SDP_INVALID`.

## 4. 미디어

| 방향 | 형식 | 처리 |
|---|---|---|
| 상향 마이크 | Opus 48kHz(브라우저 기본) | aiortc가 `AudioFrame`(s16 48kHz)으로 디코드 → 게이트 → PyAV `AudioResampler(s16, mono, 16000)` → VAD·STT·답변 음성 저장 |
| 하향 TTS | 48kHz mono s16, 20ms(960샘플) 프레임 | 전용 송신 트랙이 PCM을 pts 기준으로 일정 속도 송출, 클립 사이는 무음. 클립의 마지막 프레임을 보낸 순간을 서버가 기록(`tts_ended_at`) |
| 데이터채널 | 클라이언트가 생성, label `control`, ordered·reliable | 8절 |
| 영상 | 보내지 않음 | 카메라는 `<video muted>` 로컬 미리보기만. `MediaRecorder` 금지(INT-08) |

- 답변 음성 저장(INT-09): 게이트를 통과한 16kHz PCM만 Opus(OGG)로 인코딩해 확정 때 저장(`audio_key`). 보관 30일(8.5, X-07). 게이트 이전·끊김으로 버린 부분 음성은 저장하지 않는다(OQ-21).
- 본질문 TTS는 PREPARING 중 `tts_pregen` 작업이 미리 합성해 `question_tts_assets(audio_key, duration_ms, voice_model)`에 둔다. 사전 합성은 시작 조건이 아니다(T20). 미완료 문항은 제시 시점에 `ai /v1/tts`로 즉시 합성하고, 실패하면 텍스트만(INT-04).

## 5. 서버 턴 상태 기계

`interview_turns.state` = PRESENTING → ANSWERING → FINALIZING → CLOSED, 끊김 시 ABANDONED. `interviews.current_phase`는 같은 값 + DECIDING(꼬리질문 판단 중)·DONE으로 진행기를 비춘다. 진행 중 턴은 지원 건당 하나다(`ux_turn_live`).

| # | 현재 | 이벤트 | 조건 | 동작 | 다음 |
|---|---|---|---|---|---|
| S1 | (없음) | 다음 문항 제시 | INTERVIEWING, 진행 중 턴 없음, 음성 모드면 PC 연결 | 턴 INSERT(`presentation_no`), `question` 송신, TTS 트랙 송출 시작 | PRESENTING |
| S2 | PRESENTING | 트랙 마지막 프레임 송신 | — | `tts_ended_at` 기록, `tts_end{played:true}`, 0.3초 보호 타이머 | PRESENTING |
| S3 | PRESENTING | 보호 타이머 만료, 또는 TTS 불가(`tts_end{played:false}`) | — | `opened_at=now`, `deadline_at=opened_at+60초(본)/45초(꼬리)`, 게이트 열기, `answer_opened` | ANSWERING |
| S4 | ANSWERING | `warn_at = deadline_at − 10초` | — | `answer_warn` | ANSWERING |
| S5 | ANSWERING | IV-11 [답변 완료] | `turn_id`가 진행 중 턴, 수신 시각 ≤ `deadline_at + 유예` | `end_reason=BUTTON`, 0.3초 drain 뒤 게이트 닫기, 진행 중 구간 flush | FINALIZING |
| S6 | ANSWERING | `deadline_at + 유예(1.0초)` 도달 | — | `end_reason=TIMEOUT`, 게이트 닫기, flush | FINALIZING |
| S7 | FINALIZING | 남은 구간 STT 완료(구간당 1회 재시도 포함) | — | 확정 텍스트·`eval_text` 생성 → 한 트랜잭션: 답변 INSERT(`turn_id` UNIQUE, `ON CONFLICT DO NOTHING`) + 턴 CLOSED(조건부 `WHERE state='FINALIZING'`) + `eval_answer` 작업 등록 → `answer_finalized` → `processing` | CLOSED |
| S8 | PRESENTING·ANSWERING | 끊김 판정(11절) | — | 부분 음성·자막 폐기, `end_reason=DISCONNECT`, 응시 INTERRUPTED, 슬롯 120초 유예 | ABANDONED |
| S9 | FINALIZING | 끊김 판정 | — | 이미 받은 음성으로 확정을 계속한다(확정 답변은 잃지 않음) | CLOSED |
| S10 | CLOSED | 다음 단계 결정 | 본질문 턴이면 12절 꼬리질문 판단 | 꼬리질문 → S1(같은 번호), 아니면 다음 본질문 → S1, 마지막이면 `interview_completed` | — |

- 게이트: 서버 수신 시각이 `opened_at` 이전이거나 `deadline_at + 유예` 이후인 프레임은 버린다. BUTTON이면 수신 + 0.3초 drain까지만 받는다(그 이후 발화는 답변이 아님). 프런트의 `track.enabled=false`는 보조다[실측: 게이트 이전 발화 5.46초 서버 폐기].
- `deadline_at`은 유예를 뺀 값이고 화면 남은 시간의 기준이다. 유예(`answer_grace_seconds`=1.0, T9)는 네트워크 지연 보정용이며 유예 뒤 도착한 오디오·명령은 무시한다.
- 확정은 한 번만 일어난다: 버튼·만료·끊김이 경합해도 턴의 조건부 갱신과 답변의 유일 제약이 하나만 통과시킨다.
- 마지막 문항 뒤: 코딩 켠 공고면 CODING 진입(슬롯 반환, T10) + `interview_completed{next:"CODING"}`, 아니면 SUBMITTED + 평가 작업 등록(7.2) + `interview_completed{next:"SUBMITTED"}` → 서버가 PC를 닫는다.

## 6. VAD·자막·확정 텍스트·답변 없음

| 항목 | 값 | 근거 |
|---|---|---|
| VAD 기준 구현 | 20ms 프레임 RMS > 0.01이면 발화 | [실측 S-1] `energy-v1` |
| 구간 닫기 | 발화 뒤 무음 0.5초 | [실측 S-1] 자막 휴지 후 1.46~1.70초 |
| 최대 구간 | 15초 연속 발화면 마지막 2초 안의 최저 RMS 프레임에서 강제 분할 | [TRD] Whisper 30초 창 안에 유지 |
| 최소 발화 | 구간 안 발화 합계 0.25초 미만이면 STT 생략(잡음) | [TRD] |
| STT 호출 | 구간 PCM을 `ai /v1/stt`로, 턴 안에서 순서대로(동시 1) | TRD 05 §4 |
| 환각 차단(`hl-1`) | `no_speech_prob ≥ 0.6 && avg_logprob ≤ −1.0`, 압축률 > 2.4, 차단 목록("시청해 주셔서 감사합니다", "구독과 좋아요", "자막 제공" 등) → 그 구간 텍스트 비움 | [TRD] |
| 비교 후보 | Silero VAD(잡음 강건) — S-16 실제 녹음으로 비교 후 교체 여부 결정 | — |

- 자막: 구간마다 `caption_segment{segment_no, text}`. 구간 안 중간 결과(`caption_partial`)는 v1에서 보내지 않는다(GPU 부하, 예약 이름). 말하는 동안 화면은 마이크 레벨 막대 + "인식 중" 표시(D).
- 확정 텍스트: 구간 텍스트를 순서대로 공백 하나로 이어 붙인 것 = `interview_answers.transcript`(지원자가 본 확정 자막, INT-15). 큰 모델로 다시 인식하지 않는다(확정 자막 = 평가 원문, 5초 예산).
- 답변 없음: 발화 구간이 0개(NO_SPEECH) → `is_no_answer=true`, STT 호출 없음. 발화 구간은 있었는데 확정 시 모든 구간이 재시도 후에도 실패 → `is_no_answer=true, stt_failed=true`(담당자 구분용, INT-09). 일부 구간만 실패하면 인식된 구간으로 확정하고 실패 수를 지표로만 남긴다(본문 없이).
- `eval_text`(평가·인용 기준)는 확정 때 만든다(TRD 05 §8.1).

## 7. 클라이언트 명령 (HTTPS) — TRD 04(B)와 같은 ID·경로

접두사 `…` = `/api/candidate/applications/{aid}`(TRD 04 표기). 모든 변경 요청은 `X-CSRF-Token` + Origin 검사. 오류 형식은 공통 `{"error":{"code","message","request_id"}}`(ADR-0011). 아래 경로·코드는 TRD 04 3.3절과 같다. 다르면 04가 우선이고 이 표를 고친다.

| ID | 메서드·경로 | 요청 → 응답(요지) | 조건 | 오류 |
|---|---|---|---|---|
| IV-3 | POST `…/rtc/offer` | `{purpose: "PRECHECK"\|"INTERVIEW", sdp, type:"offer"}` → `{sdp, type:"answer", rtc_session_id}` | PRECHECK: PREPARING, 슬롯 불필요, 점검 연결 ≤ 4 / INTERVIEW: INTERVIEWING + 유효 슬롯(C4) | 409 `STATE_CONFLICT`, 429 `PRECHECK_BUSY`, 422 `SDP_INVALID`, 403 CSRF·Origin |
| IV-18 | POST `…/precheck/utterance` | → 202 `{window_sec: 6}`, 결과는 DC `precheck_result` | 점검 연결이 살아 있음 | 409 `PRECHECK_NOT_CONNECTED` — **TRD 04에 없음, B에 추가 요청**(재시도 3회를 서버가 받으려면 명령이 필요) |
| IV-4 | POST `…/precheck/media` | `{mic_ok, camera_ok, speaker_ok?}`(자가 보고) → 204 | PREPARING | 409 `STATE_CONFLICT` |
| IV-5 | POST `…/interview/start` | → 200 `InterviewState` | PREPARING, 점검 기록 30분 이내, AI 상태(LLM·STT), 질문 세트 READY, 슬롯 | 409 `PRECHECK_REQUIRED`·`QUESTIONS_NOT_READY`(`retry_after_sec`)·`CAPACITY_FULL`(INT-13 고정 문구)·`POSTING_CLOSED`·`STATE_CONFLICT`, 503 `AI_UNAVAILABLE`·`CODE_EXECUTION_UNAVAILABLE` |
| IV-6 | POST `…/interview/resume` | → 200 `InterviewState`. INTERRUPTED면 재개(슬롯 재확보, 유예 중이면 자기 슬롯), 이미 INTERVIEWING·CODING이면 그대로(멱등) | 공고 OPEN | 409 `CAPACITY_FULL`·`POSTING_CLOSED`·`STATE_CONFLICT` |
| IV-7 | GET `…/interview/state` | → `InterviewState` | 본인 | 404 |
| IV-11 | POST `…/interview/turns/{turn_id}/done` | [답변 완료] → 202 `{turn_id, state:"FINALIZING"}`. 같은 턴이 이미 FINALIZING·CLOSED면 200 `{turn_id, state, end_reason}`(멱등) | 진행 중 턴이 ANSWERING | 409 `STALE_TURN`. 턴이 아직 PRESENTING이면 409 `STATE_CONFLICT`(**04에 추가 요청**) |
| IV-17 | POST `…/interview/text-mode` | → 200 `InterviewState`(`mode:"TEXT"`) | 11절 T12 조건(서버 판정) | 409 `TEXT_MODE_NOT_ALLOWED`·`CAPACITY_FULL` |
| IV-13 | POST `…/interview/turns/{turn_id}/text-answer` | `{text}`(≤ 2,000자, 빈 문자열은 '답변 없음') → 202 `{turn_id, state}` | 텍스트 모드, 턴 ANSWERING, ≤ 마감 + 유예 | 409 `STALE_TURN`·`TURN_CLOSED`, 422 |
| IV-15 | POST `…/heartbeat` | → 204 | PREPARING, 텍스트 모드 INTERVIEWING, CODING(30초, C7). 음성 INTERVIEWING은 서버 런타임이 갱신 | 409 `STATE_CONFLICT` |
| IV-14 | POST `…/interview/proctor-events` | `{kind: LEAVE\|RETURN, occurred_at, away_ms?}` → 204 | INTERVIEWING·CODING(INT-16, P2) | — |
| — | GET `/api/system/ai-health` | → `{llm, stt, tts, can_start}` | 로그인 | — |

- 코딩 단계의 생존 신호는 모든 코딩 API 호출 + IV-15(C7). 외부망 TURN을 도입하면 ICE 서버를 내려주는 시스템 API를 그때 추가한다(13절).

`InterviewState`(TRD 04 3.3.1, 현재 턴만 노출, 다음 질문 선노출 없음, 출처·근거·판단 사유 미포함):

```json
{"attempt_status": "INTERVIEWING", "interrupted_from": null, "mode": "VOICE", "server_now": "2026-10-27T01:00:00.000Z",
 "total_main": 10, "answered_main": 2, "next_after_interview": "CODING", "warnings": [],
 "turn": {"turn_id": "…", "main_no": 3, "is_followup": false, "presentation_no": 1, "phase": "ANSWERING",
          "question_text": "…", "limit_sec": 60, "opened_at": "…Z", "deadline_at": "…Z"},
 "slot_grace_until": null, "coding_deadline_at": null}
```

## 8. 데이터채널 프로토콜 v1 (단일 원천)

- 공통 봉투: `{"v":1, "type":"…", "seq":17, "turn_id":"uuid|null", "server_now":"2026-10-27T09:00:00.123Z", …필드}`. `seq`는 연결 안 단조 증가, **모든 이벤트에 `turn_id`**(턴과 무관한 이벤트는 `null`). 시각은 모두 ISO 8601 UTC 밀리초(`Z`). epoch 숫자는 쓰지 않는다.
- 스키마 원천: `backend/app/voice/dc_protocol.py`(Pydantic) → JSON Schema → 프런트 TS 타입 생성. 계약 시험이 양쪽 왕복 픽스처를 검사한다(D-03).
- 클라이언트는 `turn_id`가 현재 턴과 다른 이벤트를 버린다. 지원자에게 가는 메시지에는 꼬리질문 판단 사유·anchor·출처(`BANK_FALLBACK` 등)·디버그 필드를 싣지 않는다(D-09). 운영에서 `debug` 타입 송신은 실패로 처리.

| type | 방향 | turn_id | 필드 | 시점 |
|---|---|---|---|---|
| `hello` | S→C | 현재 턴 또는 null | `session_id, purpose:"precheck"\|"interview", resume:{main_no, is_followup}\|null` | 채널 open 직후 |
| `question` | S→C | 새 턴 | `main_no(1~10), is_followup, text, audio:"track"\|"none", limit_sec(60\|45), presentation_no` | S1 |
| `tts_end` | S→C | 턴 | `played: bool` | S2 / TTS 불가 |
| `answer_opened` | S→C | 턴 | `opened_at, deadline_at, warn_at, limit_sec` | S3 |
| `answer_warn` | S→C | 턴 | `remaining_sec: 10` | S4 |
| `caption_segment` | S→C | 턴 | `segment_no, text` | 구간 STT 완료 |
| `answer_finalized` | S→C | 턴 | `reason:"button"\|"timeout", text, no_answer` | S7 |
| `processing` | S→C | 직전 턴 | — | 확정 뒤 다음 제시 전 |
| `interview_completed` | S→C | null | `next:"CODING"\|"SUBMITTED"` | 마지막 문항 뒤 |
| `status_changed` | S→C | null | `attempt_status, mode:"VOICE"\|"TEXT"` | 서버 상태 변경 |
| `session_superseded` | S→C | null | — | 같은 지원 건의 새 연결 |
| `precheck_listen` | S→C | null | `opened_at, deadline_at` | IV-18 수락 뒤 |
| `precheck_result` | S→C | null | `ok, text, reason:"OK"\|"NO_SPEECH"\|"NOT_RECOGNIZED"\|"STT_UNAVAILABLE"` | 점검 발화 판정 |
| `error` | S→C | 관련 턴 또는 null | `code` | 서버 측 오류 |
| `pong` | S→C | null | `client_seq, client_now` | `ping` 응답 |
| `ping` | C→S | null | `client_seq, client_now` | 2초 주기(시계·진단용, 생존 판정에 쓰지 않음) |
| `client_ready` | C→S | null | `audio:"playing"\|"blocked"` | 원격 오디오 재생 준비 뒤 1회(진단·첫 제시 대기용) |

- 순서 보장(턴 안): `question` → `tts_end` → `answer_opened` → (`answer_warn`) → `caption_segment`* → `answer_finalized` → `processing`. `answer_finalized` 뒤에는 그 턴의 자막을 보내지 않고, `answer_finalized` 전에는 다음 `question`을 보내지 않는다.
- 첫 제시: 서버는 DC open 뒤 `client_ready`를 받거나 3초가 지나면 첫 턴을 시작한다(클라이언트가 제시를 무기한 늦출 수 없음).
- 이름 정리: TRD 01 §4.2의 `caption_partial` 표기는 이 표의 `caption_segment`로 읽는다. 1단계 설계안들의 `mic_open`·`answer.open`·`answer_final` 등은 폐기.

## 9. 클라이언트 상태 기계 (D 설계안 3.7을 프로토콜 v1에 맞춤)

상태: BOOT, RESUME_GATE, CONNECTING, WAITING, PRESENTING, ANSWERING(경고 포함), CLOSING, RECONNECTING, TEXT_WAITING, TEXT_ANSWERING, SUPERSEDED, MEDIA_ERROR. 1단계 D안의 OPENING·IV-9(음성 파일)·IV-10(개방 알림)·AU-4(시계 API)는 없앤다.

| # | 현재 | 이벤트·조건 | 동작 | 다음 |
|---|---|---|---|---|
| C1 | BOOT | IV-7 = INTERVIEWING이고 점검 화면 [면접 시작]으로 막 진입(사용자 제스처 유효) | 미디어 획득, IV-3(INTERVIEW) | CONNECTING |
| C2 | BOOT | INTERVIEWING·INTERRUPTED인데 새로고침·직접 진입 | — | RESUME_GATE |
| C3 | BOOT | CODING / SUBMITTED·EXPIRED / READY·PREPARING | 해당 화면으로 이동 | (이탈) |
| C4 | RESUME_GATE | [이어서 진행] 클릭(자동재생 제스처 확보) | IV-6 → 미디어 획득 → IV-3(INTERVIEW) | CONNECTING / 409 `CAPACITY_FULL` → INT-13 문구 |
| C5 | CONNECTING | 미디어 획득 실패 | 원인별 안내 + [환경 점검으로] | MEDIA_ERROR |
| C6 | CONNECTING | PC `connected` + DC open + `hello` | 시계 오프셋 갱신, 원격 오디오 `play()` → `client_ready`, IV-7 재조회 | 스냅샷대로 WAITING/PRESENTING/ANSWERING |
| C7 | CONNECTING | 10초 안에 연결 실패 | — | RECONNECTING |
| C8 | WAITING | `question`(새 turn_id) | 질문 텍스트·진행률 "N / 10"·꼬리질문 배지 표시, 마이크 트랙 off | PRESENTING |
| C9 | PRESENTING | `question.audio="none"` 또는 원격 오디오 재생 차단 | "질문 음성을 재생하지 못해 글자로 안내합니다" 표시(INT-04). 개방은 서버가 함 | PRESENTING |
| C10 | PRESENTING | `tts_end` | 표시만(게이트는 서버) | PRESENTING |
| C11 | PRESENTING | `answer_opened` | "답변해 주세요", 마이크 트랙 on, [답변 완료] 활성, 남은 시간 = `deadline_at − (now + offset)` | ANSWERING |
| C12 | PRESENTING | `answer_opened` 없이 15초 | IV-7 재조회 | 스냅샷대로 |
| C13 | ANSWERING | `caption_segment`(turn_id 일치) | 자막 덧붙임 | ANSWERING |
| C14 | ANSWERING | `answer_warn` 또는 표시 남은 시간 ≤ 10초 | 경고(색·문구, 스크린리더 1회) | ANSWERING |
| C15 | ANSWERING | [답변 완료] 클릭 | 버튼 비활성, IV-11 | CLOSING |
| C16 | ANSWERING | 표시 남은 시간 0 | 버튼 비활성. **마이크는 `answer_finalized`까지 유지**(서버 유예 1.0초 안의 마지막 말 보존) | CLOSING |
| C17 | CLOSING·ANSWERING | `answer_finalized` | 마이크 off, 확정 텍스트 또는 '답변 없음' 표시(INT-15) | WAITING |
| C18 | CLOSING | 5초간 `answer_finalized` 없음 | IV-7 재조회 | 스냅샷대로 |
| C19 | WAITING | `interview_completed{next}` | 미디어·PC 정리 | 코딩 또는 완료 화면 |
| C20 | WAITING | 15초간 이벤트 없음 | IV-7 재조회(15초마다), 30초 넘으면 "평소보다 오래 걸리고 있습니다" | WAITING |
| C21 | 연결 필요 상태 전부 | PC `failed`, `disconnected` 5초, DC close, 마이크 트랙 `ended` | 마이크 off, 오디오 중지 | RECONNECTING |
| C22 | RECONNECTING | 재시도 1·2·4초 간격 최대 3회, 매회 **IV-6 → IV-3(INTERVIEW)**(순서 고정, B R-11), 연결 제한 10초 | 성공 시 "이 문항을 처음부터 다시 진행합니다" | CONNECTING |
| C23 | RECONNECTING | 3회 실패 | IV-17 | 허용 → TEXT_WAITING / 409 → RESUME_GATE(수동 재시도) |
| C24 | TEXT_WAITING | IV-7 폴링(2초)에 새 턴 | 질문 표시, `<textarea>`(≤2,000자), 서버 마감 기준 남은 시간 | TEXT_ANSWERING |
| C25 | TEXT_ANSWERING | [답변 완료] | IV-13 | TEXT_WAITING |
| C26 | 모든 상태 | `session_superseded` 또는 다른 탭 감지(BroadcastChannel) | 미디어 해제, "다른 창에서 면접이 진행 중입니다" | SUPERSEDED |
| C27 | 모든 상태 | `status_changed`(EXPIRED 등)·HTTP 401 | 미디어 해제, 안내 또는 로그인 | (이탈) |
| C28 | 진행 상태 | 화면 이탈 1초 이상 후 복귀(P2) | 경고 대화상자, IV-14. 상태 변화 없음(X-06) | 그대로 |

## 10. 점검 단계 연결 (INT-01·02·17, AC-08, C4)

1. W1로 점검용 PC 연결(지원 건 PREPARING, 슬롯 불필요, 서버 전체 4개 상한). 클라이언트 제한 10초. 실패 시 "면접 서버와 음성 연결을 맺지 못했습니다…" 안내, 시작 차단.
2. 하향 확인(INT-02, P1): 서버가 안내 음성을 하향 트랙으로 재생 → 지원자가 [들립니다]를 고른다(자가 보고, 시작 차단 아님). 실제 TTS 경로를 시험한다.
3. 짧은 발화 인식(INT-17): IV-18 → 서버가 6초 수신 창을 열고 `precheck_listen` → 같은 게이트·VAD·STT 경로(`purpose=PRECHECK`).
4. 통과 조건: ① VAD 발화 합계 ≥ 0.3초 ② 환각 차단(6절) 뒤 STT 텍스트가 한글·영문·숫자 2자 이상. 문구 일치는 요구하지 않는다(D 안, D-20). 결과 텍스트를 화면에 보여 주고 페이지당 3회까지 재시도. `reason`: NO_SPEECH / NOT_RECOGNIZED / STT_UNAVAILABLE.
5. 통과하면 `interviews.precheck_webrtc_ok_at` 기록(서버가 직접 확인한 값). 마이크·카메라는 IV-4(`…/precheck/media`) 자가 보고(`precheck_media_ok_at`). 면접 시작(IV-5)은 두 기록이 30분 이내일 때만 허용.
6. 점검 PC는 면접 화면 진입 전에 닫는다. 10분 무활동 점검 PC는 서버가 닫는다. 면접은 W5로 새로 연결한다.
7. 측정 항목(S-18): 팀 녹음으로 점검 거짓 실패율. 기준 ≤ 5%[TRD 제안].

## 11. 끊김·재출제·텍스트 모드·하트비트·슬롯 (T9~T12, C7)

| 항목 | 값 | 근거 |
|---|---|---|
| 끊김 판정(음성) | ICE `failed`/`closed` 즉시, `disconnected` 5초 지속, 상향 RTP 무수신 5초, DC close | [TRD], D-22(DC `ping`은 백그라운드 탭에서 늦어져 판정에 안 씀) |
| 판정 시 | 진행 중 턴 ABANDONED(FINALIZING은 확정 계속), 응시 INTERRUPTED, `interruption_count+1` | OQ-21, B R-10 |
| 슬롯 | INTERVIEWING만 점유. 연결 중 15초마다 임대 갱신(`slot_lease_seconds`=120). INTERRUPTED가 되면 갱신을 멈추므로 마지막 갱신 뒤 최대 120초 동안 그 지원 건 전용으로 남았다가 회수된다. CODING 진입·제출 때 반환 | T10, 설정 `slot_lease_seconds` |
| 클라이언트 재연결 | 1·2·4초 간격 3회, 매회 IV-6 → IV-3(INTERVIEW), 연결 제한 10초(총 ≤ 약 37초 < 슬롯 유예 120초) | D T22, B R-11 |
| 재출제 | 같은 문항을 새 `turn_id`로 처음부터(`presentation_no`+1). **문항당 재출제 2회**(`presentation_no` ≤ 3). 횟수는 리포트 표시 | T11, OQ-21 |
| 텍스트 모드 진입(서버 판정) | ① 재출제 상한에 닿은 문항이 다시 끊김, 또는 ② 서버가 20초 이상 살아 있는 연결을 못 본 상태에서 IV-17 요청. 그 외 409 `TEXT_MODE_NOT_ALLOWED` | T12, E #8 |
| 텍스트 모드 동작 | 진입한 문항부터 면접 끝까지(INT-18 "그 문항부터"). `text_mode_since` 기록, 답변 `input_mode=TEXT`, 리포트 표시. 턴은 서버가 만들고 **W7이 그 턴을 처음 돌려준 시각**을 `opened_at`으로, 마감 60/45초 + 유예 1.0초 | T12. 텍스트 모드 제한시간은 PRD에 없어 음성과 같은 값을 쓰는 해석(팀 확인, D 이견 7) |
| 텍스트 모드 생존 | IV-7 폴링·IV-15(30초). 120초 무신호 → INTERRUPTED | C7과 같은 방식 |
| CODING 생존 | 코딩 API(임시저장·실행·코딩 하트비트 30초)가 갱신, 120초 무신호 → INTERRUPTED(표시용). 코딩 마감은 상태와 무관하게 서버 시계로 흐름 | C7 |
| PREPARING | 30분 무활동 → READY(B sweeper) | B |

- 같은 문항이 텍스트 모드에서도 끊겨 `presentation_no` 상한(3)을 넘으면 그 문항은 '답변 없음'으로 확정한다(임시 해석). **B 요청**: `interview_answers.end_reason`에 `ABANDONED` 값 추가(현재 BUTTON·TIMEOUT만) — 추가 전까지는 TIMEOUT으로 기록하고 사유를 감사 로그에 남긴다.
- 재개 시 슬롯이 이미 회수됐고 상한이면 W6이 409 `CAPACITY_FULL`(INT-13 문구)이고 상태는 INTERRUPTED로 남는다. 공고가 마감된 뒤 끊긴 응시는 재개 불가(→ EXPIRED, OQ-27).

## 12. 꼬리질문 판단 흐름 (QST-05, 6.2, T27)

1. 기준 시각 `t0` = 답변이 끝난 시각: BUTTON이면 IV-11 수신 시각, 시간 만료면 `deadline_at`.
2. S7 확정(마지막 구간 STT) → 규칙 판정(TRD 05 T4: 본질문당 1·전체 4, 답변 없음, 무내용 INT-10). 꼬리질문의 답변에는 다시 꼬리질문을 만들지 않는다.
3. 규칙 통과 시 `ai /v1/llm/followup_decide`, 레인 RT, `deadline_ms = t0 + 5,000`(6.2 PRD 값 유지, T27). 마감 초과·불가·검증 실패 → 건너뜀(`followup_decisions.skip_cause` = TIMEOUT·INFRA·INVALID).
4. ASK: `interviews.followups_used`를 조건부 증가(0행이면 LIMIT으로 건너뜀) → 꼬리질문을 `interview_questions`(FOLLOWUP, 같은 번호, `answer_limit_sec=45`, `grounding`)로 저장 → `ai /v1/tts` 즉시 합성 → S1.
5. 꼬리질문 TTS가 `t0 + 5.0초`까지 끝나지 않으면 `question`(텍스트)을 먼저 보내고 합성이 끝나는 대로 송출한다. 마이크는 `tts_end` + 0.3초 뒤 열린다(OQ-22). 합성 실패면 `tts_end{played:false}` 뒤 즉시 개방.
6. SKIP·건너뜀: 다음 본질문(사전 합성 음성) S1. 판단 결과·사유·지연은 지원자에게 보내지 않는다.
7. 시간 만료로 끝난 답변은 확정이 `deadline_at + 1.0초`에 시작되므로 LLM에 남는 시간이 약 1초 짧다. 5초 안 판정은 실측(S-11)으로 확인하고 넘으면 팀에 마감 단축을 제안한다(OQ-31).

## 13. TURN과 외부망

- 사내망(시연 기준, 8.1): TURN 없음. 브라우저와 `app` host 후보로 직접 연결. `app`은 운영에서 host 네트워크(C1)이며, aioice는 UDP 포트 범위를 지정할 수 없으므로 방화벽은 **응시 PC 서브넷 → app 호스트 UDP 전체**를 허용하고 그 밖의 egress는 막는다(E 규칙). 출시 조건: 운영 호스트에서 다른 PC 브라우저로 S-1 재실행 통과(C1).
- 외부망(OQ-18, **미검증**): 자체 coturn(`turns:` TLS 443 + UDP 3478, `use-auth-secret` 임시 자격 증명 24시간, relay 포트 범위 고정 예: 49160~49200, 사설 대역 peer 허용 목록)을 DMZ에 두고, 도입할 때 ICE 서버 목록을 내려주는 시스템 API를 TRD 04에 추가한다. coturn이 app 호스트의 host 후보에 닿아야 한다. 공용 STUN/TURN은 쓰지 않는다(AC-22). 이 구성은 문서 안이며 시험하지 않았다(R-08).

## 14. 지연 예산 (8.2)

| 목표 [PRD] | 구간 | 구성 | 값 |
|---|---|---|---|
| M1 본질문 음성 1초 안 | `question` 송신 → 브라우저 첫 비무음 재생(getStats `audioLevel`) | 사전 합성 PCM 메모리 → 첫 20ms 프레임 + 지터 버퍼 | ≤ 0.3초 [추정], 사전 합성 미완료 시 TTS 1.5~2.2초 [실측] |
| M2 자막 2초 안 | 마지막 발화 프레임 수신 → `caption_segment` 브라우저 수신 | 휴지 판정 0.5 + GPU turbo STT 1.1~1.4 + 전송 | 1.46~1.70초 [실측 S-1, LLM 유휴]. 생성 중 경합 S-10 |
| M3 다음 질문 5초 안(꼬리 없음) | `t0` → 다음 본질문 음성 시작 | drain 0.3 + 마지막 구간 STT 1.1~1.4 + 판단(SKIP) 0.6~1.0 + 제시 0.3 | 약 2.3~3.0초 [실측 합 + 추정] |
| M3 다음 질문 5초 안(꼬리질문) | `t0` → 꼬리질문 음성 시작 | drain 0.3 + STT 1.1~1.4 + 선점 ≤0.49 + 판단 0.57~1.5 + 꼬리 TTS 1.5~2.2 + 0.3 | 약 3.8~6.2초 → 상한 쪽은 텍스트 먼저(OQ-22), S-11·S-12로 판정 |
| M4 리포트 3분(최대 10분) | SUBMITTED → DONE | TRD 05 §10 | S-13 |

판단 0.57초[실측]는 같은 프롬프트 반복(캐시 적중) 값이다. 서로 다른 답변 조건의 첫 호출 prefill은 205~232 tok/s[실측]여서 실제 판단은 더 걸릴 수 있다(S-11).

## 15. 시험 연결

| AC·ID | 시험 |
|---|---|
| AC-08, INT-17 | 점검 PC 실패·발화 미인식·기록 없이 IV-5 직접 호출 → 409. 점검 연결 5개째 → 429 |
| AC-09 | 서버 단위: `opened_at` 이전 타임스탬프 프레임 폐기. 통합: aiortc 클라이언트가 TTS 송출 중 "사과", 개방 뒤 "바나나" → 확정 텍스트에 "사과" 없음 |
| AC-10 | 5초 침묵을 포함한 합성 음성 → 침묵만으로 확정 안 됨, `caption_segment` 수신, IV-11 뒤 `answer_finalized` |
| AC-11 | 가짜 시계: 60/45초 만료 확정, 경고 `deadline_at − 10초`, 유예 1.0초 뒤 도착 프레임 무시, 무발화 '답변 없음' |
| AC-12 | 꼬리질문 본질문당 1·전체 4(조건부 증가), 5초 초과 건너뜀 |
| AC-13, C3 | Q4 답변 중 PC 강제 종료 → IV-11 보류 → IV-6·IV-3(INTERVIEW) → 새 턴 개방 직후 보류된 IV-11(옛 `turn_id`) 전달 → 409 `STALE_TURN`, Q1~Q3 답변 불변 |
| T11·T12 | 같은 문항 끊김 3회 → 텍스트 모드 허용, 정상 연결 중 IV-17 → 409 |
| C7 | CODING 진입 후 WebRTC 없음 + 가짜 시계 120초 이내 코딩 API 호출 → INTERRUPTED 아님 |
| 프로토콜 | `dc_protocol.py` JSON Schema와 프런트 타입 왕복 픽스처, 지원자 메시지 키 허용목록 검사(D-08·09) |
