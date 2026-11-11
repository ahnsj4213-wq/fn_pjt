# 기술 블로그 수집 결과 보고서

생성: 2026-11-11 19:00 대한민국 표준시 (scripts/validate.py)

## 요약

| 기업 | 목표 | 실제 확보 | 검증 통과 | robots.txt | 추출 제외 | 상태 |
|---|---:|---:|---:|---|---:|---|
| 우아한형제들 (woowahan) | 20 | 0 | 0 | robots_unavailable | 0 | 미달 |
| 컬리 (kurly) | 20 | 20 | 20 | ok(200) | 0 | 달성 |
| 토스 (toss) | 20 | 20 | 20 | absent(404) | 0 | 달성 |

- 총 검증 통과 문서: **40 / 60**
- 검증 오류: 0건
- 출처 URL 재확인: 실행 안 함 (`--check-urls`)
- **목표 미달**: 우아한형제들 20건 부족

## 수집 실패·제외 사유

- 우아한형제들: 수집 불가 — robots.txt 확인 실패: https://techblog.woowahan.com/robots.txt: HTTP 403
- 컬리: 제외 없음
- 토스: 제외 없음

## 회사별 문서 목록

### 우아한형제들 (0건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|

### 컬리 (20건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|
| 1 | [엑셀 업로드 중 발생한 Zip Bomb 에러 파헤치기! 🥊](https://helloworld.kurly.com/blog/74-excel-upload-zip-bomb/) | 2024-10-23 | 5,613 | - |
| 2 | [Spring Boot 버전업 중 알게된 Java 버전별 캡슐화 정책 강화](https://helloworld.kurly.com/blog/75-java-module-with-gson-serialization/) | 2024-12-06 | 8,940 | - |
| 3 | [뷰티 필터를 개발하며 얻은 새로운 경험](https://helloworld.kurly.com/blog/2022-5th-tech-meetup-web-frontend/) | 2023-04-12 | 4,845 | - |
| 4 | [컬리의 새로운 배송 시스템 구축 과정과 우리가 배운점](https://helloworld.kurly.com/blog/2023-delivery-system/) | 2024-10-25 | 8,967 | - |
| 5 | [후기 서비스 AWS Opensearch 도입기](https://helloworld.kurly.com/blog/2023-review-opensearch/) | 2023-11-22 | 8,271 | - |
| 6 | [MySqlPagingQueryProvider 살펴보기](https://helloworld.kurly.com/blog/2024-mysql-paging-query-provider/) | 2024-12-30 | 16,927 | - |
| 7 | [LLM Application 구축 도전기 (feat. 소중한 고객님들의 리뷰) - 1부](https://helloworld.kurly.com/blog/2024-review-llm-application/) | 2024-09-25 | 9,927 | - |
| 8 | [분산 시스템 환경에서 Kafka Consumer 오프셋 이동하기](https://helloworld.kurly.com/blog/2024-spring-kafka-consumer-offset-seeking/) | 2024-12-02 | 14,961 | - |
| 9 | [딜리버리 프로덕트 개발팀의 개발 문화 - 주니어 디버깅 스터디](https://helloworld.kurly.com/blog/2025-delivery-debug-study/) | 2025-04-14 | 10,363 | - |
| 10 | [우리 팀에도 Jarvis 가 생겼다 – 생성형 AI 로 만든 에러 분석가 이야기](https://helloworld.kurly.com/blog/2025-delivery-jarvis-story/) | 2025-07-01 | 4,590 | - |
| 11 | [배송 완료 사진 속 객체 탐지를 통한 수기 검수 비용 줄이기](https://helloworld.kurly.com/blog/2025-delivery-photo-object-detection/) | 2025-10-27 | 3,808 | - |
| 12 | [Kafka Streams 윈도우 도입기](https://helloworld.kurly.com/blog/2025-kafka-streams-window/) | 2025-12-01 | 9,174 | - |
| 13 | [AI에게 도메인을 가르치다 두 번 갈아엎은 이야기 — LLM Wiki + RAG 혼합기](https://helloworld.kurly.com/blog/2026-delivery-domain-rag/) | 2026-05-20 | 7,608 | - |
| 14 | [컬리의 입고 시스템이 외부 인입 데이터를 안전하게 동기화하는 방법](https://helloworld.kurly.com/blog/2026-outbox-pattern-and-retry-topic/) | 2026-01-11 | 23,192 | - |
| 15 | [nginx 설정 없이 우아하게 서비스 점검하기 (上)](https://helloworld.kurly.com/blog/access-block-1/) | 2025-10-10 | 5,552 | - |
| 16 | [nginx 설정 없이 우아하게 서비스 점검하기 (下)](https://helloworld.kurly.com/blog/access-block-2/) | 2025-10-10 | 8,291 | - |
| 17 | [AI 토큰 사용량 전사 1위 개발자가 148,000번의 대화에서 배운 것](https://helloworld.kurly.com/blog/ai-orchestration-1/) | 2026-04-02 | 6,191 | - |
| 18 | [AI 에이전트 15개를 동시에 굴리는 법 — AI 병렬 오케스트레이션 실전 운용기](https://helloworld.kurly.com/blog/ai-orchestration-2/) | 2026-04-02 | 17,525 | - |
| 19 | [기획서 하나로 결함 등록까지, QA AI Hub를 만들며 배운 것](https://helloworld.kurly.com/blog/ai-qa-from-spec-to-defect/) | 2026-11-03 | 8,891 | - |
| 20 | [서버리스에서 쿠버네티스로 - Airflow 운영 경험기](https://helloworld.kurly.com/blog/airflow-1/) | 2024-07-08 | 9,205 | - |

### 토스 (20건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|
| 1 | [실행은 격리하고, 세션은 이어간다: 망분리 환경의 Agent Platform 설계기](https://toss.tech/article/agent-platform-foundation) | 2026-11-10 | 8,147 | - |
| 2 | [TASTE, 토스의 추천 파운데이션 플랫폼](https://toss.tech/article/taste) | 2026-11-09 | 7,733 | - |
| 3 | [손가락을 튕기기로 LLM 비용 줄이기](https://toss.tech/article/snap-llm-cost-reduction) | 2026-10-27 | 5,256 | - |
| 4 | [머릿속에 있던 로그 정의 기준을 모두의 AI 스킬로 만들기](https://toss.tech/article/dataops-log-definition-skill) | 2026-10-23 | 3,109 | - |
| 5 | [평균 CPU는 여유로운데, 커밋은 왜 느릴까?](https://toss.tech/article/nic-interrupt-bottleneck) | 2026-10-19 | 5,309 | - |
| 6 | [실행은 도메인별로, 문맥은 한 레포에](https://toss.tech/article/dw-airflow-migration) | 2026-10-16 | 8,517 | - |
| 7 | [토스뱅크가 패키지를 관리하는 방식 #2: 토스뱅크 패키지의 세 가지 기준](https://toss.tech/article/package-management-2) | 2026-10-16 | 2,907 | - |
| 8 | [토스뱅크가 패키지를 관리하는 방식 #1: 패키지를 ESM으로만 제공하기로 했어요](https://toss.tech/article/package-management-1) | 2026-10-15 | 2,763 | - |
| 9 | [Spark Connect on Kubernetes #2: Operator로 만드는 Control Plane](https://toss.tech/article/spark-connect-on-kubernetes-2) | 2026-10-14 | 6,612 | - |
| 10 | [서비스 개발에 집중할 수 있는 AI 플랫폼 만들기](https://toss.tech/article/ai-platform-foundation) | 2026-10-13 | 6,287 | - |
| 11 | [CPU 뼛속까지 써먹고, Milvus 사이징 기준을 만들기까지](https://toss.tech/article/milvus-optimization-and-sizing) | 2026-10-12 | 12,894 | - |
| 12 | [추천 후보는 많을수록 좋을까? TopK를 최적화해 전환율을 높인 방법](https://toss.tech/article/53545) | 2026-10-01 | 4,628 | - |
| 13 | [쉼 없이 도는 테스트, 사람이 어디까지 돌봐야 할까요? - 토스닥터(Toss Doctor)](https://toss.tech/article/toss-doctor) | 2026-09-22 | 4,690 | - |
| 14 | [리더보드 1등 LLM, 토스에서도 1등일까? - Toss Benchmark 구축기](https://toss.tech/article/toss-benchmark) | 2026-09-16 | 8,206 | - |
| 15 | [토스증권이 GPU-aware를 넘어 GPU-native 클러스터를 구축한 방법](https://toss.tech/article/gpu-native-cluster) | null | 14,258 | - |
| 16 | [App Router의 장점은 우리에게도 장점일까요?](https://toss.tech/article/52999) | null | 6,172 | - |
| 17 | [AI가 팀 규칙을 지키도록 하는 방법](https://toss.tech/article/52631) | null | 11,931 | - |
| 18 | [How Documents Find Developers at Toss](https://toss.tech/article/toss-frontend-ai-docs-eng) | null | 5,733 | - |
| 19 | [AI가 만든 코드가 어드민이 되기까지](https://toss.tech/article/52885) | null | 11,379 | - |
| 20 | [1%가 겪은 버그 고쳐야할까요?](https://toss.tech/article/qa_hotfix) | null | 6,336 | - |

