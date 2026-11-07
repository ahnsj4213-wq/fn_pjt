# Architecture Decision Records

형식: `ADR-NNNN-<slug>.md`. 각 ADR은 아래 절을 가진다.

```
# ADR-NNNN: 제목
- 상태: 제안 | 채택(임시) | 채택 | 대체됨(→ ADR-xxxx)
- 날짜: YYYY-MM-DD
- 작성: 에이전트 X / 통합 F
- 관련: PRD ID, OQ ID, consolidation-round1 항목(C1~C8, T1~T27)

## 맥락
## 결정
## 검토한 대안 (장단점)
## 근거 (실측·문서·재현 가능한 위험)
## 결과와 영향 (코드·스키마·운영)
## 남은 위험과 재검토 조건
```

"채택(임시)"는 팀·법률 확인이 필요한 결정이다(`docs/open-questions.md`). 기술적 선택은 ADR로 결정하고 법률·운영 결정은 임의로 확정하지 않는다.

| ADR | 제목 | 작성 | 상태 |
|---|---|---|---|
| 0001 | 프로세스 구성과 서비스 경계 (7개 프로세스) | A | |
| 0002 | 기술 스택과 DB 드라이버(asyncpg) | A | |
| 0003 | 단일 LLM 공유: 게이트웨이 우선순위 레인과 선점 | C | |
| 0004 | STT·TTS·임베딩 모델 선정과 배치 (X-11 임시) | C | |
| 0005 | 질문 구성과 질문은행 회사 범위 | C | |
| 0006 | 상태 기계, 영속 작업 큐, 평가 재시도, 점수 계산 | B | |
| 0007 | 코딩테스트 runner와 샌드박스, 검증 전 비활성 | E | |
| 0008 | 인용 검증 정규화와 원문 범위 저장 | C | |
| 0009 | 실시간 면접 프로토콜: turn_id, 명령 HTTPS, 이벤트 데이터채널, 슬롯·하트비트·재출제 | C | |
| 0010 | 배포 토폴로지, HTTPS, 오프라인 운영, 운영 Docker 보안 모드 | A | |
| 0011 | API 규약: 오류 형식, 상한값, 지원자/담당자 응답 분리, PDF 보기 | B | |
| 0012 | 인증·세션·CSRF·회사 격리 | E | |
| 0013 | Redis 작업 분배·실시간 알림(SSE). jobs 테이블은 영속 기록·outbox로 유지 | 사용자 결정 | ADR-0006 분배 부분 대체 |
| 0014 | 컨테이너 구성도: frontend(Next.js)·api·mailer·worker(Celery)·scheduler·llm-rt·llm-eval·stt·tts·embed·postgres(pgvector)·redis·minio, 샌드박스는 worker가 실행 | 사용자 결정 | ADR-0001 서비스 분리, ADR-0010 앞단 대체 |
| 0015 | 컨테이너 통합 12→8: frontend·api(+migrate)·worker(+beat)·postgres·redis·llm-gpu·ai(STT·TTS·평가 LLM·임베딩)·mailer | 사용자 결정 | ADR-0014 배치 부분 대체 |
| 0016 | 면접 진행과 질문 만들기를 LangGraph 그래프로(진행: present→ask→wait_answer(interrupt)→transcribe→decide_followup→present/finish, 만들기: plan→분야별 채우기→verify→write)present→ask→wait_answer(interrupt)→transcribe→decide_followup→present/finish), PostgreSQL 체크포인트, 전송은 TurnIO로 주입 | 사용자 결정 | ADR-0009 턴 엔진 위의 흐름 제어 |
| 0017 | 코딩 점수 = 정확성 60·효율성 20·문제 해결 방식 10·코드 품질 10, 지원자·담당자 리포트 공개, 라이브 코딩도 채점 | 사용자 결정 | PRD 6.5, COD-07 변경 |
| 0018 | 팀 표준 화면정의서의 색·글꼴·컴포넌트 모양 적용, 다크 모드 제거, 아키텍처·라우트·API는 유지 | 사용자 결정 | wireframe-v14는 구조 기준으로 유지 |
| 0019 | 꼬리질문 판단 제한 5초 → 30초, 인용 불일치 1회 재요청 (PRD 6.2의 5초와 충돌, 실측 근거) | 사용자 결정 | RULE-6.2 수치 변경 |
| 0020 | 회사 지식베이스 RAG 필수화: 회사·공고 범위 검색, 동기·직무 질문 생성 우선, 근거 `grounding.sources`, 자료 없으면 공고 기반 대체, 가상 기업 12곳 | 사용자 결정 | QST-01 순서 변경 |
| 0021 | 코딩 문제은행 Lv.0~5 55문제, 직무·경력·난이도로 공고별 레벨을 정하고 지원 건마다 최근 출제를 피해 선정, 태그·출처는 담당자 전용 | 사용자 요청 | COD-01~06, 이전 "하~중 문제를 공고에 고정" 결정 변경 |
| 0022 | 본질문을 면접 중에 한 문항씩 생성: 시작 전에는 계획과 첫 질문만, 이후는 직전 답변·이력서·회사 자료로 생성, 실패·지연은 질문은행 | 사용자 결정 | QST-02 변경, `AIHIRE_QUESTION_MODE=upfront`로 원래 방식 |
