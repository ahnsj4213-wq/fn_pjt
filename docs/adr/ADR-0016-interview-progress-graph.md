# ADR-0016: 면접 진행과 질문 만들기를 LangGraph 그래프로 돌린다

- 상태: 채택 (2026-11-03, 사용자 결정 "면접 진행하는 부분은 모두 랭그래프로 구현", 이어서 질문 만들기도 같은 날 추가)
- 관련: `ai-architecture` v1.5 6.1절(그래프 2개 중 면접 진행), ADR-0009(WebRTC 턴 엔진), ADR-0013, TRD 06
- 구현: `backend/app/questions/graph.py`(질문 만들기, 아래 "질문 만들기 그래프"), `backend/app/interview/graph.py`, `checkpoint.py`, `turns.py`(노드가 부르는 턴 엔진), `app/voice/session.py`, `app/interview/router.py`(텍스트 모드), 시험 `tests/test_interview_graph.py`

## 맥락

면접 진행의 순서(질문 내기, 답변 기다리기, 글자로 바꾸기, 꼬리질문 판단, 다음 질문 또는 끝)가 음성 세션 코드(`VoiceSession._one_turn`)와 텍스트 모드 엔드포인트에 따로 적혀 있었다. 설계 문서는 이 흐름을 LangGraph 그래프와 PostgreSQL 체크포인트로 두라고 한다.

## 결정

- 면접 진행은 지원 건마다 그래프 하나(`thread_id` = 지원 번호)로 돌린다. 음성과 텍스트 두 전송 방식이 같은 그래프를 쓴다.
- 노드: `present`(질문 내기: 다음 문항 또는 꼬리질문을 새 턴으로), `ask`(전송 방식이 보여 주기: 음성은 질문 이벤트·TTS·생각 시간), `wait_answer`(`interrupt()`로 답변을 기다림), `transcribe`(들은 것을 넘겨받아 답변을 한 번만 확정), `decide_followup`(규칙 → 모델 → 정해진 시간 안에 못 하면 건너뜀, 이어서 갈림길 계산), `finish`(코딩테스트 또는 제출과 평가 작업 등록).
- 갈림길은 모두 코드가 정한다: 질문이 남음/꼬리질문 → `present`, 모두 끝 → `finish`, 끊김 → `present`(같은 문항을 새 제시로), 지원이 더는 진행 중이 아니거나 텍스트 모드 필요 → 끝. 텍스트 모드는 `decide_followup` 뒤에 클라이언트에게 제어를 돌려준다(`POST …/interview/next`가 다음 질문을 요청하는 기존 계약 유지).
- 오디오·타이머·STT 같은 전송은 그래프 상태에 넣지 않고 `config["configurable"]["io"]`(`TurnIO`: `ask`/`capture`/`finalized`)로 넘긴다. 상태에는 식별자와 작은 표시값만 있고 **답변 텍스트는 들어가지 않는다**(체크포인트에 개인 답변이 남지 않게).
- 체크포인트는 PostgreSQL(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`)에 둔다. 응시 상태의 기준은 계속 `applications`이고 체크포인트는 그래프를 이어 가는 데만 쓴다. 끊겼다 다시 접속하면 새 입력으로 `present`부터 시작해 같은 문항을 새 제시(`presentation_no`+1)로 낸다. 진행이 끝나면 스레드를 지우고, 주기 작업(`attempt_sweeper`)이 더는 진행 중이 아닌 지원의 체크포인트를 치운다. 체크포인트가 없는 진행 중 면접(그래프 도입 전)은 `ensure_waiting`이 DB의 현재 턴으로 되살린다.
- 모델 호출은 그대로 4장 호출 계층(`AIClient → run_task → Gateway`)을 거친다. 제출 후 평가(`evaluate_application`)는 그래프 없이 작업 안에서 순서대로 부른다(문서 6.3).

## 질문 만들기 그래프 (`app/questions/graph.py`)

`compose_questions` 작업이 그래프를 한 번 돌린다(`thread_id` = `compose:<지원 번호>:<설정 버전>`). 노드: `plan`(자리마다 출처 정하기: 직접 입력 CUSTOM, 코딩 문제 PROBLEM, 나머지는 분야 노드로) → `personality`·`collaboration`(질문 은행만) → `project`(T3로 이력서 근거 질문, 못 만든 자리는 직무 은행) → `motivation`(회사 은행, 모자라면 T2) → `job_skill`(직무 은행, 모자라면 T2) → `verify`(자리마다 질문이 하나이고 서로 비슷하지 않은지, 아니면 `ComposeError`) → `write`(한 트랜잭션: 다른 설정 버전의 라이브 세트를 대체하고, READY 세트와 불변 MAIN 행을 쓰고, TTS 사전 합성 작업을 넣는다. 같은 버전이 이미 있으면 그것을 돌려준다).

- 어느 출처를 쓸지(은행, 모델, 예비 질문)는 코드가 정한다. 모델이 실패하거나 규칙 검증에 걸리면 다음 출처로 간다. 노드마다 자기 트랜잭션으로 커밋하므로 체크포인트가 "끝났다"고 한 일은 DB에서도 끝나 있다.
- 작업이 실패해 다시 돌 때(워커 중단, 모델 중단) 끝난 노드는 다시 하지 않고 멈춘 노드부터 이어 간다. 끝나면 스레드를 지운다.
- 이전 구현은 한 트랜잭션 안에서 모든 분야를 채우고 썼다. 지금은 노드별 커밋이라 생성된 질문이 회사 질문 은행에 먼저 들어갈 수 있다(기존에도 같은 은행에 넣던 질문이다).
- 상태에는 질문 문장과 이력서 근거 인용(`grounding`)이 있다. 작업이 끝나면 지우고, 끝나지 않은 채 지원 상태가 `PREPARING`·`READY`를 벗어나면 `purge_finished`가 치운다.
- 문서 v1.5의 "실패 → 다시 만들기 → 예비 질문" 갈림길은 별도 노드가 아니라 각 분야 노드 안의 출처 순서(은행 → 모델 → 공용 시드)로 구현되어 있다. 모델 호출의 재시도는 `run_task`가 맡는다.

## 의존성 (CLAUDE.md 10절 기록)

| 패키지 | 버전 | 라이선스 | 이유 |
|---|---|---|---|
| langgraph | 1.2.19 | MIT | 그래프 실행. 로컬 라이브러리이고 원격 호출 없음 |
| langgraph-checkpoint-postgres | 3.2.0 | MIT | 체크포인트를 우리 PostgreSQL에 저장 |
| psycopg[binary,pool] | 3.3.7 | LGPL-3.0 | 위 체크포인터의 드라이버. 라이브러리로 동적 사용, 앱의 DB 접근은 asyncpg 그대로 |

함께 들어오는 `langsmith`(LangChain 추적 클라이언트)는 원격 서비스로 실행 기록을 보낼 수 있으므로 `checkpoint.py`가 `LANGSMITH_TRACING`·`LANGCHAIN_TRACING_V2`를 항상 끈다(외부 전송 없음, AC-22). 의존성 거부 목록 검사(`test_offline_static.py`)는 그대로 통과한다(`langchain-openai` 등은 없다).

## 결과와 한계

- Windows 개발 PC의 기본 이벤트 루프(Proactor)는 psycopg 비동기를 지원하지 않는다. `OwnLoopSaver`가 Windows에서만 작은 selector 루프 스레드에 저장소를 두고 호출을 전달한다. Linux 컨테이너는 저장소를 직접 쓴다.
- 음성 전송 자체(WebRTC 트랙, VAD, STT 워커, 끼어들기 타이머)는 `VoiceSession` 안에 있고 그래프는 그 위의 흐름만 맡는다.
