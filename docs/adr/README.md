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
