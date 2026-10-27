# 설계 2단계 상호 리뷰 1라운드 — 에이전트 B(백엔드·데이터베이스)

- 검토 대상: `agent-A-architecture.md`, `agent-C-ai-voice.md`, `agent-D-frontend-ux.md`, `agent-E-security-qa.md`, 조정자 실측 `prototypes/README.md`.
- 초점: 데이터·API 계약 충돌, 트랜잭션 경계, 상태 전이, 회사 격리, 멱등성.
- 심각도: **Critical**(보안 취약점·데이터 손실·PRD 위반·실행 불가능) / **Major** / **Minor**(취향 차이 포함).
- 내 설계안(`agent-B-backend-data.md`)의 자체 수정은 1절에 적었다. 2단계 규칙상 그 파일은 고치지 않았으니 통합할 때 반영해 주세요.

## 1. 실측 결과로 바뀐 내 입장

| 실측 (F) | B 설계안 대비 바뀐 입장 |
|---|---|
| 문항 평가 6~11초/답변 → 14답변 순차 약 1.4~2.6분 | 10분 시도 제한은 여유가 있다. 3분 목표(8.2)는 아슬아슬하므로 EVL-05(P1) "함께 구현" 의견(A·C)에 동의한다. 단위 평가를 `(answer_id, manifest_hash, criterion)` 유일 키로 저장해 run끼리 재사용하고, 따로 두었던 캐시 테이블 `answer_pre_evaluations`는 없앤다. 펜싱은 최종 반영(리포트·DONE)에만 건다 |
| 인용 원문 비교 6/20 → 정규화 비교 20/20 | 인용 검증은 정규화 비교로 한다(ADR-0008). 평가 행에 `quote_norm_version`과 검증 기준 텍스트 버전을 저장한다. 정규화 범위는 아직 쟁점(R-04) |
| 스트림 연결을 끊으면 생성 취소 0.49초, 취소 없으면 11초 대기 | ai-gw 선점을 실제로 쓸 수 있다. 선점(PREEMPTED)은 작업 시도 실패가 아니라 한 시도 안에서 단위를 다시 요청하는 일로 본다. `evaluation_runs.preempted_count`는 진단용으로만 둔다 |
| `num_ctx`를 바꾸면 5.1초 재적재 | `num_ctx`를 포함한 LLM options를 manifest 필수 항목으로 넣는다(EVL-08 재현성) |
| 샌드박스 12/12 차단, 컨테이너 기동 약 0.45초 | E의 G-b(테스트 1건 = 컨테이너 1개, 기대 출력 비교는 runner 밖)가 비용 면에서 가능하다(20케이스 약 9~15초). runner에는 기대 출력을 보내지 않고, `coding_test_results`에는 통과 여부와 상태만 저장한다. [실행]은 비동기 큐+폴링 대신 **동기 호출**(A·D)로 바꾼다. `coding_runs`는 동시 1건 제한과 기록 용도로만 남긴다 |
| 브라우저↔aiortc 자막 1.46~1.70초, 게이트 이전 발화를 서버가 폐기 | 마이크 개방 시각과 마감 시각의 권위는 서버에 있다. 그래서 문항 제시 단위 엔터티 `interview_turns(id=turn_id, question_id, presentation_no, gate_open_at, deadline_at, status OPEN/CONFIRMED/ABANDONED, end_reason)`를 추가한다. 1단계 안의 `POST .../questions/{q}/finish`(question_id 키)는 재출제 때 같은 키가 반복되어 멱등 키로 쓸 수 없으므로 **폐기**한다 |
| Supertonic-3 RTF 약 0.3, 본질문 10개 사전 합성 15~25초 | 면접 시작 조건을 "질문 세트 READY"(텍스트)로만 두고, TTS 사전 합성 완료는 조건에서 뺀다. 미완 문항은 제시할 때 즉시 합성한다(C 3.3). TTS 지연 때문에 `QUESTIONS_NOT_READY`가 나는 일을 줄인다 |
| CPU STT small 2.0~2.6초(목표 미달), GPU turbo 1.1~1.4초 | 스키마 영향은 없다. 동시 응시 상한은 STT의 GPU 공유로 정해질 수 있으니 설정값으로 유지한다. 답변 행의 `stt_model`은 manifest로 대체한다 |
| aiortc 기본값은 공용 STUN 사용 → `iceServers=[]` 필수 | D의 `GET /api/system/rtc-config` 기본값 `[]`를 운영 기동 검증(E SC-61) 항목에 넣는다 |

다른 설계안을 읽고 고친 점은 세 가지다.
- PDF 파싱을 API 본 프로세스에서 하지 않는다(E 2.7). 그래서 업로드 응답에서 `text_extractable`을 바로 돌려주던 설계를 거둔다(R-18 참조).
- 지원자 결과의 `feedback: null`은 키 자체를 빼는 방식으로 바꾼다(D CA-11).
- 감사 로그 REPORT_VIEW는 상세 조회(RC-12)가 아니라 리포트 조회(RC-14)에서만 남긴다(D 3.10).

## 2. 리뷰 표

| # | 대상 설계안·절 | 문제 | 심각도 | 근거·재현·테스트 | 제안 해결 |
|---|---|---|---|---|---|
| R-01 | C 3.5 T2(질문은행 보충), QST-01 | 공고·회사 소개로 AI가 만든 기업·직무 질문을 `source=AI`로 은행에 넣는다고만 적고 **회사 범위가 없다**. 그러면 X사 사업·공고 내용이 Y사 면접에 출제된다 | **Critical** (회사 간 데이터 노출, SEC-03 "어떤 경로로도", E TH-33) | 재현: X사 공고 업무란에 `CANARY-X-77` 입력 → 은행이 부족한 상태에서 X사 지원자 질문 구성 → Y사 지원자 질문 구성. 테스트: Y사 질문 세트 전체에서 카나리아 0건 | 은행 행에 `company_id`를 필수로 둔다(B DDL `CHECK (source<>'AI' OR company_id IS NOT NULL)`). 선택 쿼리는 `company_id IS NULL OR company_id=:cid`로 하고, 유사 중복 판정도 같은 범위에서만 한다. 공용 은행에는 SEED만 둔다(E SC-16과 같음) |
| R-02 | A 4.1·4.3, C 3.3·4.3, D 2.7·4.2(IV-10~13)·4.3, E SC-42·46 | [답변 완료]·마이크 개방·텍스트 답변 명령의 **키와 경로가 넷 다 다르다**. A는 데이터채널 `answer_complete{seq}`를 rtc로, C는 `answer.done{aid}`(데이터채널, REST 대체)를, D는 `POST /api/.../attempts/{attempt_id}/done`(HTTP, api로)을, E는 HTTPS만 쓰고 `expected_question_seq`를 붙인다. A는 진행기를 rtc에 두는데 구간 STT 텍스트가 rtc 메모리에 있으므로, api가 받은 done으로는 답변을 확정할 수 없다. `attempt_id`도 A에서는 응시 단위, D에서는 문항 제시 단위라 뜻이 다르다 | **Critical** (실행 불가능: INT-05·AC-10 경로 미정, 확정 중복·누락) | 재현 1: D 경로로 api가 done을 받아 DB만 갱신 → rtc는 마감까지 계속 수신 → 버튼이 무시된다(AC-10 실패). 재현 2: 버튼과 시간 만료가 같은 순간 → 두 프로세스가 각각 확정한다. 테스트: 데이터채널 done, HTTP done, 마감 타이머를 같은 ms에 주입 → 답변 행 1개, `end_reason`은 먼저 커밋된 것, 응답은 둘 다 2xx(멱등) | 모든 이벤트·명령의 키를 문항 제시 단위 `turn_id`(UUID, `interview_turns` PK)로 통일하고 `attempt_id`라는 이름은 쓰지 않는다. 진행기 소유자는 한 프로세스로 한다: 음성 모드는 rtc, 텍스트 모드(INT-18)는 api. `interviews.mode`로 배타 소유를 표시한다. HTTP 명령은 Caddy가 `/rtc/v1/...`로 rtc에 보내 진행기가 직접 처리하게 하고(D가 원하는 CSRF·응답 코드는 유지), 데이터채널 명령도 같은 `handle_done(turn_id)`를 부른다. 확정은 `UPDATE interview_turns SET status='CONFIRMED' WHERE id=:t AND status='OPEN'` 조건부 갱신으로 한 번만 일어난다 |
| R-03 | E SC-40 | "응시 상태와 **슬롯 보유를 확인한 뒤에만** 피어 연결 생성"을 OQ-16(슬롯은 면접 시작 때 확보)과 같이 적용하면, PREPARING 단계의 점검 연결(INT-17)을 맺을 수 없다. 점검 통과 기록이 없으니 start도 불가능한 **교착**이 생긴다 | **Critical** (실행 불가능, AC-08) | 테스트: PREPARING이고 슬롯이 없는 상태에서 `purpose=precheck` offer → 200, `purpose=interview` offer → 409. start 후 interview offer → 200 | SC-40을 목적별로 나눈다. precheck: PREPARING + 소유자 확인 + 전역 점검 연결 상한(A "응시 상한 + 점검 2"). interview: INTERVIEWING(또는 유예 중인 INTERRUPTED) + 슬롯 |
| R-04 | E SC-72 ↔ C 3.6 `qn-1` | 인용 정규화 규칙이 충돌한다. E는 NFKC, 공백 축약, 앞뒤 공백 제거만 허용하고 문장부호 대량 제거를 금지한다. C는 공백·문장부호·기호를 전부 지우고 간투사 제거와 표기 동치 사전까지 쓴다. F 실측에서 모델이 "APM 으로"처럼 공백을 끼워 넣어 원문 비교는 6/20, 공백·일부 문장부호 제거 비교는 20/20이었다. E 규칙으로는 끼워 넣은 공백이 그대로 남아 '근거 부족'(1점)이 대량으로 나온다 | Major | F의 `quote_norm_check.py` 20건을 E 규칙으로 다시 돌려 일치 수를 비교한다. 회귀 테스트: 고정 픽스처 20건의 일치율이 실측치 이상 | ADR-0008 기준 "NFKC + 공백 전부 제거 + 문장부호(P)만 제거"를 기본으로 한다. C의 표기 동치 사전(파이썬↔python)은 EVL-02 "원문과 같은지"를 넘어서므로 빼기를 권장한다. 간투사 제거는 평가용 텍스트(eval_text)를 만들 때 이미 반영되므로 검증 단계에서 다시 적용하지 않는다 |
| R-05 | C 4.2·3.8, D 4.2 RC-14 | 평가 원문이 두 개다: `transcript_raw`(담당자 표시용)와 `answer_eval_text`(간투사 제거·마스킹, 인용 검증 기준). D는 인용 `{start,end}` 오프셋으로 강조하는데 어느 텍스트 기준인지 정하지 않았다. 마스킹 결과 `[지원자]`가 들어간 인용은 raw에서 찾을 수 없다 | Major | 픽스처: 답변에 자기 이름 포함 → 인용에 `[지원자]` 포함 → raw 기준 오프셋 계산 실패. 테스트: 리포트의 모든 인용 오프셋이 응답에 실린 표시 텍스트의 부분 문자열과 일치 | `interview_answers`에 `transcript_raw`, `eval_text`, `eval_text_version`, `stt_failed`를 둔다(B 스키마 확장). 리포트의 질문별 답변은 eval_text를 기본으로 표시하고 raw는 토글로 보여 준다. 오프셋은 서버가 eval_text 기준으로 계산해 저장한다. 두 텍스트 모두 보관 삭제(365일·탈퇴) 대상에 넣는다 |
| R-06 | C 3.5 T5 ↔ D RC-14 `quote{...}\|null` ↔ B `quote text` | 항목당 인용 개수가 다르다: C는 1~2개 배열, B·D는 1개 | Major (계약) | 가짜 LLM이 인용 2개를 반환하면 B 스키마에 저장할 수 없거나 1개가 사라진다 | `quotes jsonb`(배열, 원소 `{text,start,end,verified}`). CHECK: 근거 부족이 아니면 1개 이상이고 전부 verified. D 응답 스키마도 배열로 바꾼다 |
| R-07 | C 3.5 T5 ↔ E AC-16 ↔ B | '답변 없음'을 '근거 부족'으로 셀지가 다르다. C는 NO_ANSWER를 "전 항목 1점"으로 따로 구분하고, E AC-16과 EVL-02 원문은 "무응답·매우 짧은 답변은 규칙으로 '근거 부족'"이다. 점수는 같지만 리포트의 "근거 부족 개수"(6.4)가 구현마다 달라진다 | Major | 픽스처: 10문항 중 2문항 무응답 → 근거 부족 개수 8(항목 4 × 2) 대 0 | EVL-02 문장대로 무응답도 근거 부족으로 센다. 사유 `NO_ANSWER/TOO_SHORT/QUOTE_MISMATCH/NO_EVIDENCE`를 저장해 화면에서 '답변 없음' 배지와 개수를 구분해 보여 준다 |
| R-08 | A 4.1 jobs 계약 ↔ B 3.6 | 펜싱 토큰 정의가 두 개다. A는 `run_no`(작업마다 1부터), B는 전역 시퀀스 `lease_token`. 재평가(ADM-06)는 새 작업이라 A의 run_no가 1로 돌아가고, `evaluation_runs` 유일 제약과 결과 반영 조건이 꼬인다. 이 밖에 상태 이름(done/dead 대 SUCCEEDED/FAILED), 임대 시간(2분 대 60초), `requires text[]` 대 `resource`도 다르다 | Major | 테스트: ON_HOLD → 재평가 → 새 작업의 첫 실행과 옛 작업 좀비가 같은 run_no=1로 반영을 시도 → 옛 것이 통과하면 실패 | 펜싱은 작업 간에도 단조 증가하는 전역 시퀀스 토큰 + `WHERE id=:job AND lease_token=:tok`로 한다. `evaluation_runs.run_no`는 지원 건 단위로 B가 매긴다. 이름은 A 것을 쓴다(`queued/running/done/dead`, `lane`, `requires text[]`). 임대는 60초 + 하트비트 15초(값은 설정) |
| R-09 | A 3.2 6단계(면접 시작) | 시작 "한 트랜잭션"에 AI 상태 확인(ai-gw·speech HTTP)이 들어 있다. 공고 행 `FOR UPDATE`와 슬롯 advisory lock을 쥔 채 외부 호출을 하므로, ai-gw가 느리면 다른 시작 요청과 설정 변경이 잠금을 기다린다 | Major | 재현: ai-gw 응답에 5초 지연 주입 + 동시 start 3건 → p95 지연과 lock_timeout 오류를 측정 | 상태 확인은 트랜잭션 밖에서 10초 캐시값으로 하고, 트랜잭션 안에서는 DB 작업만 한다(B 3.7 순서). runner 상태(E-Q3)도 같은 방식 |
| R-10 | OQ-15 해석: A 3.8·4.3, C 3.3, D 3.8 IV-15, E AC-13·14 | 끊김 처리와 슬롯 회수 시점이 서로 다르다. C는 데이터채널 10초 무응답이면 즉시 INTERRUPTED, D는 재연결 최대 3회(약 7초 이상) 후 resume, E AC-13은 "INTERRUPTED는 슬롯 반환"을 기대한다. 그 결과 10초짜리 망 끊김 뒤 재진입할 때 슬롯을 다른 사람이 가져갔으면 **면접 도중 CAPACITY_FULL**이 난다. 또 D는 코딩 중에도 슬롯 하트비트를 보내는데, 코딩 중 끊긴 지원자가 상한에 걸리면 서버 시계로 계속 흐르는 코딩 시간을 잃는다 | Major (공정성, INT-11·COD-05) | 테스트 1: 상한 1, A가 면접 중 끊김 → 즉시 B가 start → 유예 안이면 409, A 재진입 200. 테스트 2: 상한 1, A가 CODING 중 끊김 → B 면접 시작 → A 재진입 → CODING 복귀 200 | INTERRUPTED 전이는 즉시 한다(C). 슬롯은 그 지원 건 전용으로 120초 유예한 뒤 회수한다(B). E AC-13의 기대값을 "유예가 지난 뒤 반환"으로 고친다. CODING은 슬롯을 점유하지 않게 한다(OQ-15 수정안, 팀 결정). D의 IV-15는 CODING↔INTERRUPTED 판정에만 쓴다 |
| R-11 | D 3.7 T22 ↔ E SC-40 ↔ B | 재연결 순서 문제. D는 새 offer를 먼저 보내고 그다음 IV-6 resume을 부른다. 서버가 이미 INTERRUPTED로 바꿨다면 offer 시점에 상태가 INTERVIEWING이 아니고(슬롯도 회수됐을 수 있음) 409가 난다. 재연결 3회가 소진되면 텍스트 모드로 잘못 넘어간다 | Major | 테스트: 데이터채널을 12초 끊어 INTERRUPTED를 만든 뒤 D 순서대로 재연결 → 텍스트 모드로 가면 실패 | 클라이언트 순서를 resume → offer로 바꾼다. resume은 멱등으로 만든다(이미 INTERVIEWING이면 200 + 스냅샷, 같은 지원 건의 유예 슬롯은 재사용) |
| R-12 | D 4.1 `Idempotency-Key`(CA-9, IV-10·11·13, CD-4, RC-16), E "request_id 멱등" ↔ B(자연 멱등만) | B는 유일 제약과 조건부 갱신으로 중복을 막는다. 하지만 응답이 유실돼 재시도하면 409가 돌아가고, D는 상황에 맞지 않는 문구를 띄운다: CA-9 "이미 지원한 공고", CD-4 "자동 제출됨", RC-16 "다른 담당자가 먼저 변경" | Major | 테스트: 같은 키로 CA-9 두 번 → 둘 다 201, 같은 application_id. 다른 키 → 409 ALREADY_APPLIED. RC-16을 같은 키로 재전송 → 200, 같은 결과, 이력 1행 | 테이블 `idempotency_keys(user_id, key, route, request_sha256, status, response_json, created_at)`, PK(user_id, key), 24시간 보관. 응답에 개인정보가 없는 세 경로(CA-9·CD-4·RC-16)에만 적용한다. 턴 명령(IV-10·11·13)은 turn 상태로 자연 멱등이다(이미 같은 결과면 202) |
| R-13 | 오류 코드: B 3.12, D 4.1·4.2, E 4, A 4.3 | 같은 상황에 오류 코드가 다르다: `INVALID_CREDENTIALS`(B·D)/`AUTH_FAILED`(E), `HAS_APPLICATIONS`/`HAS_APPLICANTS`, `WEIGHT_SUM_NOT_100`/`WEIGHTS_SUM`, `RUN_IN_PROGRESS`/`RUN_BUSY`, `RUNNER_UNAVAILABLE`/`RUNNER_DISABLED`/`CODE_EXECUTION_UNAVAILABLE`, `QUESTIONS_NOT_READY`/`PREP_IN_PROGRESS`, `INVALID_STATE`/`STATE_CONFLICT`, `DRAFT_CONFLICT`/`STALE_REVISION`, `DECISION_CONFLICT`/`VERSION_CONFLICT`. 오류 응답 형식도 중첩(B)과 평면(D)으로 갈린다 | Major (계약) | 프런트 분기와 AC 테스트가 code 문자열에 의존한다. 테스트: OpenAPI 오류 enum과 프런트 `fixed.ts`, E 매트릭스 기대값을 대조하는 스크립트 | 레지스트리를 하나로 둔다(`app/core/errors.py`, B 소유). 이를 OpenAPI enum으로 내보내 D가 타입을 생성한다. 형식은 평면 `{code, message, fields, request_id}`. 이름은 쓰는 쪽(D·E) 것을 우선한다: AUTH_FAILED, HAS_APPLICANTS, WEIGHTS_SUM, RUN_BUSY, CODE_EXECUTION_UNAVAILABLE, QUESTIONS_NOT_READY, STATE_CONFLICT, STALE_REVISION, VERSION_CONFLICT |
| R-14 | A 4.4 runner `/v1/grade {tests:[{input, expected, hidden}]}` ↔ E 2.3 G-b | A는 기대 출력과 비공개 입력을 한 요청으로 runner에 넘기면서, 테스트를 어떤 단위로 실행할지(컨테이너 1개에 여러 테스트인지)는 정하지 않았다. G-a 방식으로 구현되면 제출 코드가 기대 출력을 읽을 수 있다(COD-06) | Major | E SBX-11(카나리아) 실행. F 실측 기동 0.45초로 G-b 비용을 감당할 수 있다 | E 계약을 채택한다: runner 입력은 `cases[{case_id, stdin}]`만, 비교는 worker가 한다. B는 비공개 테스트를 `app_worker` 역할만 읽게 권한을 나눈다 |
| R-15 | E SC-68(감사 로그 해시 체인) | 요청마다 "마지막 해시 읽기 → 계산 → INSERT"를 하면 동시 요청에서 같은 prev_hash를 가진 행이 둘 생겨 체인이 갈라지고, 일일 검증이 거짓 경보를 낸다. 반대로 직렬화하면 리포트 열람 같은 모든 감사 대상 요청이 전역 잠금을 탄다 | Major | 테스트: 감사 INSERT 50건을 동시에 → 체인 검증 통과 | 행은 해시 없이 INSERT한다(append-only 트리거와 권한 회수는 유지). 1분 주기 봉인 작업(worker-sys)이 id 순서로 `prev_hash`·`row_hash`를 채운다. 봉인된 행은 UPDATE 금지 트리거로 막는다 |
| R-16 | C 4.2 `EVAL_ANSWER`/`EVAL_FINALIZE`, C 3.4-4 "검증 통과 단위 재사용" | 재시도·재평가 때 이전 run의 단위 결과를 재사용한다고 했지만 저장 키와 펜싱 범위가 없다. 좀비 run의 단위 결과가 섞여 들어갈 수 있다 | Major | 테스트: run1이 단위 5개 저장 후 펜싱 아웃 → run2가 재사용 → 리포트 1건, 단위 중복 0 | 단위 결과 키를 `(answer_id, manifest_hash, criterion)` 유일로, `INSERT ... ON CONFLICT DO NOTHING`, 불변으로 둔다. 검증을 통과한 단위만 저장되므로 run과 독립적이다. 펜싱은 FINALIZE 단계의 리포트·DONE 반영에만 건다 |
| R-17 | A 3.2 9단계 "제출은 api 또는 rtc", A 4.1 "rtc가 쓰는 칼럼" | rtc가 상태 전이(INTERVIEWING→SUBMITTED, 작업 등록)를 직접 커밋하는데 DB 역할 경계가 없다. E SC-18·19는 앱 역할 하나를 가정한다 | Minor | 정적 검사: rtc 진입점에서 decision·weights 쓰기 0건 | DB 역할 `app_rtc`를 추가한다. 권한은 applications의 attempt/eval 칼럼, interviews, turns, answers, jobs의 INSERT로만 한정한다. 전이 함수는 공용 모듈 하나에 둔다(A 4.1) |
| R-18 | D CA-7·8 ↔ E 2.7 ↔ A 3.2-2 | D는 손상 PDF를 업로드 응답 422로 받기를 기대하고, E·A는 파싱을 API 밖에서 하려 한다. 비동기로만 처리하면 손상 판정이 폴링 단계로 밀린다 | Minor | AC-06: 손상 PDF 업로드 → 즉시 422 | 텍스트 추출까지는 API가 시간·메모리 상한을 건 하위 프로세스로 동기 처리한다(20초 이하, E SC-22). 손상이면 422, 스캔 PDF면 `NO_TEXT`. LLM 구조화만 비동기 작업으로 한다 |
| R-19 | D 3.8 CD-2, E SC-49, B 3.6 | 코딩 마감 직후 저장 유예가 다르다. E는 마감 뒤 거부, B는 3초 유예, D는 종료 5초 전 강제 저장 | Minor | 가짜 시계 테스트: 마감 1초 전 저장 200, 마감 1초 후 저장은 정책값대로 | 저장 유예를 1.5초로 한다(E SC-44 네트워크 유예와 같은 값). 자동 제출은 유예가 끝난 뒤 실행한다 |
| R-20 | C 3.5 T1 ↔ B 3.2 ↔ D 3.5 | 이력서 표준 항목 JSON 키가 다르다: `experience/certifications`(C) 대 `career/certificates`(B) | Minor | 스냅샷 JSON Schema 검증 테스트 | C의 키를 `resume-schema v1`로 확정하고 B·D가 따른다 |
| R-21 | A 3.2-8 ↔ B 3.6 | 코딩 자동 제출 방식이 다르다. A는 CODING 진입 때 `run_after=마감`인 일회 작업을 등록하고, B는 15초 주기로 훑는다 | Minor | — | A안을 채택하고, B의 주기 점검은 누락 보완용(멱등)으로 남긴다 |
| R-22 | D 4.2 IV-7 `version`, `can_*`, `locks` | B 스키마에 상태 스냅샷의 버전이 없다 | Minor | — | `interviews.state_version`을 턴이 바뀔 때마다 올린다. 능력 플래그(`can_*`, `locks`)는 B 서비스가 계산한다 |
| R-23 | E SC-04·05, 3.4.4 | B 스키마에 로그인 실패 카운터, 세션 CSRF 토큰, 샌드박스 점검 기록 테이블이 없다 | Minor | — | `login_throttle`, `sessions.csrf_token_hash`, `sandbox_verifications`를 추가한다 |

## 3. 다른 설계안에서 받아들이는 점

- **A**
  - 프로세스 경계: rtc 분리, worker-ai와 worker-sys 분리.
  - runner와 ai-gw에는 DB 자격 증명을 주지 않는다.
  - jobs 계약 이름: `lane`, `requires[]`, `dead`.
  - 코딩 자동 제출은 일회 작업으로 한다.
  - 상태 전이 함수는 공용 모듈 한 곳에 둔다.
  - 마감 시각의 원본은 DB(`deadline_at`)다.
- **C**
  - 원문과 평가용 텍스트(`transcript_raw`/`eval_text`)를 분리하고 `stt_failed`를 저장한다.
  - manifest 테이블과 해시를 둔다(EVL-08).
  - 본답변과 꼬리답변을 각각 평가한다.
  - 꼬리질문 판단 로그 테이블 `followup_decisions(main_question_id, decision, reason, anchor, latency_ms, skip_cause RULE/LLM/TIMEOUT/INVALID)`를 둔다. A가 제안한 "생략 사유 리포트 표시"에도 쓴다.
  - 커스텀 질문은 7번 자리에 넣는다.
  - T7은 별도로 생성하고 별도로 저장한다.
  - 10분 제한은 RUNNING 진입부터 잰다(B와 같음).
- **D**
  - 문항 제시 단위 명령과 HTTP 명령 경로를 받아들인다. 단 라우팅은 rtc로 한다(R-02).
  - 서버가 `can_*`·`locks`를 계산하고, 스냅샷에 `version`을 둔다.
  - 결정 전에는 `feedback` 키를 생략한다.
  - 평면 오류 형식과 `fields[]`.
  - `resume-extractions` 리소스와 `update_registered` 플래그.
  - 리포트 탭을 열 때만 열람 감사를 남긴다.
  - 단조 증가 `revision`(STALE_REVISION).
  - 10MB는 10 MiB로 정의한다.
- **E**
  - 라우트 정책 데코레이터와 라우트 인벤토리 테스트를 B의 범위 헬퍼와 결합한다.
  - CSRF synchronizer 토큰을 쓴다.
  - 로그인 실패 카운터는 DB에 둔다.
  - 감사 해시 체인을 둔다. 단 봉인 방식으로(R-15).
  - `SANDBOX_ERROR`는 0점이 아니다(B의 채점 인프라 지연 처리와 같다).
  - DB 역할을 분리한다.
  - RLS는 P1 시점에 다시 검토한다(B와 같다).
  - AC-03·04·14·18·21 테스트 설계를 B 테스트 ID에 그대로 연결한다.

## 4. 남은 쟁점 (합의 필요)

| # | 쟁점 | 관련 | 결정 주체·기준 |
|---|---|---|---|
| 1 | 턴 명령 경로: HTTP(rtc로 라우팅) + 데이터채널 병행 / HTTP만(E) / 데이터채널만(A·C) | R-02 | A·C·D·E. aiortc 데이터채널 장시간 안정성 실측(30분·재연결, 미검증) 뒤 결정 |
| 2 | 인용 정규화 범위(공백·문장부호만 vs 표기 동치·간투사 포함) | R-04 | C·E, F의 ADR-0008 |
| 3 | 무응답을 '근거 부족' 개수에 넣을지 | R-07 | 팀(EVL-02 해석) |
| 4 | OQ-15 수정: CODING은 슬롯 미점유, INTERRUPTED는 120초 유예 후 반환 | R-10 | 팀 + 기술 |
| 5 | PRD 7.1 표에 없는 전이: OQ-B1(READY/PREPARING→EXPIRED), OQ-B2(INTERRUPTED→SUBMITTED, 코딩 자동 제출). OQ-B3(마감 시 진행 중인 응시를 끝까지 허용할지)은 D 이견 15와 같은 쟁점 | B 3.4 | 팀 |
| 6 | 시도 횟수: 임대 상실은 미소모(OQ-B5), 재시도 때 검증 통과 단위 재사용 | R-16 | C·F, EVL-06 "다시 만든다" 해석 |
| 7 | 총점 반올림 순서(저장된 항목 점수로 계산). E의 AC-17 오라클도 같은 순서를 써야 한다 | B 3.9, OQ-10 | E·F |
| 8 | 오류 코드 최종 이름과 응답 형식 | R-13 | B·D·E |
| 9 | TTS 하향 경로: A·C는 WebRTC, D는 HTTP. F가 WebRTC 하향 + `tts_end` + 300ms 게이트를 실측으로 성공시켰다. D안이면 IV-9 오디오 엔드포인트와 서버 자동 개방 규칙이 B 계약에 추가된다 | D 2.8 | A·C·D |
| 10 | 결정을 검토 전으로 되돌리는 것 불허(OQ-B6), 응시 중 탈퇴 차단(OQ-B7), 감사 로그 보관 기간(OQ-B8) | B 3.4·3.10 | 팀·법률 |
| 11 | 담당자 리포트의 기본 표시 텍스트(eval_text vs raw) | R-05 | C·D |
| 12 | 텍스트 모드(INT-18) 제한시간. B는 같은 `deadline_at` 칼럼을 쓰고 값은 팀이 정한다 | D 이견 7 | 팀 |
