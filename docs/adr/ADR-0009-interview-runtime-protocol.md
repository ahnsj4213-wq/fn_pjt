# ADR-0009: 실시간 면접 프로토콜 — turn_id, 명령 HTTPS, 이벤트 데이터채널, 슬롯·하트비트·재출제
- 상태: 채택(임시) — 텍스트 모드 제한시간 해석(D 이견 7), 하향 TTS 경로 해석(8.1)은 팀 확인
- 날짜: 2026-10-27
- 작성: 에이전트 C / 통합 F
- 관련: X-01, X-02, INT-04·05·06·09·11·13·15·17·18, COD-02~05, AC-08~14, OQ-15·18·21·22·27, consolidation-round1 C3·C4·C7·T7~T12·T27

## 맥락

1라운드 리뷰에서 실시간 계약의 결함이 드러났다.

- C3(B R-02, D-02): [답변 완료]의 키·경로가 설계마다 달랐고, 재출제(OQ-21) 뒤 늦게 도착한 옛 명령이 새 제시분을 확정해 답변을 잃는 경로가 재현됐다(INT-11 위반).
- C4(B R-03): 모든 WebRTC 연결에 슬롯을 요구하면 PREPARING 점검 연결이 막혀 시작할 수 없었다(AC-08).
- C7(D-01): 하트비트가 WebRTC 런타임에서만 와서 코딩 단계(WebRTC 없음)가 90초 뒤 INTERRUPTED가 되고 코딩 API가 막혔다.
- 데이터채널 메시지가 세 벌(A·C·D), 시각 형식·식별자도 달랐다(D-03, A R6).
- 실측 S-1: 서버→브라우저 TTS 하향 트랙 송출 → `tts_end` → 0.3초 뒤 게이트 개방, 게이트 이전 발화 5.46초를 서버가 폐기, 자막 휴지 후 1.46~1.70초. aiortc 기본 공용 STUN은 외부 통신 + 첫 프레임 10.2초.

## 결정

1. **턴**: 문항 제시 1회 = `interview_turns` 행 1개 = `turn_id`(UUID). 상태 PRESENTING → ANSWERING → FINALIZING → CLOSED, 끊김 시 ABANDONED. 지원 건당 진행 중 턴 1개. 모든 명령·이벤트·멱등 판정은 `turn_id`. 진행 중이 아닌 턴에 대한 명령은 409 `STALE_TURN`. 확정은 턴 조건부 갱신 + `interview_answers.turn_id` 유일 제약으로 한 번만.
2. **채널**: 클라이언트→서버 명령은 HTTPS(쿠키 + CSRF + Origin, T8). 서버→클라이언트 이벤트는 데이터채널 `control`. 상향 데이터채널은 진단용 `ping`과 첫 제시 대기용 `client_ready`(3초 상한)뿐.
3. **데이터채널 프로토콜 v1**: 봉투 `{v, type, seq, turn_id, server_now}`, 모든 이벤트에 `turn_id`(무관하면 null), 시각은 ISO 8601 UTC 밀리초. 이벤트 `hello, question, tts_end, answer_opened, answer_warn, caption_segment, answer_finalized, processing, interview_completed, status_changed, session_superseded, precheck_listen, precheck_result, error, pong`. `caption_partial`은 예약(v1 미송신). 지원자에게 판단 사유·anchor·출처·디버그를 싣지 않는다. 원천은 `app/voice/dc_protocol.py` → JSON Schema → TS 타입(TRD 06 §8).
4. **TTS 경로**: WebRTC 하향 오디오 트랙(T7). 게이트는 서버가 마지막 프레임 송신 + 0.3초에 연다. 클라이언트 신호로 앞당겨지지 않는다.
5. **답변 창**: `deadline_at = opened_at + 60초(본)/45초(꼬리)`, 경고 `deadline_at − 10초`, 네트워크 유예 1.0초(`answer_grace_seconds`, T9). 유예 뒤 도착한 오디오·명령은 무시. 침묵으로는 끝내지 않는다(X-02).
6. **시그널링**: 비-trickle, `iceServers=[]`(서버·브라우저). 점검 연결은 PREPARING만 요구(슬롯 불필요), 서버 전체 4개 상한(C4). 면접 연결은 INTERVIEWING + 슬롯. 같은 지원 건의 새 연결은 기존 연결을 대체(`session_superseded`).
7. **끊김·재출제**: 서버가 ICE·RTP로 판정(ICE failed/closed 즉시, disconnected 5초, RTP 무수신 5초). 진행 중 턴 ABANDONED(FINALIZING은 확정 계속), 응시 INTERRUPTED. 재개는 클라이언트가 `resume`(멱등) → 새 offer 순서. 같은 문항을 새 턴으로 처음부터. **문항당 재출제 2회**(`presentation_no` ≤ 3, T11).
8. **텍스트 모드(INT-18)**: 서버 판정만(T12) — 재출제 상한에 닿은 문항이 다시 끊겼거나, 서버가 20초 이상 살아 있는 연결을 못 본 상태에서 요청한 경우. 그 문항부터 면접 끝까지. 턴의 `opened_at`은 상태 API가 그 턴을 처음 돌려준 시각, 마감은 음성과 같은 60/45초 + 유예(해석, 팀 확인).
9. **슬롯·하트비트(T10, C7)**: 슬롯은 INTERVIEWING만 점유. 연결 중 15초마다 임대 갱신(`slot_lease_seconds`=120), INTERRUPTED가 되면 갱신이 멈춰 최대 120초 그 지원 건 전용으로 남았다가 회수. CODING 진입·제출 때 반환. CODING 생존은 코딩 API(임시저장·실행·코딩 하트비트 30초)가 갱신하고 120초 무신호일 때만 INTERRUPTED(코딩 마감은 상태와 무관하게 서버 시계). 텍스트 모드 생존은 상태 폴링·하트비트 30초, 120초 무신호 → INTERRUPTED.
10. **꼬리질문 판단**: 답변 종료 시각 + 5.0초를 RT 마감으로(PRD 값, T27). 꼬리질문 TTS가 그때까지 안 끝나면 텍스트 먼저, 음성은 합성되는 대로, 마이크는 `tts_end` + 0.3초 뒤(OQ-22).

명령 경로 표(TRD 04의 IV-3·4·5·6·7·11·13·14·15·17 + 추가 요청 IV-18)와 클라이언트 상태 기계(C1~C28)는 TRD 06 §7·§9. 경로 이름은 B의 TRD 04와 일치해야 한다.

## 검토한 대안 (장단점)

| 쟁점 | 대안 | 판정 |
|---|---|---|
| 식별자 | 질문 ID / 본질문 번호 `seq` / **제시 회차 `turn_id`** | 질문 ID·번호는 재출제 때 반복돼 옛 명령을 거를 수 없다(C3) → `turn_id` |
| 명령 채널 | 데이터채널(A·C 1단계) / **HTTPS**(B·D·E) | 데이터채널은 응답이 없어 전달 실패를 모르고 CSRF·감사·멱등이 약하다(D-05, E SC-42) → HTTPS |
| TTS 경로 | HTTP 파일 + 클라이언트 `ended` 알림(D 1단계) / **WebRTC 트랙** | HTTP는 클라이언트가 개방을 앞당길 수 있다(E #10, A R9). 트랙은 S-1로 동작 확인 → 트랙 |
| 유예 | 0.5초(C) / 1.5초(A·E) / **1.0초** | 사내망 RTT 기준 절충, 설정값(T9) |
| 재출제 | 무제한(C 1단계) / **2회** | 무제한은 준비 시간을 늘리는 우회(E #9) |
| 텍스트 모드 | 클라이언트 전환(D) / **서버 판정** | 클라이언트 자가 전환은 음성·STT 우회(E #8) |
| 슬롯 | CODING까지 점유(OQ-15 원안) / **INTERVIEWING만** | 상한 원인은 GPU·STT. 코딩 재진입 거부 시 서버 시계 제한시간을 잃는다 |
| 끊김 판정 | DC `ping` 무응답 / **ICE·RTP 상태** | 백그라운드 탭에서 타이머가 늦어져 ping은 오판한다(D-22) |

## 근거

- 재현: Q4 답변 중 PC 강제 종료 → 오프라인 동안 완료 명령 보류 → 재개 → 새 제시 개방 직후 보류 명령 전달 → 질문 ID 키면 새 답변이 확정됨. `turn_id` 키면 409.
- S-1(`prototypes/rtc_browser/server.py`): 하향 트랙·`tts_end`·0.3초 게이트·서버 폐기·에너지 VAD 0.5초·GPU turbo 자막. S-0(`rtc_loopback.py`): `iceServers=[]`로 0.17초.
- 스키마(반영됨): `interview_turns(presentation_no 1~3, state, end_reason BUTTON|TIMEOUT|DISCONNECT, ux_turn_live)`, `interview_answers.turn_id UNIQUE`, `interviews.current_phase`, `interview_slots`, `followup_decisions`.

## 결과와 영향

- `app`(FastAPI + aiortc, 워커 1)이 시그널링·PC·턴 상태 기계·게이트·자막·확정·슬롯 갱신을 맡는다(ADR-0001).
- B(TRD 04): 경로·오류 코드는 04 3.3절과 일치시켰다. 추가 요청 — IV-18 `POST …/precheck/utterance`(409 `PRECHECK_NOT_CONNECTED`), IV-11이 PRESENTING 턴에 오면 409 `STATE_CONFLICT`, `interview_answers.end_reason`에 ABANDONED(텍스트 모드 상한 초과) 검토.
- D: 상태 기계에서 OPENING·개방 알림 명령·음성 파일 API·시계 API 제거, 재연결 순서 resume → offer, 시간 0에서도 마이크는 `answer_finalized`까지 유지.
- E: 지원자 세션의 데이터채널 메시지도 금지 키 검사 대상(QG-02), 시그널링에 CSRF·Origin, 운영 `debug` 이벤트 금지.

## 남은 위험과 재검토 조건

- aiortc 장시간(30분)·재연결·다른 PC·운영 Linux host 네트워크는 미검증(R-10, C1 출시 조건). 데이터채널이 불안정하면 이벤트 전달만 SSE로 바꾸고 이 프로토콜의 메시지 스키마는 유지한다.
- 외부망 TURN(coturn) 구성안은 미검증(OQ-18, R-08).
- 꼬리질문이 있는 전환은 5초 예산 상한(약 6.2초 [실측 합])을 넘을 수 있다 → S-11·S-12, OQ-31.
- 텍스트 모드 제한시간과 상한 초과 문항의 '답변 없음' 처리는 해석이다. 팀이 달리 정하면 8번만 고친다.
