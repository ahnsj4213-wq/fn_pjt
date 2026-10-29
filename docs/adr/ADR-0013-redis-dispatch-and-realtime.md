# ADR-0013: Redis 작업 분배·실시간 알림

- 상태: 채택 (2026-10-29, 사용자 결정) — ADR-0006의 "작업 큐는 DB만" 결정 중 **분배(대기·집기)** 부분을 대체한다. ADR-0006의 영속 기록·펜싱·재시도·상태 기계는 그대로다.
- 날짜: 2026-10-29
- 관련: PRD 7.2·8.3, AC-18, SEC-04, ADR-0003·0006, TRD 01 §1, TRD 03 §7
- 구현: `app/jobs/dispatch.py`, `app/jobs/runtime.py`, `app/events/relay.py`·`router.py`, `app/core/redis.py`, 마이그레이션 0003(`jobs.dispatched_at`), `frontend/src/app/liveUpdates.ts`, `deploy/docker-compose*.yml`의 `redis`

## 맥락

- 팀 요구: Redis로 **AI 작업 대기줄을 관리**하고 **결과를 화면에 실시간 전달**한다.
- ADR-0006은 Redis+Celery를 기각했다. 기각 이유: DB 커밋과 큐 등록이 원자적이지 않다. 원자적이지 않으면 PRD 7.2 "제출과 같은 트랜잭션으로 평가 작업 기록"·8.3 "누락 0"을 지키려고 결국 outbox가 필요하다.
- 따라서 Redis를 넣되 이 보장을 잃지 않는 구조가 필요하다.

## 결정

1. **`jobs` 테이블은 영속 기록이자 outbox로 남긴다.** `enqueue`는 지금처럼 호출자 트랜잭션 안에서 행을 넣는다. 임대·펜싱(`lease_token`)·시도 횟수·`ON_CLAIM`/`ON_REQUEUE`/`ON_EXHAUSTED`·reaper도 PostgreSQL에서 그대로 한다.
2. **대기줄은 Redis로 옮긴다.**
   - 자원(LLM·CPU·RUNNER·NONE)마다 sorted set `aihire:q:{resource}`를 둔다. 점수 = 우선순위 × 10¹³ + 실행 가능 시각(ms)이라 기존 SQL 집기와 같은 순서다.
   - worker의 dispatcher가 0.5초마다 실행 가능한 QUEUED 작업을 넘긴다. 실행 가능 = `run_after` 경과, 선행 작업(`depends_on`) 종료.
   - 넘긴 시각은 `dispatched_at`에 남긴다. `ZADD`는 멱등이라 다시 보내도 중복이 생기지 않는다.
3. **worker 레인은 `BZPOPMIN`으로 기다리다 받은 id를 PostgreSQL에서 조건부로 집는다.** 이미 집혔거나 아직 때가 안 된 id는 버린다. 인프라 탐침이 내려가 있으면 꺼내지 않는다(OQ-23, AC-18 유지).
4. **자가 복구**:
   - 넘긴 뒤 30초가 지나도 QUEUED이면 다시 보낸다. Redis 재시작·꺼낸 뒤 worker 사망 같은 경우다.
   - 재시도로 `run_after`가 미뤄진 작업은 그 시각이 되면 다시 보낸다(`dispatched_at < run_after`).
   - Redis가 내려가면 레인은 기존 SQL 집기로 자동 전환한다. Redis는 영속 설정이 필요 없다.
5. **실시간 알림**:
   - 상태가 바뀌면 DB 트리거가 `application_state_events`에 남긴다(TRD 03 §4). 이 테이블을 worker가 0.5초마다 읽어 Redis pub/sub로 발행한다.
   - 채널: 지원자 `aihire:ev:user:{id}`, 회사 `aihire:ev:company:{id}`.
   - worker 여럿이 있어도 Redis 임대 키로 한 곳만 발행한다.
   - 메시지는 "어느 지원 건이 바뀌었다"뿐이다. 상태값·개인정보는 없다. 화면은 이 신호를 받으면 기존 허용목록 API로 다시 조회한다(SEC-04).
6. **전달 경로는 SSE**(`GET /api/events/stream`):
   - 세션 확인은 짧은 DB 세션으로 끝내고, 스트림은 DB 연결을 잡지 않는다.
   - 스트림은 최대 10분이다. 브라우저 EventSource가 재연결하면서 세션을 다시 확인한다.
   - Redis가 없으면 503을 돌려준다. 화면은 평소 조회 방식으로 계속 동작한다.
7. **면접 중 실시간(질문·자막·답변 확정)은 바꾸지 않는다.** 그대로 WebRTC 데이터채널이다(ADR-0009). Redis를 거치면 수십 ms 단위 순서·지연 보장이 약해진다.

## 결과

- 작업은 대기 중 폴링 없이 즉시 집힌다. 지연은 dispatcher 주기 0.5초 이내다.
- 담당자 결정·평가 완료가 열린 화면에 새로고침 없이 반영된다. E2E로 확인했다(지원 현황의 '합격').
- 누락 0·펜싱·인프라 게이트는 그대로다. 근거는 `tests/test_job_queue.py`, `tests/test_redis_dispatch.py`.
- 운영 구성요소가 하나 늘었다. 오프라인 반입 이미지는 `redis:7-alpine`이다. Redis 장애는 성능 저하(SQL 폴백·실시간 알림 없음)로만 나타난다.

## 대안

| 대안 | 판단 |
|---|---|
| 큐를 Redis만으로(Celery/RQ/arq), DB 기록 없음 | 기각: 제출과 작업 등록의 원자성(7.2)·누락 0(8.3)을 잃는다. 펜싱·인프라 게이트를 다시 만들어야 한다 |
| 실시간 알림을 PostgreSQL LISTEN/NOTIFY로 | 가능했으나, 팀이 Redis 사용을 정했다. pub/sub 채널을 사용자·회사 단위로 나누기 쉽다 |
| 실시간 알림을 WebSocket으로 | 단방향 알림이라 SSE로 충분하다. 쿠키 인증·프록시 통과가 단순하다 |
