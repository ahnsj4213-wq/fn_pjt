# 기술 블로그 수집 결과 보고서

생성: 2026-11-13 09:55 대한민국 표준시 (scripts/validate.py)

## 요약

| 기업 | 목표 | 실제 확보 | 검증 통과 | robots.txt | 추출 제외 | 상태 |
|---|---:|---:|---:|---|---:|---|
| 컬리 (kurly) | 20 | 20 | 20 | ok(200) | 2 | 달성 |
| 토스 (toss) | 20 | 20 | 20 | absent(404) | 3 | 달성 |
| 카카오 (kakao) | 20 | 20 | 20 | ok(200) | 19 | 달성 |

- 총 검증 통과 문서: **60 / 60**
- 게시일 없음(null): 0건
- 검증 오류: 0건
- 출처 URL 재확인: 60/60 정상 (HTTP 200)
- 수집 대상에서 제외: 우아한형제들 (woowahan) — Cloudflare 403 on the collector (2026-11-11), not bypassed; replaced by kakao

## 수집 실패·제외 사유

- 컬리: manual_review(team study program, not a tech case) 1건, not_technical(score=3) 1건
  - `manual_review(team study program, not a tech case)` https://helloworld.kurly.com/blog/2025-delivery-debug-study/
  - `not_technical(score=3)` https://helloworld.kurly.com/blog/%EC%BF%A0%ED%8F%B0%EA%B3%BC-%ED%95%A0%EC%9D%B8%EC%9C%BC%EB%A1%9C-%EC%95%9E%EB%8B%A4%EB%A6%AC%EC%82%B4-%ED%95%98%EB%82%98-%EB%8D%94-%ED%8C%90%EB%A7%A4%ED%95%9C-%EC%9D%B4%EC%95%BC%EA%B8%B0/
- 토스: manual_review(English translation of an earlier Korean post) 1건, language(en) 2건
  - `manual_review(English translation of an earlier Korean post)` https://toss.tech/article/toss-frontend-ai-docs-eng
  - `language(en)` https://toss.tech/article/technical-writing-2-eng
  - `language(en)` https://toss.tech/article/technical-writing-1-eng
- 카카오: excluded_title 5건, excluded_category(events) 6건, not_technical(score=3) 1건, short_or_empty_body 1건, language(en) 1건, not_technical(score=2) 1건, not_technical(score=1) 1건, manual_review(a non-developer's AI tool essay, not a tech case) 1건, manual_review(new-hire onboarding retrospective) 2건
  - `excluded_title` https://tech.kakao.com/posts/842
  - `excluded_category(events)` https://tech.kakao.com/posts/836
  - `excluded_category(events)` https://tech.kakao.com/posts/835
  - `excluded_category(events)` https://tech.kakao.com/posts/833
  - `not_technical(score=3)` https://tech.kakao.com/posts/834
  - `short_or_empty_body` https://tech.kakao.com/posts/832
  - `excluded_title` https://tech.kakao.com/posts/830
  - `language(en)` https://tech.kakao.com/posts/828
  - `not_technical(score=2)` https://tech.kakao.com/posts/825
  - `excluded_category(events)` https://tech.kakao.com/posts/818
  - `not_technical(score=1)` https://tech.kakao.com/posts/820
  - `excluded_category(events)` https://tech.kakao.com/posts/819
  - `excluded_title` https://tech.kakao.com/posts/814
  - `excluded_title` https://tech.kakao.com/posts/813
  - `excluded_category(events)` https://tech.kakao.com/posts/811
  - `manual_review(a non-developer's AI tool essay, not a tech case)` https://tech.kakao.com/posts/823
  - `manual_review(new-hire onboarding retrospective)` https://tech.kakao.com/posts/816
  - `manual_review(new-hire onboarding retrospective)` https://tech.kakao.com/posts/815
  - `excluded_title` https://tech.kakao.com/posts/809

## 회사별 문서 목록

### 컬리 (20건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|
| 1 | [엑셀 업로드 중 발생한 Zip Bomb 에러 파헤치기! 🥊](https://helloworld.kurly.com/blog/74-excel-upload-zip-bomb/) | 2024-10-23 | 5,613 | ok(200) |
| 2 | [Spring Boot 버전업 중 알게된 Java 버전별 캡슐화 정책 강화](https://helloworld.kurly.com/blog/75-java-module-with-gson-serialization/) | 2024-12-06 | 8,940 | ok(200) |
| 3 | [뷰티 필터를 개발하며 얻은 새로운 경험](https://helloworld.kurly.com/blog/2022-5th-tech-meetup-web-frontend/) | 2023-04-12 | 4,845 | ok(200) |
| 4 | [컬리의 새로운 배송 시스템 구축 과정과 우리가 배운점](https://helloworld.kurly.com/blog/2023-delivery-system/) | 2024-10-25 | 8,967 | ok(200) |
| 5 | [후기 서비스 AWS Opensearch 도입기](https://helloworld.kurly.com/blog/2023-review-opensearch/) | 2023-11-22 | 8,271 | ok(200) |
| 6 | [MySqlPagingQueryProvider 살펴보기](https://helloworld.kurly.com/blog/2024-mysql-paging-query-provider/) | 2024-12-30 | 16,735 | ok(200) |
| 7 | [LLM Application 구축 도전기 (feat. 소중한 고객님들의 리뷰) - 1부](https://helloworld.kurly.com/blog/2024-review-llm-application/) | 2024-09-25 | 9,819 | ok(200) |
| 8 | [분산 시스템 환경에서 Kafka Consumer 오프셋 이동하기](https://helloworld.kurly.com/blog/2024-spring-kafka-consumer-offset-seeking/) | 2024-12-02 | 14,865 | ok(200) |
| 9 | [우리 팀에도 Jarvis 가 생겼다 – 생성형 AI 로 만든 에러 분석가 이야기](https://helloworld.kurly.com/blog/2025-delivery-jarvis-story/) | 2025-07-01 | 4,590 | ok(200) |
| 10 | [배송 완료 사진 속 객체 탐지를 통한 수기 검수 비용 줄이기](https://helloworld.kurly.com/blog/2025-delivery-photo-object-detection/) | 2025-10-27 | 3,773 | ok(200) |
| 11 | [Kafka Streams 윈도우 도입기](https://helloworld.kurly.com/blog/2025-kafka-streams-window/) | 2025-12-01 | 9,174 | ok(200) |
| 12 | [AI에게 도메인을 가르치다 두 번 갈아엎은 이야기 — LLM Wiki + RAG 혼합기](https://helloworld.kurly.com/blog/2026-delivery-domain-rag/) | 2026-05-20 | 7,608 | ok(200) |
| 13 | [컬리의 입고 시스템이 외부 인입 데이터를 안전하게 동기화하는 방법](https://helloworld.kurly.com/blog/2026-outbox-pattern-and-retry-topic/) | 2026-01-11 | 23,192 | ok(200) |
| 14 | [nginx 설정 없이 우아하게 서비스 점검하기 (上)](https://helloworld.kurly.com/blog/access-block-1/) | 2025-10-10 | 5,552 | ok(200) |
| 15 | [nginx 설정 없이 우아하게 서비스 점검하기 (下)](https://helloworld.kurly.com/blog/access-block-2/) | 2025-10-10 | 8,291 | ok(200) |
| 16 | [AI 토큰 사용량 전사 1위 개발자가 148,000번의 대화에서 배운 것](https://helloworld.kurly.com/blog/ai-orchestration-1/) | 2026-04-02 | 6,191 | ok(200) |
| 17 | [AI 에이전트 15개를 동시에 굴리는 법 — AI 병렬 오케스트레이션 실전 운용기](https://helloworld.kurly.com/blog/ai-orchestration-2/) | 2026-04-02 | 17,525 | ok(200) |
| 18 | [기획서 하나로 결함 등록까지, QA AI Hub를 만들며 배운 것](https://helloworld.kurly.com/blog/ai-qa-from-spec-to-defect/) | 2026-11-03 | 8,891 | ok(200) |
| 19 | [서버리스에서 쿠버네티스로 - Airflow 운영 경험기](https://helloworld.kurly.com/blog/airflow-1/) | 2024-07-08 | 8,989 | ok(200) |
| 20 | [AWS Sagemaker Ground Truth로 훈련용 이미지를 편하게 분류하자](https://helloworld.kurly.com/blog/aws-ground-truth/) | 2020-04-15 | 5,577 | ok(200) |

### 토스 (20건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|
| 1 | [실행은 격리하고, 세션은 이어간다: 망분리 환경의 Agent Platform 설계기](https://toss.tech/article/agent-platform-foundation) | 2026-11-10 | 8,147 | ok(200) |
| 2 | [TASTE, 토스의 추천 파운데이션 플랫폼](https://toss.tech/article/taste) | 2026-11-09 | 7,733 | ok(200) |
| 3 | [손가락을 튕기기로 LLM 비용 줄이기](https://toss.tech/article/snap-llm-cost-reduction) | 2026-10-27 | 5,256 | ok(200) |
| 4 | [머릿속에 있던 로그 정의 기준을 모두의 AI 스킬로 만들기](https://toss.tech/article/dataops-log-definition-skill) | 2026-10-23 | 3,109 | ok(200) |
| 5 | [평균 CPU는 여유로운데, 커밋은 왜 느릴까?](https://toss.tech/article/nic-interrupt-bottleneck) | 2026-10-19 | 5,309 | ok(200) |
| 6 | [실행은 도메인별로, 문맥은 한 레포에](https://toss.tech/article/dw-airflow-migration) | 2026-10-16 | 8,517 | ok(200) |
| 7 | [토스뱅크가 패키지를 관리하는 방식 #2: 토스뱅크 패키지의 세 가지 기준](https://toss.tech/article/package-management-2) | 2026-10-16 | 2,907 | ok(200) |
| 8 | [토스뱅크가 패키지를 관리하는 방식 #1: 패키지를 ESM으로만 제공하기로 했어요](https://toss.tech/article/package-management-1) | 2026-10-15 | 2,763 | ok(200) |
| 9 | [Spark Connect on Kubernetes #2: Operator로 만드는 Control Plane](https://toss.tech/article/spark-connect-on-kubernetes-2) | 2026-10-14 | 6,612 | ok(200) |
| 10 | [서비스 개발에 집중할 수 있는 AI 플랫폼 만들기](https://toss.tech/article/ai-platform-foundation) | 2026-10-13 | 6,287 | ok(200) |
| 11 | [CPU 뼛속까지 써먹고, Milvus 사이징 기준을 만들기까지](https://toss.tech/article/milvus-optimization-and-sizing) | 2026-10-12 | 12,894 | ok(200) |
| 12 | [추천 후보는 많을수록 좋을까? TopK를 최적화해 전환율을 높인 방법](https://toss.tech/article/53545) | 2026-10-01 | 4,628 | ok(200) |
| 13 | [쉼 없이 도는 테스트, 사람이 어디까지 돌봐야 할까요? - 토스닥터(Toss Doctor)](https://toss.tech/article/toss-doctor) | 2026-09-22 | 4,690 | ok(200) |
| 14 | [리더보드 1등 LLM, 토스에서도 1등일까? - Toss Benchmark 구축기](https://toss.tech/article/toss-benchmark) | 2026-09-16 | 8,206 | ok(200) |
| 15 | [토스증권이 GPU-aware를 넘어 GPU-native 클러스터를 구축한 방법](https://toss.tech/article/gpu-native-cluster) | 2026-09-15 | 14,258 | ok(200) |
| 16 | [App Router의 장점은 우리에게도 장점일까요?](https://toss.tech/article/52999) | 2026-09-09 | 6,172 | ok(200) |
| 17 | [AI가 팀 규칙을 지키도록 하는 방법](https://toss.tech/article/52631) | 2026-09-08 | 11,931 | ok(200) |
| 18 | [AI가 만든 코드가 어드민이 되기까지](https://toss.tech/article/52885) | 2026-09-04 | 11,379 | ok(200) |
| 19 | [1%가 겪은 버그 고쳐야할까요?](https://toss.tech/article/qa_hotfix) | 2026-08-28 | 6,336 | ok(200) |
| 20 | [토스증권 추천과 검색은 어떻게 진화하고 있을까?](https://toss.tech/article/tech_talk_talk_3) | 2026-08-26 | 7,149 | ok(200) |

### 카카오 (20건)

| # | 제목 | 게시일 | 본문 길이 | URL 확인 |
|---:|---|---|---:|---|
| 1 | [AI 에이전트를 위한 멀티모달 의사결정 모델 ‘Kanana-2-v-decide’ 오픈소스 공개](https://tech.kakao.com/posts/846) | 2026-11-05 | 9,475 | ok(200) |
| 2 | [옮기는 것보다 증명이 어렵다](https://tech.kakao.com/posts/845) | 2026-11-02 | 13,834 | ok(200) |
| 3 | [팀이 신뢰할 수 있는 AI 분석 환경 만들기: 추천팀의 컨텍스트 설계](https://tech.kakao.com/posts/844) | 2026-10-27 | 7,924 | ok(200) |
| 4 | [문자를 보냈다는데 왜 안 오죠? 길 잃은 국제 MO 메시지 구출 작전](https://tech.kakao.com/posts/843) | 2026-10-21 | 6,680 | ok(200) |
| 5 | [글로벌 최고 수준의 자체 비전 인코더, Kanana-2-vision-encoder 개발기](https://tech.kakao.com/posts/840) | 2026-10-20 | 12,880 | ok(200) |
| 6 | [데이터 분석 에이전트를 만들며 배운 컨텍스트 설계](https://tech.kakao.com/posts/838) | 2026-10-01 | 11,048 | ok(200) |
| 7 | [개인화 추천을 위한 랭킹 모델 개발기](https://tech.kakao.com/posts/837) | 2026-09-22 | 7,163 | ok(200) |
| 8 | [같은 장애를 두 번 겪지 않기 위해, 배포 전에 리뷰합니다 — KRIS 개발기](https://tech.kakao.com/posts/831) | 2026-08-31 | 10,641 | ok(200) |
| 9 | [개인화된 Airflow 테스트 환경 구축 및 운영 경험](https://tech.kakao.com/posts/829) | 2026-08-07 | 14,768 | ok(200) |
| 10 | [잘 말하는 AI를 넘어, 원하는 대로 말하는 AI로: Kanana-o 음성 생성 고도화 과정](https://tech.kakao.com/posts/827) | 2026-08-04 | 25,610 | ok(200) |
| 11 | [더 작고 강해진 Kanana SLM 개발](https://tech.kakao.com/posts/826) | 2026-07-28 | 26,433 | ok(200) |
| 12 | [AI 에이전트로 카카오톡 추천 지표 분석 자동화하기](https://tech.kakao.com/posts/824) | 2026-06-16 | 6,494 | ok(200) |
| 13 | [메시징 서버의 스트레스 테스트 노하우와 AI가 덜어 준 부분](https://tech.kakao.com/posts/822) | 2026-05-22 | 19,805 | ok(200) |
| 14 | [음성 AI 모델을 프로덕션에 올리기까지: Kanana-O 서빙 최적화 여정](https://tech.kakao.com/posts/821) | 2026-05-06 | 11,073 | ok(200) |
| 15 | [수억 건의 보안 신호 속 진짜 위협 찾기 — AI로 보안 모니터링의 패러다임을 바꾸다](https://tech.kakao.com/posts/817) | 2026-03-16 | 9,077 | ok(200) |
| 16 | [한국 문화 이해부터 화면 조작까지: Kanana-V 기능 확장의 모든 것](https://tech.kakao.com/posts/812) | 2026-03-11 | 34,456 | ok(200) |
| 17 | [잃어버린 리포트를 찾아서: 카카오 메시징 시스템의 경쟁 조건 문제와 안티 패턴 제거 과정](https://tech.kakao.com/posts/810) | 2026-02-09 | 14,258 | ok(200) |
| 18 | [Kanana-2 개발기 (2): 개선된 post-training recipe를 중심으로](https://tech.kakao.com/posts/808) | 2026-01-15 | 27,818 | ok(200) |
| 19 | [Kanana-2 개발기 (1): Pre-training에서의 의사결정들을 중심으로](https://tech.kakao.com/posts/807) | 2026-01-15 | 15,771 | ok(200) |
| 20 | [“생각하고 답변하는” 카카오의 하이브리드 멀티모달 언어모델, Kanana-v-4b-hybrid 개발기](https://tech.kakao.com/posts/806) | 2026-01-05 | 44,252 | ok(200) |

