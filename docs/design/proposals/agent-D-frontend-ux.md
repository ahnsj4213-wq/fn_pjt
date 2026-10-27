# 에이전트 D — 프런트엔드·사용자 경험 설계안 (1단계 독립안)

- 작성: 에이전트 D(프런트엔드·UX). 상태: **제안**(조정자 F 통합 전). 작성일 2026-10-26.
- 기준: `통합 PRD.pdf` v1.0 초안, `docs/requirements-traceability.md`(ID·우선순위 기준), `docs/open-questions.md`(OQ), `docs/risks.md`(R), `docs/environment.md`.
- 표기: **[원문 외]** = PRD에 없는 UX 보강(채택은 팀 결정). **[신규]** = 추적표에 없는 API 제안. **OQ-nn** = 미결정 항목의 임시 해석이며 확정으로 다루지 않는다.

## 1. 범위와 전제

### 1.1 범위
- 담당: 지원자·담당자 웹 화면, 라우팅·역할 가드, 사용자 흐름, 서버 상태·클라이언트 상태 관리, 폼 검증, 오류·로딩·빈 상태·복구 UI, 브라우저 미디어 클라이언트(getUserMedia, WebRTC, 질문 음성 재생), 프런트 테스트.
- 계약만 요구하고 구현하지 않음: API·DB(B), STT·TTS·LLM·aiortc 서버(C), 배포·리버스 프록시·인증서(A), CSP 등 보안 정책 최종안(E), 일정·통합(F). 운영자 웹 UI는 없다(OQ-19, CLI).

### 1.2 전제
| 항목 | 전제 | 근거 |
|---|---|---|
| 지원 환경 | PC 크롬·엣지 최신. 모바일 미지원. 마이크·카메라 필수 | NFR-8.7 |
| 보안 컨텍스트 | 브라우저 마이크는 HTTPS 또는 localhost에서만 | 8.4, R-09 |
| 오프라인 | 운영망은 외부 인터넷 차단. 외부 CDN·웹폰트·스크립트·텔레메트리 금지, 모든 자산을 번들에 포함 | AC-22, R-21 |
| 백엔드(잠정) | FastAPI REST, HttpOnly 쿠키 세션, CSRF 토큰 헤더, WebRTC 시그널링은 HTTP offer/answer, 서버→클라이언트 실시간 이벤트는 데이터채널 | 과제 전제. 보완·반대 근거는 2.7, 2.8, 7장 |
| 배포 형태 | SPA 정적 파일과 `/api`를 같은 출처(same-origin)로 제공. CORS 없음, SameSite 쿠키 | 4.4 |

### 1.3 바꾸지 않는 것
PRD 기능 ID, 우선순위, 수치(본질문 10개, 60/45초, 10초 경고, PDF 10MB, 가중치 합 100, 꼬리질문 본질문당 1·전체 4), 인수 기준, 고정 문구. 충돌·해석 차이는 7장에 둔다.

## 2. 설계 대안 비교

### 2.1 앱 프레임워크
| 기준 | A. React + TypeScript + Vite SPA | B. Next.js(App Router) | C. React Router 프레임워크 모드(SPA 모드) | D. Vue 3 + Vite |
|---|---|---|---|---|
| 운영 런타임 | 정적 파일만, Node 프로세스 없음 | SSR/RSC용 Node 서버 필요. `output: 'export'`면 동적 라우트·미들웨어 제약 | 정적 빌드 가능 | 정적 파일 |
| 오프라인(AC-22) | 번들 밖 요청 없음, 검사 쉬움 | `next/font/google`은 빌드 때 외부 요청, 텔레메트리 기본 켜짐(끄기 필요) | 문제 없음 | 문제 없음 |
| 인증·세션 | FastAPI 쿠키 하나로 끝 | Node BFF와 FastAPI 사이 세션 소유권이 둘로 갈릴 위험 | A와 같음 | A와 같음 |
| 제품 적합성 | 전 화면이 로그인 뒤, SEO 불필요, WebRTC·미디어는 어차피 클라이언트 전용 | SSR 이점이 거의 없음 | 파일 라우팅·로더 장점, 추가 학습 | 생태계 충분. 팀 React 경험을 가정하면 불리 |
| 라이선스 | MIT | MIT | MIT | MIT |

**권장 A.** 운영 구성 요소가 하나 줄고(정적 파일), 오프라인 검증이 단순하다.

### 2.2 라우터·서버 상태
- 라우터: React Router 8(라이브러리 모드) 권장. 대안 TanStack Router(검색 매개변수 타입 안전성)는 팀 숙련도 부담. React Router의 `useBlocker`로 면접·코딩 중 앱 내부 이동을 막는다.
- 서버 상태: TanStack Query v5 권장. 평가 상태·PDF 추출 폴링, 409 뒤 재조회, 캐시 무효화가 많다. 라우터 loader만 쓰는 안은 폴링·재시도를 직접 만들어야 한다.
- 전역 상태 라이브러리(Redux, zustand)는 쓰지 않는다. 서버 상태는 Query, 면접·코딩은 리듀서, 세션·토스트는 React 컨텍스트.

### 2.3 면접·점검 상태 머신 구현
| 기준 | 순수 TS 리듀서 + 효과 실행기 | XState v5 | 전역 저장소(zustand) |
|---|---|---|---|
| 의존성 | 없음 | xstate, @xstate/react(MIT) | zustand(MIT) |
| 장점 | 3.7 전이표와 코드가 1:1, 행 단위 Vitest, 번들 증가 0 | 타이머·취소·병렬 상태 표현력, 시각화 | 단순 |
| 단점 | 타이머·재연결 효과 코드를 직접 작성 | 학습 비용, 팀 전원이 읽기 어려움 | 전이 규칙이 흩어지고 불가능한 상태 조합을 허용 |

**권장: 순수 리듀서** `interviewReducer(state, event) → { state, effects[] }`. 리듀서는 부수 효과를 "명령 목록"으로만 반환하고 `InterviewController`가 실행한다(HTTP, 오디오, 마이크 on/off, 타이머). 복잡도가 커지면 같은 전이표로 XState로 옮긴다.

### 2.4 코드 편집기 (COD-02, AC-22)
| 기준 | CodeMirror 6 | Monaco Editor |
|---|---|---|
| 라이선스 | MIT | MIT |
| 오프라인 번들 | ES 모듈, Vite 기본 설정으로 번들. 외부 요청 없음 | 웹 워커 빌드 설정 필요. `@monaco-editor/react`는 **기본값이 jsDelivr CDN 로더**라 `loader.config({ monaco })`로 로컬 번들을 지정하지 않으면 사내망에서 편집기가 뜨지 않는다 |
| 크기 | 수백 KB급(필요한 확장만) | 수 MB급 |
| Python | `@codemirror/lang-python` 구문 강조·들여쓰기 | Monarch 구문 강조. LSP 없이는 IntelliSense 없음 |
| 한글 IME·접근성 | contenteditable 기반이라 브라우저 IME를 그대로 씀, 스크린리더 지원 | 숨은 textarea 기반, 한글 조합 문제 보고 이력 → 검증 필요 |
| CSP | 런타임 스타일 주입, `EditorView.cspNonce` 지원 | `worker-src`, 스타일 정책 필요 |

**권장 CodeMirror 6.** 담당자 화면의 제출 코드 보기(읽기 전용)도 같은 편집기를 쓴다.

### 2.5 UI 컴포넌트·스타일
| 안 | 내용 | 장점 | 단점 | 라이선스 |
|---|---|---|---|---|
| A. 직접 CSS | CSS Modules + CSS 변수 토큰 + 네이티브 요소(`<dialog>`, `<select>`, `datetime-local`) | 의존성 최소, 크롬·엣지 전용이라 네이티브 품질 충분 | 탭·토스트 접근성을 직접 구현 | — |
| B. 헤드리스 | Radix Primitives 개별 사용 또는 React Aria Components | 검증된 키보드·ARIA | 의존성 추가 | MIT / Apache-2.0 |
| C. 유틸리티 CSS | Tailwind v4(+ shadcn/ui 복사형) | 작업 속도 | 클래스 남용, 팀 규칙 필요 | MIT |
| D. 완성형 | MUI, Ant Design | 컴포넌트 풍부 | 무겁고, MUI 기본 Roboto를 자체 호스팅해야 함, 커스터마이즈 비용 | MIT |

**권장 A + 필요한 위젯(Tabs, Toast, Tooltip)만 Radix.** 글꼴은 시스템 글꼴(`"Malgun Gothic", sans-serif`) 기본, Pretendard(OFL-1.1) woff2 자체 호스팅은 선택. 아이콘은 lucide-react(ISC) 또는 인라인 SVG.

### 2.6 폼 검증
zod 4 + react-hook-form 7(+@hookform/resolvers) 권장. 대안 Valibot은 더 작지만 팀 자료가 적다. 클라이언트 검증은 편의이고 최종 판정은 서버 422이며, 서버 `fields[]` 오류를 `setError`로 같은 칸에 표시한다.

### 2.7 실시간 이벤트·명령 경로
| 기준 | 데이터채널(전제) | SSE | WebSocket(제어 전용) |
|---|---|---|---|
| WebRTC 장애 시 | 함께 끊김 → INT-18 텍스트 모드에서 못 씀 | 독립 | 독립 |
| 누락 복구 | 직접 구현 | `Last-Event-ID` 표준 | 직접 구현 |
| 서버 구현 | aiortc 데이터채널 장기 안정성 미검증(R-10) | FastAPI 스트리밍 응답 | 별도 엔드포인트 |
| X-01 | 위반 아님 | 위반 아님(음성 아님) | 제어만 실으면 위반 아님 |

**권장:** 서버→클라이언트 이벤트는 데이터채널(전제 유지). 단 (1) 모든 화면 상태는 `GET …/interview/state` 스냅샷으로 복구할 수 있고 이벤트는 증분일 뿐이다. (2) 클라이언트→서버 명령(마이크 열림 알림, [답변 완료], 텍스트 답변)은 **HTTP POST**로 보낸다: 응답 코드·멱등성·CSRF·감사 로그가 명확하다. (3) 텍스트 모드·코딩 화면은 HTTP 폴링. (4) R-10 프로토타입에서 데이터채널이 불안정하면 SSE로 바꾼다. 프런트는 `InterviewEventSource` 인터페이스로 감싸 상태 머신을 바꾸지 않는다.

### 2.8 질문 음성(TTS) 전달
| 기준 | A. HTTP 오디오 파일 + `<audio>` | B. WebRTC 서버→클라이언트 오디오 트랙 |
|---|---|---|
| 재생 종료 시점 | `ended` 이벤트로 정확 | 서버 송신 종료 + 지터 버퍼 지연. 클라이언트가 끝을 정확히 모름 |
| 사전 생성(8.2, 1초) | 미리 만든 파일 그대로 재생 | 서버가 파일을 트랙으로 흘려 보냄 |
| WebRTC 장애 시 | 질문 음성 계속 가능(텍스트 모드에서도) | 함께 끊김 |
| 재생 실패 감지(INT-04) | `play()` 거부·`error` 이벤트로 즉시 | 무음과 실패 구분 어려움 |
| PRD 문장 | 8.1 "음성은 브라우저와 서버가 WebRTC로 직접 주고받는다"를 넓게 읽으면 B가 문자 그대로 | |

**권장 A**(7장 이견 1). 프런트는 `QuestionAudioPlayer`(시작·종료·실패 이벤트) 인터페이스로 감싸 B로 정해져도 상태 머신은 같다.

### 2.9 담당자 PDF 원본 보기 (RES-08)
브라우저 내장 PDF 뷰어(`<iframe>`, 오프라인 동작, 크롬 샌드박스)를 권장하고 [새 탭에서 열기]를 함께 둔다. 대안 pdf.js(Apache-2.0)는 워커 포함 수 MB이고 구버전 임의 JS 실행 취약점(CVE-2024-4367) 이력이 있어 쓸 경우 최신판·`isEvalSupported:false`가 필요하다. 내장 뷰어를 쓰려면 PDF 응답에만 `frame-ancestors 'self'`가 필요하다(4.4).

## 3. 권장안과 근거

### 3.1 기술 스택
버전은 2026-10-26 `npm view` 조회값이며 착수 시 lockfile로 고정한다.

| 용도 | 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|---|
| UI | react, react-dom | 19.3.1 | MIT | |
| 언어 | typescript | **6.0.3** | Apache-2.0 | latest 7.0.2는 typescript-eslint 8.72의 peer(`>=4.8.4 <6.1.0`) 밖. 린트 호환 확인 후 상향 |
| 빌드 | vite, @vitejs/plugin-react | 8.3.5, 6.1.2 | MIT | |
| 라우터 | react-router | 8.5.0 | MIT | |
| 서버 상태 | @tanstack/react-query | 5.105.0 | MIT | |
| 폼 | react-hook-form, zod, @hookform/resolvers | 7.91.0, 4.6.7, 6.0.0 | MIT | |
| 편집기 | codemirror, @codemirror/lang-python | 6.0.2, 6.2.1 | MIT | React 래퍼는 직접 작성(약 50줄) 또는 @uiw/react-codemirror(MIT) |
| 위젯 | radix-ui(필요 프리미티브만) | 1.8.1 | MIT | |
| API 타입 | openapi-typescript (+ openapi-fetch 선택) | 7.13.0, 0.17.0 | MIT | 역할별 OpenAPI에서 타입 생성 |
| 단위·컴포넌트 테스트 | vitest, @testing-library/react, msw, jsdom | 5.0.5, 16.3.3, 3.0.3, 30.1.3 | MIT | |
| E2E | @playwright/test | 1.64.1 | Apache-2.0 | Chromium + `msedge` 채널 |
| 접근성 검사 | @axe-core/playwright | 4.14.0 | MPL-2.0 | 개발 의존성만, 배포물 미포함 |
| 모듈 경계 | eslint-plugin-boundaries 또는 dependency-cruiser | 7.3.0, 18.7.0 | MIT | |
| 글꼴(선택) | pretendard | 1.3.9 | OFL-1.1 | 자체 호스팅 |

### 3.2 디렉터리와 모듈 경계 (SEC-04 구조적 보호)
```
frontend/src/
  app/        라우터, 가드(RequireAuth, RequireRole), 레이아웃(기본·집중), QueryClient
  shared/     ui/  http/(client, errors, csrf)  copy/(fixed.ts 고정 문구)  time/(serverClock)  posting/(PostingView)
  api/common/      schema.gen.ts ← openapi.common.json   (auth, system)
  api/candidate/   schema.gen.ts ← openapi.candidate.json
  api/recruiter/   schema.gen.ts ← openapi.recruiter.json
  media/      devices, levelMeter, rtcClient, questionAudio   (지원자 전용)
  pages/common/     Login, Signup, Unsupported, NotFound
  pages/candidate/  Postings, PostingDetail, Consent, Apply, ResumeEditor, MyPage, Result, Guide, Precheck, Interview, Coding, Done
  pages/recruiter/  Home, PostingForm, PostingPreview, EvaluationSettings, Applications, ApplicationDetail, Stats
frontend/e2e/       Playwright 시나리오, fixtures/audio/*.wav
```
- 경계 규칙(ESLint + CI의 dependency-cruiser): `pages/candidate`·`api/candidate`·`media` → `api/recruiter`·`pages/recruiter` import 금지(역방향도 금지). `shared` → `pages`·역할별 `api` 금지. 공고 미리보기처럼 양쪽이 쓰는 표시 컴포넌트는 `shared/posting/PostingView`에 두고 중립 타입만 받는다. 경로명은 추적표의 `pages/candidate/*`, `pages/recruiter/*`와 맞췄다.
- 타입 분리: 백엔드 라우트에 `common`·`candidate`·`recruiter` 태그를 달고, 스크립트가 OpenAPI를 태그별 3개 파일로 나눠 타입을 만든다. 지원자 코드에는 `total_score`, `quotes`, `weights`, `memo` 타입이 아예 존재하지 않는다.
- 번들 분리: 담당자 페이지는 `lazy()` + 청크 이름 접두사 `rc-`. E2E에서 지원자 세션이 `rc-*.js`를 한 번도 요청하지 않는지 검사한다.
- 실제 데이터 보호는 서버 책임(SEC-03·04)이고 프런트 분리는 내부 필드를 실수로 그리는 일을 막는 2차 방어다. 지원자 화면은 총점을 계산하지도 않는다(가중치를 모른다).

### 3.3 라우트 표
API ID는 4.2 참조. 가드 `공개`/`지원자`/`담당자`. `집중` 레이아웃은 상단 메뉴가 없는 면접·코딩 전용 화면.

| 경로 | 화면 | 가드 | 기능 ID | API |
|---|---|---|---|---|
| `/login` | 역할 선택 로그인 | 공개 | USR-01, 04 | AU-1, AU-3 |
| `/signup` | 지원자 가입 | 공개 | USR-02, AC-01 | AU-2 |
| `/unsupported` | 미지원 환경 안내 | 공개 | NFR-8.7 | — |
| `/postings` | 공고 목록(검색·직무 필터 P1) | 지원자 | JOB-01, 08 | CA-1, AU-7 |
| `/postings/:pid` | 공고 상세, [지원하기] | 지원자 | JOB-01, 02, USR-05 | CA-2 |
| `/postings/:pid/apply/consent` | 개인정보 동의 | 지원자 | SEC-01, 02 | CA-3, CA-4 |
| `/postings/:pid/apply/resume` | 이력서 선택: 등록본·PDF 업로드·직접 작성 | 지원자 | RES-01, 02, 04, 06 | CA-5, CA-7, CA-8 |
| `/postings/:pid/apply/review` | 추출 결과 확인·수정·제출 | 지원자 | RES-03, 05, 07 | CA-8, CA-9 |
| `/postings/:pid/apply/complete` | 지원 완료, [면접 안내 보기]·[나중에 하기] | 지원자 | INT-13(나중에 시작) | — |
| `/me/applications` | 마이페이지 지원 현황 | 지원자 | ADM-04 표시, 6.6 | CA-10 |
| `/me/applications/:aid/result` | 결과·피드백 | 지원자 | RPT-03, 04, AC-19 | CA-11 |
| `/me/resume` | 등록 이력서 관리 | 지원자 | RES-07 | CA-5~CA-8 |
| `/me/account` | 회원 탈퇴(P1) | 지원자 | USR-07 | CA-12 |
| `/applications/:aid/guide` | 시작 전 안내 | 지원자 | INT-03 | IV-1, IV-2 |
| `/applications/:aid/precheck` | 환경 점검 | 지원자 | INT-01, 02, 14, 17 | AU-5, AU-6, IV-3~IV-5 |
| `/applications/:aid/interview` | AI 면접(집중) | 지원자 | INT-04~13, 15, 16, 18 | IV-6~IV-14 |
| `/applications/:aid/coding` | 코딩테스트(집중) | 지원자 | COD-01~06 | CD-1~CD-4, IV-15 |
| `/applications/:aid/done` | 응시 완료 | 지원자 | RPT-01 | CA-10 |
| `/recruiter` | 공고 관리 + 요약 카드, 진행 중·마감·임시저장 탭 | 담당자 | ADM-01, JOB-07 | RC-1, RC-2 |
| `/recruiter/postings/new`, `/recruiter/postings/:pid/edit` | 공고 작성·수정·임시저장·게시·마감 | 담당자 | JOB-03~06 | RC-3~RC-8, AU-7 |
| `/recruiter/postings/:pid/preview` | 지원자에게 보이는 모습 미리보기 | 담당자 | JOB-03 | RC-4 |
| `/recruiter/postings/:pid/settings` | 평가 설정(가중치·커스텀 질문) | 담당자 | ADM-05, QST-04, JOB-06 | RC-9, RC-10 |
| `/recruiter/applications` | 지원자 목록(공고·결정 상태 필터) | 담당자 | ADM-02 | RC-11 |
| `/recruiter/applications/:aid` | 지원자 상세(탭: 이력서·리포트·질문별 답변·코딩·결정과 메모) | 담당자 | ADM-03, 04, 06, 07, RES-08, RPT-02 | RC-12~RC-18 |
| `/recruiter/stats` | 통계(P1) | 담당자 | ADM-08 | RC-19 |

가드: 비로그인 → `/login?next=…`. 담당자가 지원자 경로에 들어오면 "담당자 계정으로는 지원자 기능을 사용할 수 없습니다." + [담당자 홈으로](USR-05). 지원자가 `/recruiter`에 들어오면 권한 없음 안내. 진짜 차단은 서버 403이고 화면 가드는 안내용이다(SEC-03). 모바일 UA는 `/applications/*` 진입 시 `/unsupported`로 보낸다.

### 3.4 핵심 사용자 흐름
- 지원자: 로그인 → 공고 목록 → 상세 → [지원하기] → 동의(동의하지 않으면 공고 상세로, SEC-02) → 이력서 선택 → (업로드면 추출 대기) → 확인·수정 → 제출(지원 건 READY) → 지원 완료 → [면접 안내 보기] 또는 마이페이지에서 나중에 → 안내(READY→PREPARING) → 환경 점검 → [면접 시작] → 본질문 10개(+꼬리질문) → (켠 공고만) 코딩테스트 → 완료 → 마이페이지 → 결정 후 결과.
- 담당자: 로그인 → 공고 관리(요약 카드·탭) → 작성·임시저장·미리보기·게시 → 평가 설정 → 지원자 목록 → 상세 검토·메모 → 결정 저장(이력).
- 이미 지원한 공고는 [지원하기] 대신 "이미 지원한 공고입니다" + [지원 현황 보기]. 제출 시 서버 409 `ALREADY_APPLIED`도 같은 문구(JOB-02, AC-04). 마감 공고는 버튼 비활성 "마감된 공고입니다"(7.4).
- 지원 마법사 상태는 메모리(React 컨텍스트)에만 둔다. 이력서 내용을 localStorage·sessionStorage에 쓰지 않는다(공용 PC 개인정보). 새로고침 시 업로드 추출 결과는 서버(CA-8)에서 다시 받고, 직접 작성 중이면 이탈 경고(`beforeunload` + `useBlocker`).
- 동의 화면: 고지 항목(목적·수집 항목·보유 기간·AI 사용과 담당자 결정·사내 처리·공개 범위, SEC-01)을 항목별 체크로 보여 주고, 필수 항목을 모두 체크해야 [다음]이 활성. [동의하지 않음] → 공고 상세. 문구 버전을 화면에 표시하고 법률 검토 전에는 "검토 전 초안"으로 표시한다(OQ-04).

### 3.5 이력서 업로드·추출·수정 (RES-01~07, AC-06)
- 드롭존: 네이티브 드래그 앤 드롭 + `<input type="file" accept=".pdf,application/pdf">`, 키보드로 [파일 선택] 가능. 라이브러리 불필요.
- 업로드 전 즉시 검사: 파일 1개, 확장자 `.pdf`, 크기 ≤ 10MB, 첫 5바이트 `%PDF-`. 문구: "PDF 파일만 올릴 수 있습니다." / "파일 크기는 10MB 이하여야 합니다. (선택한 파일: 12.4MB)" / "파일은 하나만 올릴 수 있습니다." 서버도 같은 검사를 다시 한다. 10MB의 바이트 정의는 서버와 같은 상수로 맞춘다(이견 11).
- 업로드는 진행률 표시를 위해 `XMLHttpRequest` 사용(fetch는 업로드 진행률이 없다). 서버 오류 `NOT_PDF`, `FILE_TOO_LARGE`(프록시 413 포함), `PDF_UNREADABLE` → "PDF 파일이 손상되었거나 열 수 없습니다. 다른 파일을 올려 주세요."
- 추출 대기: `202 {extraction_id}` → 2초 간격 폴링. "이력서 내용을 읽고 있습니다" + [직접 작성으로 바꾸기].
- `NO_TEXT`(스캔 PDF, RES-04): "글자를 읽을 수 없는 PDF입니다(스캔 이미지 등). 직접 입력해 주세요." → 빈 직접 작성 폼. OCR을 흉내 내지 않는다.
- 확인·수정 폼: 성명, 학력[], 경력[], 프로젝트[], 기술 스택(태그), 자격증[](`useFieldArray`). 추출로 채워진 칸에 "PDF에서 가져옴" 표시. 최소 필수 성명 + (학력 1건 또는 경력 1건)(RES-06)을 zod `refine`으로 검사하고 상단 오류 요약과 해당 구역을 함께 표시.
- 스냅샷(RES-07): 제출 시 서버가 지원 건 스냅샷을 고정한다. 등록본은 자동으로 덮어쓰지 않고 "이 내용으로 내 등록 이력서도 바꾸기" 체크박스(기본 해제)로만 갱신한다.

### 3.6 환경 점검 화면 (INT-01, 02, 14, 17, AC-08)
구성: 항목별 체크리스트 카드(확인 전·확인 중·통과·실패 + [다시 확인]), 카메라 미리보기와 마이크 레벨 막대, 하단 [이전 단계](안내 화면으로) · [면접 시작](필수 항목 모두 통과 시에만 활성).

| 항목 | 방법 | 통과 조건 | 실패 안내(요지) | 시작 차단 |
|---|---|---|---|---|
| 브라우저·보안 | `isSecureContext`, `mediaDevices`, `RTCPeerConnection` 존재, UA-CH(`brands`, `mobile`) | 기능 존재 + 데스크톱 | "PC의 크롬 또는 엣지 최신 버전에서 접속해 주세요." / 보안 연결(HTTPS) 안내 | 예(NFR-8.7) |
| 마이크 권한·장치 | `getUserMedia({audio})`, `enumerateDevices`, `devicechange` | 선택 장치 트랙 `live` | 거부(NotAllowedError): 주소창 자물쇠 → 마이크 허용 안내 / 장치 없음(NotFoundError) / 다른 프로그램 사용 중(NotReadableError) | 예 |
| 마이크 입력 레벨 | WebAudio `AnalyserNode` RMS 막대 | "아무 말이나 해 보세요" 뒤 임계값 이상 누적 0.5초(설정값) | "소리가 들어오지 않습니다. 장치 선택과 음소거를 확인해 주세요." | 예 |
| 카메라 권한·장치·영상 | `getUserMedia({video})`, `requestVideoFrameCallback` | 프레임 수신 + 해상도 > 0 | 권한·장치 안내 | 예 |
| 스피커(P1) | 번들에 포함한 샘플 음성 재생, `setSinkId`로 출력 장치 선택 | 사용자가 [들립니다] 선택 | [안 들립니다] → 음량·장치 안내, "질문은 글자로도 표시됩니다" | **아니오**(이견 4) |
| WebRTC 연결 | 오디오 트랙만 넣은 PC → IV-3 offer/answer → `connected` + 데이터채널 open, 10초 제한 | 둘 다 성공 | "면접 서버와 음성 연결을 맺지 못했습니다. 사내 네트워크 연결, VPN·방화벽 설정, 브라우저를 확인해 주세요." | 예(INT-17) |
| 짧은 발화 인식 | "'안녕하세요'라고 말해 보세요" → 데이터채널 `precheck_caption` | 8초 안에 빈 문자열이 아닌 인식 결과(문구 일치는 요구하지 않음) | "말씀하신 내용을 인식하지 못했습니다. 조용한 곳에서 다시 시도해 주세요." | 예(INT-17) |
| AI 서버 | AU-5 | `can_start = true` | LLM·STT 불가: 시작 차단 / TTS 불가: "질문 음성 없이 글자로 진행됩니다" 경고(OQ-08 임시안) | LLM·STT만 |

- 장치를 바꾸면 그 장치에 의존하는 항목(입력 레벨, 발화 인식)을 "확인 전"으로 되돌린다.
- 결과를 IV-4로 서버에 보고한다. 마이크·카메라·스피커는 자가 보고(비디오를 서버로 보내지 않으므로 서버가 확인할 수 없다), WebRTC·발화 인식은 서버가 직접 확인한다. 서버는 start 때 이 기록을 확인한다(INT-01 비고).
- 선택한 deviceId는 sessionStorage에 저장해 면접 화면에서 다시 쓴다(개인정보 아님).
- [면접 시작] → IV-5. 409 `CAPACITY_FULL` → 대화상자에 정확히 **"이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요."** + [마이페이지로]. 미디어와 점검용 PC를 해제하고 상태는 READY로 남는다(INT-13, OQ-16). 409 `QUESTIONS_NOT_READY` → "질문을 준비하고 있습니다" 표시 후 `retry_after` 뒤 자동 재시도. 503 `AI_UNAVAILABLE` → INT-14 안내.
- 점검용 PC는 면접 화면 진입 전에 닫는다. 면접은 IV-8로 새로 연결해 첫 진입과 재진입 경로의 코드를 하나로 만든다.

### 3.7 면접 화면 상태 머신 (INT-04~13, 15, 16, 18)
화면 요소: 상단 진행률 `3 / 10`(꼬리질문이면 같은 번호 옆에 "꼬리질문" 배지, INT-07)·남은 시간·연결 표시, 가운데 질문 텍스트(자막 겸, INT-04)와 상태 문구, 실시간 자막 영역, 본인 카메라 미리보기(좌우 반전, INT-08), [답변 완료] 버튼. 아바타와 3-2-1 카운트다운은 없다.

미디어 원칙:
- 카메라는 별도 `getUserMedia({video})` 스트림을 `<video muted>`에만 연결한다. `RTCPeerConnection`에는 **오디오 트랙만** 넣는다. offer SDP에 `m=video`가 없어야 하며 E2E로 검사한다(서버도 비디오 m-line을 거부하도록 권장). `MediaRecorder` 사용은 린트로 금지(녹화 없음, INT-08·8.5).
- 마이크 트랙은 연결 직후 `enabled=false`(무음 프레임). `answer_opened`를 받았을 때만 `true`, 답변 종료 즉시 `false`. 서버는 개방 시각 이전 프레임을 따로 버린다(AC-09 이중 방어). 오디오 제약은 `echoCancellation`, `noiseSuppression`, `autoGainControl` 켬, 모노.

상태별 표시:
| 상태 | 문구 | 남은 시간 | 마이크 | [답변 완료] |
|---|---|---|---|---|
| BOOT, CONNECTING | "면접 서버에 연결하고 있습니다" | — | off | 숨김 |
| RESUME_GATE | "연결이 끊겨 N번 문항부터 이어서 진행합니다. 진행 중이던 문항은 처음부터 다시 답변합니다." + [이어서 진행] | — | off | 숨김 |
| WAITING(처리 중) | "다음 질문을 준비하고 있습니다"(직전 확정 답변은 유지) | — | off | 비활성 |
| PRESENTING, OPENING | 질문 텍스트 + "질문을 들려 드리고 있습니다" | 제한시간 정지 표시 | off | 비활성 |
| ANSWERING | **"답변해 주세요"** + 실시간 자막 | 카운트다운 | **on** | 활성 |
| ANSWERING(경고) | 위 + "10초 남았습니다"(색·아이콘·문구) | 강조 | on | 활성 |
| CLOSING | "답변을 정리하고 있습니다" | 정지 | off | 비활성 |
| RECONNECTING | 오버레이 "연결이 끊겨 다시 연결하고 있습니다 (2/3)" | 숨김 | off | 비활성 |

전이표(리듀서가 이 표를 그대로 구현한다. 서버 이벤트 스키마는 4.3):
| # | 현재 | 이벤트·조건 | 동작(효과) | 다음 |
|---|---|---|---|---|
| T1 | BOOT | IV-7 스냅샷 INTERVIEWING, 점검 화면의 [면접 시작]으로 막 넘어옴(사용자 제스처 유효) | 미디어 획득, IV-8 연결 | CONNECTING |
| T2 | BOOT | INTERVIEWING·INTERRUPTED인데 새로고침·직접 진입 | — | RESUME_GATE |
| T3 | BOOT | CODING / SUBMITTED·EXPIRED / READY·PREPARING | 해당 화면으로 `replace` 이동 | (이탈) |
| T4 | RESUME_GATE | [이어서 진행] 클릭(자동재생 허용 제스처 확보) | IV-6 resume, 미디어 획득, IV-8 | CONNECTING / 409 `CAPACITY_FULL` → INT-13 문구 |
| T5 | CONNECTING | 미디어 획득 실패 | 원인별 안내 + [환경 점검으로] | MEDIA_ERROR |
| T6 | CONNECTING | PC `connected` + 데이터채널 open + `hello` | 시계 오프셋 갱신, IV-7 재조회 | 스냅샷 phase에 따라 PRESENTING 또는 WAITING |
| T7 | CONNECTING | 10초 안에 연결 실패 | — | RECONNECTING |
| T8 | WAITING | `question`(새 attempt_id) | 질문·진행률 표시, 질문 음성 재생 시작 | PRESENTING |
| T9 | WAITING | `interview_completed {next}` | 마이크·카메라 트랙 정지, PC 종료 | `/coding` 또는 `/done` |
| T10 | WAITING | 15초간 이벤트 없음 | IV-7 재조회(15초마다), 30초를 넘기면 "평소보다 오래 걸리고 있습니다" | WAITING |
| T11 | PRESENTING | 오디오 `ended` | IV-10 open(마이크 열림 알림) | OPENING |
| T12 | PRESENTING | `audio_url` 없음 / `play()` 거부 / 오류 / 3초 안에 재생 미시작 | "질문 음성을 재생하지 못해 글자로 안내합니다" 표시, IV-10 open | OPENING |
| T13 | PRESENTING, OPENING | `answer_opened {deadline_at}`(서버 자동 개방 포함) | 오디오 중지, 마이크 on, 타이머 시작 | ANSWERING |
| T14 | OPENING | 3초간 `answer_opened` 없음 | IV-7 재조회 | 스냅샷대로 |
| T15 | ANSWERING | `caption_partial`·`caption_segment`(attempt 일치) | 자막 갱신. 다른 attempt의 이벤트는 버림 | ANSWERING |
| T16 | ANSWERING | 남은 시간 ≤ 10초 | 경고 표시, 스크린리더 1회 알림 | ANSWERING(경고) |
| T17 | ANSWERING | [답변 완료] 클릭 | 마이크 off, 버튼 비활성, IV-11 done | CLOSING |
| T18 | ANSWERING | 표시상 남은 시간 0 | 마이크 off(요청 없음, 서버가 마감을 확정) | CLOSING |
| T19 | ANSWERING, CLOSING | `answer_finalized {text 또는 no_answer}` | 확정 텍스트 또는 '답변 없음' 표시(INT-15) | WAITING |
| T20 | CLOSING | 5초간 `answer_finalized` 없음 | IV-7 재조회 | 스냅샷대로 |
| T21 | 연결이 필요한 모든 상태 | PC `failed`, `disconnected` 5초 지속, 데이터채널 close, 마이크 트랙 `ended` | 마이크 off, 오디오 중지 | RECONNECTING |
| T22 | RECONNECTING | 새 offer로 재연결 성공(최대 3회, 1·2·4초 간격) | IV-6 resume → IV-7 | PRESENTING/WAITING + "이 문항을 처음부터 다시 진행합니다" |
| T23 | RECONNECTING | 3회 실패 | IV-12 answer-mode=text, 마이크 트랙 정지 | 텍스트 모드 WAITING(INT-18, P1) |
| T24 | 텍스트 모드 ANSWERING | [답변 완료] | IV-13 text-answer | CLOSING |
| T25 | 모든 상태 | `session_superseded` 또는 같은 브라우저 다른 탭 감지(BroadcastChannel) | 미디어 해제, "다른 창에서 면접이 진행 중입니다" | SUPERSEDED |
| T26 | 모든 상태 | `status_changed = EXPIRED` | 미디어 해제, "공고가 마감되어 응시가 종료되었습니다" | 마이페이지 |
| T27 | 모든 상태 | HTTP 401 | 미디어 해제 | `/login?next=현재 경로` |
| T28 | 모든 진행 상태 | 화면 이탈(`visibilitychange`·`blur`) 1초 이상 후 복귀(P2) | 경고 대화상자 "면접 화면을 벗어난 기록이 남았습니다. 면접은 계속 진행 중입니다.", IV-14 기록 | 상태 변화 없음(X-06) |

규칙:
- **침묵으로 끝나지 않는다**: 클라이언트는 음성 활동 감지로 답변을 끝내지 않는다. 종료는 [답변 완료] 또는 서버 마감뿐(INT-05, X-02).
- **서버 시각 기준 마감**(INT-06): `serverClock`가 AU-4를 3회 호출해 왕복 시간이 가장 짧은 표본으로 오프셋을 잡고, `hello`·스냅샷의 `server_now`로 보정한다. 남은 시간 = `deadline_at − (performance.timeOrigin + performance.now() + offset)`, 초 단위 올림 표시. 탭이 다시 보이면 즉시 재계산(백그라운드 타이머 지연 대비). 클라이언트 0초는 표시일 뿐이며 확정은 서버 `answer_finalized`.
- **마이크 열림과 타이머 시작**: TTS 종료 → IV-10으로 서버에 알림 → 서버가 개방 시각과 마감(`개방 + 60초 또는 45초 + 네트워크 유예`)을 정해 `answer_opened`로 알림 → 그때 마이크 on과 "답변해 주세요" 표시. 클라이언트가 알리지 않아도 서버는 `TTS 길이 + 여유`(설정값) 뒤 자동 개방한다. 그래서 오디오를 멈춰 생각할 시간을 늘리는 우회가 불가능하다.
- **TTS 실패**(INT-04): 텍스트만 표시하고 곧바로 개방을 요청한다(읽을 시간을 따로 두지 않음, 이견 5).
- **재진입**(INT-11, AC-13): 상태는 서버 스냅샷만 믿는다. 새로고침 뒤에는 자동재생 정책 때문에 반드시 RESUME_GATE의 클릭을 거친다. 진행 중이던 문항은 처음부터 다시 출제되고(OQ-21), 저장된 답변은 그대로다. 재진입 시 동시 응시 슬롯을 다시 확보하므로 INT-13 문구가 나올 수 있다(OQ-15).
- **텍스트 모드**(INT-18, P1): 데이터채널이 없으므로 IV-7을 2초 간격으로 폴링해 같은 리듀서에 스냅샷 이벤트로 넣는다. 질문 음성은 HTTP로 계속 재생할 수 있다(2.8 A안의 이점). 답변 입력은 `<textarea>` + 같은 서버 마감. 텍스트 모드 제한시간은 PRD에 없다(이견 7).
- **이탈 방지**: 진행 중에는 `beforeunload`와 `useBlocker`로 확인을 받는다. 이탈해도 서버 상태는 INTERRUPTED가 되고 나중에 이어갈 수 있다.
- [원문 외] 면접·코딩 중 `navigator.wakeLock`(화면 꺼짐 방지), 안내 화면의 이어폰 권장 문구.

### 3.8 코딩테스트 화면 (COD-01~06, AC-15)
- 진입: 면접 완료 이벤트 또는 재진입으로만. 상태가 CODING이 아니면 알맞은 화면으로 보낸다. 건너뛰기 버튼은 없다(COD-01).
- 구성: 왼쪽 문제 설명·입출력 예시(팀이 등록한 문제, 일반 텍스트 + 코드 블록으로 표시하고 HTML로 해석하지 않음), 오른쪽 편집기, 상단 언어 선택(Python 하나뿐인 `<select>`, COD-02)·남은 시간(서버 시계)·저장 상태, 하단 실행 결과 패널, [실행]·[최종 제출].
- 자동 저장: 변경 2초 뒤(디바운스)와 변경이 있으면 15초마다 CD-2 `{code, revision}`. `revision`은 단조 증가하며 서버는 더 오래된 저장을 거부(409 `STALE_REVISION`). 마지막 60초는 5초마다, 종료 5초 전 강제 저장. 탭 숨김·`pagehide` 때 `fetch(…, {keepalive:true})`(sendBeacon은 CSRF 헤더를 못 붙임). 표시 "자동 저장됨 14:03:21" / "저장 중" / "저장 실패 · 다시 시도 중"(`aria-live="polite"`). COD-05는 "마지막으로 저장된 코드"를 제출하므로 이 저장 주기가 곧 손실 한도다.
- [실행](COD-03): 실행 중 버튼 비활성. 결과 "정상 작동" / "비정상 작동" + 출력(stdout) + 오류(stderr, 마지막 N줄) + 실행 시간. 여러 번 가능하고 실행만으로 완료되지 않는다. 429 `RUN_BUSY` → "실행 요청이 많습니다. 잠시 후 다시 실행해 주세요." 503 `RUNNER_DISABLED`(샌드박스 점검 실패 시 실행 비활성, R-06) → "지금은 실행 기능을 사용할 수 없습니다. 작성과 최종 제출은 할 수 있습니다."
- [최종 제출](COD-04): `<dialog>` "최종 제출하면 더 이상 코드를 고칠 수 없습니다. 제출할까요?" [취소]·[제출] → CD-4 → 완료 화면. 409 `ALREADY_SUBMITTED`(이미 자동 제출) → 자동 제출 안내 후 완료 화면.
- 시간 만료(COD-05): 0초가 되면 편집기를 읽기 전용으로 바꾸고, CD-1로 상태를 확인한 뒤 "제한시간이 끝나 마지막으로 저장된 코드가 자동 제출되었습니다." 네트워크가 끊겨 있으면 "제한시간이 끝났습니다. 서버에 저장된 마지막 코드가 자동 제출됩니다."
- 비공개 테스트(COD-06): 화면·타입·지원자 응답 어디에도 없다. 실행은 서버가 공개 예시 입력으로만 돌린다. 제출 응답에 점수가 없다.
- 하트비트: 코딩 중에는 데이터채널이 없으므로 IV-15를 30초마다 보낸다(슬롯 점유 판단, OQ-15).
- 편집기 청크는 면접 마지막 문항 대기 중에 미리 받는다(번들 내부 파일이므로 오프라인 무관).

### 3.9 정보 공개 범위 (6.6, SEC-04, RPT-03·04, AC-19)
지원자 결과 화면(CA-11)은 서버가 준 필드만 그린다.

| 지원자 화면 요소 | 결정 전(검토 전) | 결정 후(합격·불합격) | 근거 |
|---|---|---|---|
| 응시 상태, 평가 진행 상태 | 표시 | 표시 | 6.6 |
| 최종 결정 | "검토 전" | 합격·불합격(저장 즉시) | 6.6, ADM-04 |
| 면접 항목별 점수 4개(100점 만점, 소수 첫째 자리) | 응답에 없음 | 표시 | 6.6, OQ-10 |
| 강점·개선점(지원자용으로 따로 만든 것) | 응답에 없음 | 표시 | RPT-03 |
| 코딩테스트 | 응시 중 [실행]의 정상/비정상 작동만 | "정답을 모두 맞힘" / "맞히지 못함"(켠 공고만) | 6.6, OQ-24 |
| 총점, 질문별 점수, 근거 인용, 메모, 가중치, 비공개 테스트 | 없음 | 없음 | AC-19 |
| "이 피드백은 AI가 만든 참고 자료입니다" | — | 피드백 영역 위에 고정 | RPT-04 |

- 구조적 장치: 3.2의 타입·모듈·청크 분리, 지원자 화면은 점수를 계산하지 않음, E2E에서 지원자 세션이 받은 모든 `/api/candidate/*`·`/api/postings*` 응답 JSON을 재귀 검사해 금지 키(`total_score`, `quote`, `quotes`, `weights`, `memo`, `notes`, `hidden_tests`, `rubric`)가 하나라도 있으면 실패(서버 화이트리스트 테스트는 E·B가 1차).

상태 라벨(7.1~7.4):
| 값 | 지원자 표시 | 담당자 표시 |
|---|---|---|
| READY / PREPARING | 응시 전 [면접 보기] / 준비 중 [이어서 준비] | 응시 전 / 준비 중 |
| INTERVIEWING, CODING / INTERRUPTED | 응시 중 / 중단됨 — 둘 다 [이어서 응시] | 면접 중(3/10)·코딩 중 / 중단됨(3/10) |
| SUBMITTED / EXPIRED | 제출 완료 / 공고 마감으로 응시 종료 | 제출됨 / 만료됨 "10문항 중 N문항 응답" |
| 평가 PENDING / RUNNING / DONE / ON_HOLD | 평가 대기 / 평가 중 / 평가 완료 / 이견 6 | 평가 대기 / 평가 중 / 평가 완료 / 평가 보류 |
| 결정 | 검토 전 / 합격 / 불합격 | 검토 전 / 합격 / 불합격 |

완료 화면(RPT-01): "응시가 완료되었습니다. 평가는 서버에서 계속 진행되며, 결과는 담당자의 최종 결정 뒤 마이페이지에서 확인할 수 있습니다." + 평가 상태 + [마이페이지로]. 접근성(WCAG 2.2.1)을 위해 자동 이동은 하지 않는다(이견 10).

### 3.10 담당자 화면 (ADM, JOB, RES-08, RPT-02)
- 홈(ADM-01, JOB-07): 요약 카드(진행 중 공고 수, 검토 대기 지원자 수 — 정의는 B 계약), 탭 진행 중·마감·임시저장, 행마다 지원자 수·응시 완료 수·마감일·[수정]·[지원자 보기]·[마감].
- 공고 작성·수정(JOB-03, 05): 제목, 직무(AU-7 목록), 지원요건, 우대사항, 업무, 정원(표시만), 마감 일시(`datetime-local`, KST 해석), 코딩테스트 사용 체크박스 → 문제 선택(RC-8, OQ-14)·언어 Python(고정 표시)·제한시간(10~180분, 기본 60, OQ-17). [임시저장]은 최소 검증(제목), [게시]는 전체 검증 + 마감 일시가 미래인지. [미리보기]는 `shared/posting/PostingView`로 지원자 화면과 같은 모습. [마감]은 확인 대화상자. [삭제]는 지원자가 있으면 비활성 + "지원자가 있는 공고는 삭제할 수 없고 마감만 할 수 있습니다."(JOB-04).
- 잠금(JOB-06, AC-21): 서버가 준 `locks`가 참이면 코딩 설정·가중치·커스텀 질문 입력을 읽기 전용으로 바꾸고 "첫 응시가 시작되어 바꿀 수 없습니다" 배지. 저장 시 409 `SETTINGS_LOCKED`면 같은 안내 후 재조회.
- 평가 설정(ADM-05, QST-04): 고정 루브릭 4항목(직무 전문성·논리성·구체성·관련성·전달력) + 코딩테스트를 켠 공고만 코딩 항목. 정수 0~100 입력, 합계 표시 "합계 95 / 100"(`aria-live="polite"`), 합이 100일 때만 [저장] 활성, 서버 422 `WEIGHTS_SUM`도 처리. [초기화]는 서버가 준 `defaults`(끈 공고 35/25/25/15, 켠 공고 30/20/20/10/20)를 채우기만 하고 저장은 따로 누른다. 커스텀 질문 입력란 1개, 비우면 자동 채움 안내(6.1, X-09).
- 지원자 목록(ADM-02): 필터 공고·결정 상태(URL 검색 매개변수와 동기화), 열 지원자명·공고·지원일·진행률·평가 상태·총점(평가 완료 전에는 총점 대신 상태)·최종 결정. 기본 정렬은 지원일.
- 지원자 상세(ADM-03): 상단 고정 문구 **"AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다"**. 탭:
  - 이력서: 원본 PDF(내장 뷰어 + 새 탭) + 표준 항목 스냅샷(RES-08, 표준 항목 보기 P1).
  - 리포트(RPT-02): 총점, 항목별 점수, 총평, 강점·개선점 1~3순위, '근거 부족' 개수(6.4), 적용 루브릭·가중치 버전(EVL-08).
  - 질문별 답변: 면접 순서대로 본질문(유형 표시) 아래에 꼬리질문을 묶고, 답변 원문 안에서 인용 위치를 강조(서버가 준 `start`·`end` 오프셋 사용, 프런트가 문자열을 다시 찾지 않음), 항목 점수 1~5·코멘트, 배지 '근거 부족'·'답변 없음'·'텍스트로 답변함'(INT-18)·재출제 횟수(OQ-21)·화면 이탈 기록(INT-16).
  - 코딩: 통과율, 테스트별 결과, 제출 코드(읽기 전용 CodeMirror), LLM 코멘트는 "참고용 · 점수에 반영되지 않음"(COD-07).
  - 결정·메모: 합격·불합격 선택 + [저장] → "저장하면 지원자 마이페이지에 바로 표시됩니다" 확인(6.6). 서버 `can_decide`가 거짓이면 비활성 + "평가가 완료된 뒤 결정할 수 있습니다"(7.3). 변경 이력(누가·언제·무엇에서 무엇으로). 409 `VERSION_CONFLICT` → "다른 담당자가 먼저 변경했습니다. 최신 내용을 불러왔습니다." 메모 목록·작성(ADM-07). '평가 보류'이면 [재평가 실행](ADM-06).
- 리포트는 탭을 열 때만 요청한다. 목록 hover 미리 받기를 금지해 감사 로그의 "리포트 열람"(SEC-08)이 실제 열람과 일치하게 한다.
- P-8: 합격·불합격을 암시하는 점수 색(빨강·초록), "추천" 문구, AI 순위 표시를 두지 않는다. 총점은 중립 색 숫자.
- 통계(ADM-08, P1): 월별 응시자 수, 직무별 평균 점수, 공고·직무별 합격률(결정 완료 건 기준). 표 + CSS 막대로 그리고 차트 라이브러리는 쓰지 않는다.

### 3.11 API 클라이언트·오류 처리 표준
- `apiFetch`: `credentials:'same-origin'`, 변경 요청에 `X-CSRF-Token`, 지정 요청에 `Idempotency-Key`(4.1), JSON, 15초 `AbortController` 타임아웃(업로드 제외), 실패는 `ApiError {status, code, message, fields}`로 정규화.
- Query 기본값: GET만 5xx·네트워크 오류에 2회 재시도(4xx 재시도 없음), 면접·코딩 화면은 `refetchOnWindowFocus:false`, 로그아웃·401 때 `queryClient.clear()`.
- 버튼 활성 여부는 서버가 준 능력 플래그(`can_apply`, `can_start`, `can_resume`, `can_decide`, `can_re_evaluate`, `locks.*`)로만 판단한다. 상태 규칙을 프런트에 다시 구현하지 않는다.

| 상황 | 화면 처리 | 문구 |
|---|---|---|
| 401 | 캐시 비움, 미디어 해제, `/login?next=` | "로그인 시간이 만료되었습니다. 다시 로그인해 주세요." |
| 403 | 역할 안내 화면 | 담당자→지원자 기능: "담당자 계정으로는 지원자 기능을 사용할 수 없습니다." / 그 밖: "이 기능을 사용할 권한이 없습니다." |
| 404 | 찾을 수 없음 화면(다른 회사 리소스도 404, SEC-03) | "요청한 정보를 찾을 수 없습니다." |
| 409 | `code`별 문구 + 관련 쿼리 재조회 | `ALREADY_APPLIED` "이미 지원한 공고입니다." / `SETTINGS_LOCKED` / `CAPACITY_FULL`(INT-13 고정 문구) / `POSTING_CLOSED` "마감된 공고입니다." / `EVAL_NOT_DONE` / `VERSION_CONFLICT` / `STALE_REVISION` / `STATE_CONFLICT`(스냅샷 재조회만) |
| 422 | 해당 칸 오류 + 상단 요약, 첫 오류 칸으로 포커스 | 서버 `fields[].msg` |
| 413·415 | 업로드 영역 오류 | 3.5 문구 |
| 429 | 재시도 안내 | "요청이 많습니다. 잠시 후 다시 시도해 주세요." |
| 503 | 기능별 차단 안내 | `AI_UNAVAILABLE`: "AI 서버를 사용할 수 없어 지금은 면접을 시작할 수 없습니다. 잠시 후 다시 시도해 주세요." / `RUNNER_DISABLED`: 3.8 |
| 네트워크·5xx | [다시 시도] | "서버에 연결할 수 없습니다. 네트워크를 확인한 뒤 다시 시도해 주세요." |

### 3.12 로딩·빈 상태·접근성·문구
- 로딩: 300ms 뒤에만 스켈레톤(깜빡임 방지). 긴 작업(PDF 추출, 질문 준비, 평가)은 무엇을 기다리는지 문장으로 표시. 공통 `EmptyState`·`ErrorState`(제목·설명·행동 버튼).
- 빈 상태: "현재 지원할 수 있는 공고가 없습니다." / "아직 지원한 공고가 없습니다." + [공고 보기] / "아직 지원자가 없습니다." / "임시저장한 공고가 없습니다."
- 접근성(WCAG 2.1 AA 목표): 대비 4.5:1, 보이는 포커스 링, 색만으로 상태를 전달하지 않음, 모든 기능 키보드 조작, `<dialog>`로 포커스 가두기·Esc, 화면 전환 시 h1으로 포커스 이동, `lang="ko"`, `word-break: keep-all`.
- 면접 화면 aria: 새 질문 영역 `aria-live="polite"`, 확정 자막 `aria-live="polite"`, 중간 자막은 `aria-live="off"`(잦은 갱신이 읽기를 방해), 10초 경고는 `role="alert"` 1회, 남은 시간은 매초 읽지 않는다. [답변 완료]로 자동 포커스하지 않는다(키 한 번으로 실수 종료 방지). 스크린리더 음성이 마이크로 들어갈 수 있어 이어폰 권장 안내 [원문 외].
- 문구 원칙: 안내는 "~합니다/~해 주세요", 버튼은 짧은 동작어("지원하기", "답변 완료", "최종 제출", "이어서 진행"), 오류는 "무엇이 문제인지 + 무엇을 하면 되는지" 두 문장 이내, 날짜 "2026년 11월 3일(화) 18:00"(KST), 점수 "82.5점". 용어는 PRD 통일어(지원자·담당자·본질문·꼬리질문·리포트).

고정 문구는 `shared/copy/fixed.ts` 한 곳에 두고 E2E도 같은 상수로 정확히 비교한다.
| 키 | 문구 | 출처 |
|---|---|---|
| CAPACITY_FULL | 이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요. | INT-13 |
| ANSWER_PROMPT | 답변해 주세요 | INT-05 |
| PROGRESS / FOLLOWUP | `{n} / 10` / 꼬리질문 | INT-07 |
| NO_ANSWER / INSUFFICIENT | 답변 없음 / 근거 부족 | INT-06·09, EVL-02 |
| EVAL_ON_HOLD | 평가 보류(담당자 화면) | EVL-06, 7.2 |
| RUN_OK / RUN_FAIL | 정상 작동 / 비정상 작동 | COD-03 |
| CODING_PASS / CODING_FAIL | 정답을 모두 맞힘 / 맞히지 못함 | 6.6(이견 12) |
| CANDIDATE_NOTICE | 이 피드백은 AI가 만든 참고 자료입니다 | RPT-04 |
| RECRUITER_NOTICE | AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다 | ADM-03 |
| DECISION / POSTING | 검토 전·합격·불합격 / 임시저장·진행 중·마감 | 7.3, 7.4 |

### 3.13 테스트 전략
| 층 | 도구 | 대상 | 예 |
|---|---|---|---|
| 정적 | tsc, ESLint(boundaries), dependency-cruiser | 모듈 경계, 금지 API(`MediaRecorder`, 외부 URL 리터럴) | 지원자→담당자 import 0건 |
| 단위 | Vitest + jsdom | 면접·점검·코딩 리듀서(전이표 행마다 1개), `serverClock`, zod 스키마(연락처 `^010-\d{4}-\d{4}$`, 가중치 합), PDF 즉시 검사, 오류 매핑 | T1~T28 |
| 컴포넌트 | Vitest + Testing Library + MSW. 미디어는 `MediaDevices`·`RTCPeerConnection`·`QuestionAudioPlayer`를 가짜 구현으로 주입. 필요하면 Vitest 브라우저 모드(Playwright 공급자) | 폼, 결과 화면, 가중치 합계, 업로드 거부 문구 | |
| E2E | Playwright(Chromium + `msedge` 채널) + 실제 백엔드 test 프로필(가짜 LLM 또는 실모델) | AC-01, 02, 04~06, 08, 09(UI 부분), 10~15, 19(UI 부분), 20, 21, 22(프런트 부분) | |
| 접근성 | @axe-core/playwright | 주요 화면 | serious 이상 0건 |
| 오프라인 | `dist` 외부 URL 검사 + Playwright `context.route`로 localhost 외 요청 차단·기록 | AC-22 | 외부 요청 0건 |
| 수동 | 실제 마이크·카메라·사람 발화 체크리스트 | R-20 | 시연 전 1회 |

음성 E2E:
- 실행 인자: `--use-fake-ui-for-media-stream`(권한 자동 허용), `--use-fake-device-for-media-stream`(가짜 카메라·마이크), `--use-file-for-fake-audio-capture=e2e/fixtures/audio/answer_ko.wav`(로컬 TTS로 만든 합성 답변, 끝에 `%noloop`를 붙이면 반복 안 함). 인자는 브라우저 프로세스 단위라 음성 파일이 다른 시나리오는 파일을 나눠 `test.use({ launchOptions })`로 따로 띄운다. `context.grantPermissions(['microphone','camera'])` 병행.
- 권한 거부·장치 없음(AC-08): `page.addInitScript`로 `getUserMedia`가 `NotAllowedError`·`NotFoundError`를 던지게 하고 [면접 시작] 비활성을 확인. WebRTC 실패는 test 프로필 서버가 offer를 거부하게 해 확인.
- AC-10: "말한 뒤 긴 침묵" WAV로 [답변 완료]를 누르기 전에는 확정되지 않음을 확인. AC-09의 1차 검증은 서버 프레임 게이트 단위 테스트이고 E2E는 TTS 중 마이크 트랙 `enabled=false`와 "답변해 주세요" 표시 시점을 확인.
- AC-11(60초 만료·10초 경고): 실제 시간으로 기다리는 느린 시나리오 1개(야간)와, 서버 test 프로필 시간 배율 훅(4.5)을 쓰는 빠른 시나리오. 클라이언트 시계를 `page.clock`으로 비틀어 오프셋 보정도 확인.
- AC-13: 답변 중 `page.reload()` → RESUME_GATE → 같은 문항 재출제, 이전 답변 유지(API로 확인). WebRTC 끊김은 서버 test 훅으로 강제 종료(CDP 오프라인 에뮬레이션이 WebRTC 미디어에 적용되는지는 미검증).
- AC-14: 동시 응시 상한을 1로 둔 test 프로필에서 두 번째 컨텍스트가 정확한 문구를 받는지.
- INT-08: offer 요청 본문에 `m=video`가 없는지, `pc.getSenders()`에 비디오 트랙이 없는지.
- 자동재생: 대부분 시나리오는 실제 정책대로 두어 RESUME_GATE 클릭이 필요한지까지 검증한다. `--autoplay-policy=no-user-gesture-required`는 쓰지 않는다.

## 4. 다른 에이전트에게 필요한 인터페이스·계약

### 4.1 공통 규약 (B, E)
- 오류 응답: `{"code": "CAPACITY_FULL", "message": "...", "fields": [{"loc": ["weights","logic"], "msg": "..."}]}`. 프런트는 `code`로 분기하고 `message`는 대체 문구로만 쓴다.
- CSRF: 쿠키 이중 제출(HttpOnly 아닌 `csrf_token` 쿠키) 또는 AU-3 응답 중 B·E가 결정. 프런트는 POST·PUT·PATCH·DELETE에 `X-CSRF-Token`.
- 시각: ISO 8601 UTC(`Z`). 시간에 민감한 응답(IV-7, CD-1, 데이터채널 이벤트)에 `server_now`를 넣는다. 화면은 KST.
- 멱등: `Idempotency-Key`(UUID) 지원 요청 — CA-9, IV-10, IV-11, IV-13, CD-4, RC-16.
- 능력 플래그: 상태 규칙은 서버가 계산해 `can_*`·`locks`로 준다(3.11).
- 점수: 서버가 표시 자릿수까지 반올림한 값을 준다(OQ-10). 프런트는 다시 계산하지 않는다.
- OpenAPI: 라우트 태그 `common`·`candidate`·`recruiter`. 지원자 응답은 별도 Pydantic 모델(화이트리스트). 운영에서는 문서 엔드포인트 비활성(E).
- 목록: `?page=&size=` → `{items, total}`.

### 4.2 REST 목록
접두사 `…` = `/api/candidate/applications/{aid}`. 비고의 "추적표"는 `requirements-traceability.md`에 이미 있는 경로.

| ID | 메서드·경로 | 요청 → 응답 핵심 | 오류 code | 비고 |
|---|---|---|---|---|
| AU-1 | POST /api/auth/login | `{role,email,password}` → `{user}` | 401 `INVALID_CREDENTIALS`(역할 불일치 포함, 단일 문구) | 추적표 |
| AU-2 | POST /api/auth/signup | `{name,email,password,phone}` → 201 | 409 `EMAIL_UNAVAILABLE`, 422 | 추적표. 생년월일·성별 필드 없음 |
| AU-3 | GET /api/auth/me, POST /api/auth/logout | → `{id, role, name, company_name?}` | 401 | [신규] |
| AU-4 | GET /api/system/time | → `{now}` | — | [신규] 시계 동기화 |
| AU-5 | GET /api/system/ai-health | → `{llm, stt, tts, can_start}` | — | 추적표 |
| AU-6 | GET /api/system/rtc-config | → `{ice_servers[]}` | — | [신규] OQ-18 |
| AU-7 | GET /api/job-families | → `[{key,label}]` | — | [신규] JOB-08, 공고 폼 |
| CA-1 | GET /api/postings?q=&job_family=&page= | → `items{id,title,company_name,job_family,deadline_at,headcount,coding_enabled}` | — | 추적표 |
| CA-2 | GET /api/postings/{pid} | → 상세 + `my_application{id,status}\|null`, `can_apply` | 404 | 추적표 + 필드 추가 |
| CA-3 | GET /api/consent/current | → `{version, items[{key,title,body,required}]}` | — | 추적표 |
| CA-4 | POST /api/candidate/postings/{pid}/consents | `{version, answers{key:bool}}` → `{consent_id}` | 422 `REQUIRED_CONSENT_MISSING` | [신규] 경로 |
| CA-5 | GET·PUT /api/candidate/resume | 등록본 표준 항목 | 404, 422 | 추적표(PUT) |
| CA-6 | GET /api/candidate/resume-file | 등록본 원본 PDF | 404 | [신규] |
| CA-7 | POST /api/candidate/resume-files | multipart → 202 `{file_id, extraction_id}` | 413 `FILE_TOO_LARGE`, 415 `NOT_PDF`, 422 `PDF_UNREADABLE` | 추적표 |
| CA-8 | GET /api/candidate/resume-extractions/{id} | → `{status: RUNNING\|DONE\|NO_TEXT\|FAILED, fields}` | 404 | [신규] RES-04 |
| CA-9 | POST /api/candidate/postings/{pid}/applications | `{consent_id, resume{source,fields,file_id?}, update_registered}` → 201 `{application_id}` | 409 `ALREADY_APPLIED`·`POSTING_CLOSED`, 403 `ROLE_FORBIDDEN`, 422 | 추적표 |
| CA-10 | GET /api/candidate/applications | → `items{id, posting{title,company_name,deadline_at}, attempt_status, eval_status, decision, answered_main, can_start, can_resume}` | — | [신규] |
| CA-11 | GET …/result | → `{attempt_status, eval_status, decision, feedback?{item_scores[{key,label,score}], strengths[], improvements[], coding_all_passed?}}`. 결정 전에는 `feedback` 키 자체가 없음 | 404 | 추적표 |
| CA-12 | DELETE /api/candidate/me | — | — | 추적표(P1) |
| IV-1 | POST …/interview/prepare | READY→PREPARING, 질문 구성·본질문 TTS 사전 생성 시작 | 409 `POSTING_CLOSED`·`STATE_CONFLICT` | [신규] |
| IV-2 | GET …/interview/guide | → `{main_count, main_sec, followup_sec, followup_max, coding{enabled, time_limit_min, language}}` | — | [신규] INT-03 |
| IV-3 | POST …/precheck/webrtc | `{sdp,type:'offer'}` → `{sdp,type:'answer'}`, 이후 데이터채널 `precheck_caption` | 409 | 추적표 |
| IV-4 | POST …/precheck/result | `{browser, mic, camera, speaker}` | 422 | [신규] |
| IV-5 | POST …/interview/start | → 200 스냅샷 | 409 `CAPACITY_FULL`·`PRECHECK_REQUIRED`·`QUESTIONS_NOT_READY`(+`retry_after`)·`POSTING_CLOSED`, 503 `AI_UNAVAILABLE` | 추적표 |
| IV-6 | POST …/interview/resume | INTERRUPTED→INTERVIEWING, 슬롯 재확보, 진행 중 문항 재출제 | 409 `CAPACITY_FULL`·`NOT_RESUMABLE` | [신규] INT-11 |
| IV-7 | GET …/interview/state | 스냅샷(아래) | 404 | 추적표 |
| IV-8 | POST …/interview/rtc/offer | `{sdp}` → `{sdp}` | 409 `NOT_INTERVIEWING` | [신규] |
| IV-9 | GET …/interview/attempts/{attempt}/audio | 질문 음성(`audio/ogg; codecs=opus` 또는 `audio/wav`), `Cache-Control: no-store` | 404 | [신규] 현재 attempt만 허용 → 다음 질문 선노출 방지 |
| IV-10 | POST …/interview/attempts/{attempt}/open | 마이크 열림 알림 → 202 (확정은 `answer_opened`) | 409 `ATTEMPT_CLOSED` | [신규] |
| IV-11 | POST …/interview/attempts/{attempt}/done | [답변 완료] → 202 | 409 `ATTEMPT_CLOSED`(이미 시간 만료 확정 → 무시) | [신규] |
| IV-12 | POST …/interview/answer-mode | `{mode:'text'}` | 409 | [신규] INT-18 |
| IV-13 | POST …/interview/attempts/{attempt}/text-answer | `{text}` | 409, 422 | [신규] INT-18 |
| IV-14 | POST …/interview/proctor-events | `{type:'hidden'\|'blur', started_at, ended_at}` | — | 추적표(P2) |
| IV-15 | POST …/heartbeat | 데이터채널이 없는 단계(점검·코딩·텍스트 모드) | — | [신규] OQ-15 |
| CD-1 | GET …/coding | → `{problem{title,description,examples[{input,output}]}, languages:['python'], deadline_at, server_now, draft{code,revision,saved_at}, run_available, status}` | 409 | 추적표 |
| CD-2 | PUT …/coding/draft | `{code, revision}` → `{revision, saved_at}` | 409 `STALE_REVISION`·`TIME_OVER` | [신규] |
| CD-3 | POST …/coding/run | `{code, language}` → `{verdict: NORMAL\|ABNORMAL, stdout, stderr, truncated, duration_ms}` | 429 `RUN_BUSY`, 503 `RUNNER_DISABLED` | 추적표 |
| CD-4 | POST …/coding/submit | `{code, revision}` → `{status}` | 409 `ALREADY_SUBMITTED` | 추적표 |
| RC-1 | GET /api/recruiter/dashboard | → `{open_postings, awaiting_review}` | — | 추적표 |
| RC-2 | GET /api/recruiter/postings?status= | → `items{…, applicant_count, completed_count}` | — | 추적표 |
| RC-3 | POST /api/recruiter/postings | DRAFT 생성 | 422 | 추적표 |
| RC-4 | GET /api/recruiter/postings/{pid} | → 전체 + `locks{coding, weights, custom_question}` | 404 | 추적표 |
| RC-5 | PUT /api/recruiter/postings/{pid} | 임시저장 포함 수정 | 409 `SETTINGS_LOCKED`, 422 | 추적표 |
| RC-6 | POST /api/recruiter/postings/{pid}/publish, …/close | 상태 전이 | 422 `PUBLISH_INVALID`, 409 | [신규] 경로 |
| RC-7 | DELETE /api/recruiter/postings/{pid} | | 409 `HAS_APPLICANTS` | 추적표 |
| RC-8 | GET /api/recruiter/coding-problems | 팀 등록 문제 목록 | — | [신규] OQ-14 |
| RC-9 | GET /api/recruiter/postings/{pid}/evaluation-settings | → `{weights, defaults, custom_question, coding_enabled, locked}` | — | [신규] GET |
| RC-10 | PUT /api/recruiter/postings/{pid}/evaluation-settings | `{weights, custom_question}` | 422 `WEIGHTS_SUM`·`WEIGHTS_ITEMS_MISMATCH`, 409 `SETTINGS_LOCKED` | 추적표 |
| RC-11 | GET /api/recruiter/applications?posting_id=&decision=&page= | → `items{id, candidate_name, posting_title, applied_at, attempt_status, progress{answered_main,total_main}, eval_status, total_score\|null, decision}` | — | 추적표 |
| RC-12 | GET /api/recruiter/applications/{aid} | 개요 + `can_decide`, `can_re_evaluate`, 결정 이력 | 404 | 추적표 |
| RC-13 | GET …/{aid}/resume, …/{aid}/resume-file | 표준 항목 스냅샷 / PDF(`inline`, `nosniff`) | 404 | 추적표(파일) + [신규] |
| RC-14 | GET …/{aid}/report | 리포트 + 질문별 `{answer_text, items[{key, score, comment, insufficient, quote{text,start,end}\|null}], is_followup, answer_mode, reasked_count}`, 이탈 기록 | 404 | 추적표 |
| RC-15 | GET …/{aid}/coding-result | 통과율, 테스트별 결과, 제출 코드, LLM 코멘트 | 404 | [신규] 또는 RC-14에 포함 |
| RC-16 | POST …/{aid}/decision | `{decision, expected_version}` | 409 `EVAL_NOT_DONE`·`VERSION_CONFLICT` | 추적표 |
| RC-17 | POST …/{aid}/re-evaluate | | 409 `NOT_ON_HOLD` | 추적표 |
| RC-18 | GET·POST …/{aid}/notes | 메모 목록·작성 | 422 | 추적표(P1) |
| RC-19 | GET /api/recruiter/stats?from=&to= | 월별·직무별·공고별 집계 | — | 추적표(P1) |

IV-7 스냅샷 예:
```json
{ "status": "INTERVIEWING", "mode": "voice", "version": 42, "server_now": "2026-10-26T09:00:00.000Z",
  "total_main": 10, "answered_main": 2, "next_after_interview": "CODING",
  "current": { "attempt_id": "a-17", "main_no": 3, "is_followup": false, "phase": "PRESENTING",
               "text": "…", "audio_url": "/api/…/attempts/a-17/audio", "limit_sec": 60,
               "deadline_at": null, "reasked": false } }
```

### 4.3 WebRTC·데이터채널 (C, B)
- 시그널링: 비트리클 ICE(후보 수집 완료 뒤 SDP 한 번 교환). aiortc는 trickle ICE를 지원하지 않는 것으로 알려져 있어 HTTP offer/answer와 맞는다(C 확인). `iceServers`는 AU-6(사내망 기본 빈 목록).
- 트랜시버: 2.8 A안이면 클라이언트 오디오 `sendonly`, B안이면 `sendrecv`. 비디오 m-line 없음.
- 데이터채널: 클라이언트가 생성, label `control`, ordered·reliable. 메시지 `{type, seq, attempt_id?, …}`, `seq`는 연결 안에서 단조 증가. 지원 건당 활성 연결 1개, 새 연결이 오면 기존 연결에 `session_superseded`를 보내고 닫는다.

| type | 필드 | 시점 |
|---|---|---|
| `hello` | `server_now`, `session_id` | 채널 open 직후 |
| `precheck_caption` | `text` | 점검 발화 인식 결과 |
| `question` | `attempt_id, main_no, is_followup, text, audio_url\|null, limit_sec` | 새 문항(재출제 포함) |
| `answer_opened` | `attempt_id, opened_at, deadline_at, server_now` | 마이크 개방 확정(IV-10 수신 또는 서버 자동 개방) |
| `caption_partial` / `caption_segment` | `attempt_id, segment_no, text` | 인식 중간 결과 / 구간 확정 |
| `answer_finalized` | `attempt_id, reason: done\|timeout, text\|null, no_answer` | 답변 확정 |
| `interview_completed` | `next: CODING\|SUBMITTED` | 마지막 문항 뒤 |
| `status_changed` | `status` | EXPIRED 등 |
| `session_superseded` | — | 다른 연결이 이어받음 |

서버 쪽 요구: 개방 전 프레임 폐기(AC-09), 마감 = 개방 + `limit_sec` + 네트워크 유예(값은 C·B), 개방 요청이 없으면 `TTS 길이 + 여유` 뒤 자동 개방, 끊김 시 진행 중 attempt 무효·재출제와 횟수 기록(OQ-21), `answer_finalized` 전에는 다음 `question`을 보내지 않음, 다른 attempt의 자막 이벤트를 섞지 않음.

### 4.4 정적 호스팅·보안 헤더 (A, E)
- `/api/*` 외 경로는 `index.html`(SPA fallback). `assets/*` 해시 파일은 `immutable` 캐시, `index.html`은 `no-cache`.
- 헤더 제안: `Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; frame-src 'self'; object-src 'none'; frame-ancestors 'none'`(PDF 응답만 `'self'`). CodeMirror는 런타임에 스타일을 넣으므로 `style-src`는 nonce(`EditorView.cspNonce`, 서버가 index.html에 주입) 또는 `'unsafe-inline'` 중 E가 결정. `Permissions-Policy: microphone=(self), camera=(self)`. WebRTC 트래픽은 CSP `connect-src`로 통제되지 않는다.
- HTTPS: 사내 CA 인증서를 응시 PC에 설치해야 마이크가 열린다(R-09). 개발은 Vite `localhost:5173` + `/api` 프록시 → FastAPI `localhost:8000`(같은 출처).
- 빌드: 운영 반입물은 `dist/`뿐. `npm ci`용 패키지 반입(사내 미러 또는 캐시)은 A가 정한다.

### 4.5 테스트 지원 (B, C, E)
test 프로필에서만 존재하고 운영 빌드에는 라우트 자체가 없어야 하는 것: 가짜 LLM·STT 응답, 면접 시간 배율 또는 가짜 시계, 서버 측 WebRTC 강제 종료(`POST /api/test/rtc/{aid}/drop`), 동시 응시 상한 값 변경, 시드 계정·공고·코딩 문제. 운영 비활성 검사는 E.

## 5. PRD 요구사항 대응

| ID | 프런트 대응 | 화면 | 검증 |
|---|---|---|---|
| USR-01, 04 | 역할 라디오, 이메일 형식 검사, 실패 시 단일 문구 "이메일 또는 비밀번호가 올바르지 않습니다." | /login | 단위, E2E |
| USR-02 / AC-01 | 이름·이메일·비밀번호·확인·연락처(자동 하이픈). 생년월일·성별 입력란 없음 | /signup | E2E 입력란 부재 검사 |
| USR-03, 05 / AC-02 | 담당자 가입 화면 없음, 지원자 경로 가드 안내 + 403 처리 | 가드 | E2E |
| USR-06, 07 (P1) | 401 → 로그인 후 `next` 복귀 / 탈퇴 확인 대화상자 | 전역, /me/account | E2E |
| JOB-01, 08 | 목록·상세, 정원은 표시만, 검색·직무 필터는 URL 매개변수 | /postings | 컴포넌트 |
| JOB-02 / AC-04 | 지원 여부 표시, 409 처리 | 상세, 마법사 | E2E |
| JOB-03~07 / AC-21 | 공고 폼·미리보기·게시·마감·삭제 제한·탭·잠금 표시 | /recruiter | E2E |
| RES-01~07 / AC-06 | 3.5 | 마법사, /me/resume | E2E |
| RES-08 | 내장 PDF 뷰어 + 표준 항목(P1) | 상세 이력서 탭 | E2E |
| QST-04 / OQ-02 | 커스텀 질문 1개 | 평가 설정 | 컴포넌트 |
| INT-01, 02, 14, 17 / AC-08 | 3.6 | 점검 | E2E(가짜 장치) |
| INT-03 | 서버 IV-2 값으로 안내 | 안내 | 컴포넌트 |
| INT-04~07, 15 / AC-09~12 | 3.7 전이표, 진행률·꼬리질문 배지, 경고, 자막 | 면접 | 단위, E2E |
| INT-08 | 비디오 미송신·녹화 없음 | 면접 | E2E SDP 검사 |
| INT-09, 10 | 서버 처리. 화면은 '답변 없음' 확정 표시 후 진행 | 면접 | E2E |
| INT-11 / AC-13 | RESUME_GATE, 스냅샷 복구 | 면접 | E2E |
| INT-12 | 제출 후 응시 버튼·경로 차단 | 마이페이지 | E2E |
| INT-13 / AC-14 | 고정 문구, READY 유지, 마이페이지에서 다시 시작 | 점검, 재진입 | E2E |
| INT-16 (P2), INT-18 (P1) | T28 / T21~T24 | 면접 | 단위, E2E(서버 훅) |
| COD-01~06 / AC-15 | 3.8 | 코딩 | E2E |
| COD-07 (P1) | "참고용 · 점수에 반영되지 않음" | 상세 코딩 탭 | 컴포넌트 |
| EVL-02, 06 / ADM-06 | '근거 부족' 배지·개수, '평가 보류'·[재평가 실행] | 상세 | 컴포넌트 |
| RPT-01, 02 | 완료 화면 / 리포트 탭 | | E2E |
| RPT-03, 04, RULE-6.6, SEC-04 / AC-19 | 3.9, 금지 키 검사 | 결과 | E2E |
| ADM-01~05, 07, 08 | 3.10 | 담당자 | E2E |
| ADM-04 / AC-20 | 결정 패널·확인·이력 | 상세 | E2E |
| SEC-01, 02 / AC-05 | 3.4 동의 화면 | 동의 | E2E |
| SEC-03 | 화면 가드는 안내용, 404 화면 | 전역 | E2E |
| SEC-06, NFR-8.4 / AC-22 | 외부 요청 0건, 번들 자산만 | 전체 | 오프라인 검사 |
| SEC-08 | 답변·이력서를 콘솔·브라우저 저장소에 남기지 않음, 리포트 미리 받기 금지 | 전체 | 린트, 리뷰 |
| RULE-6.1, 6.2, 6.4 | 개수·점수는 서버 값만 표시, 프런트 계산 없음 | 면접, 결과 | 단위 |
| STATE-7.1~7.4 | 3.9 상태 라벨 표 | 마이페이지, 목록 | 컴포넌트 |
| NFR-8.2 | 측정 지점: `question` 수신→오디오 `playing`, 답변 종료→다음 `question` | 면접 | E2E 측정 |
| NFR-8.5, 8.7 | 카메라 미저장, 브라우저 저장소 개인정보 없음 / 점검 1단계·모바일 차단 | 점검 | E2E |
| SCOPE-9.2 | 아바타·녹화·시선 분석·AI 합불 추천 UI 없음 | 전체 | 리뷰 |

## 6. 위험과 검증 방법

| ID | 위험 | 등급 | 완화 | 검증 |
|---|---|---|---|---|
| FR-01 | 새로고침·재진입 시 자동재생 차단으로 질문 음성 실패 | M | RESUME_GATE 클릭 필수, `play()` 거부를 TTS 실패로 처리(T12) | 자동재생 플래그 없이 E2E |
| FR-02 | 클라이언트·서버 시계 차이로 남은 시간 표시와 실제 마감 불일치, 말끝 잘림 | H | 서버 마감 + 오프셋 보정 + 네트워크 유예, 클라이언트 0초는 표시만 | `page.clock` 시계 비틀기 E2E, 서버 단위(가짜 시계) |
| FR-03 | aiortc 데이터채널 불안정(R-10)으로 이벤트 누락 → 화면 멈춤 | H | 스냅샷 `version` 재동기화, 상태별 감시 타이머(T10·T14·T20), SSE 대체 경로 | 루프백 프로토타입, 장시간 E2E |
| FR-04 | 재연결·텍스트 모드 전환 UX가 서버 재출제 규칙(OQ-21)과 어긋남 | M | 4.3 서버 요구 명시, 전이표 T21~T24 공동 검토 | 서버 훅으로 강제 끊기 E2E |
| FR-05 | 가짜 미디어 E2E는 실제 마이크·소음·억양을 대표하지 못함(R-20) | M | 합성 음성 + 수동 체크리스트 | 시연 전 수동 점검 |
| FR-06 | 외부 자산 유입(CDN 로더, 웹폰트, 라이브러리 기본값) | H | CSP `default-src 'self'`, `dist` 외부 URL 검사, Monaco 미채택 | AC-22 오프라인 E2E |
| FR-07 | 지원자 화면이 내부 필드를 실수로 표시 | H | 타입·모듈·청크 분리, 금지 키 E2E | 3.9 검사 |
| FR-08 | 자동 저장 간격 때문에 자동 제출 코드가 최신이 아님 | M | 2초 디바운스, 마지막 60초 5초 주기, `keepalive` 저장, 저장 시각 표시 | E2E: 종료 직전 수정 반영 확인 |
| FR-09 | 면접 중 장치 분리(마이크 `ended`) | M | T21로 재연결 경로, 실패 시 텍스트 모드 | 단위 |
| FR-10 | 여러 탭·창 동시 응시로 상태 꼬임 | M | 서버 단일 연결 + `session_superseded`, BroadcastChannel 조기 감지 | E2E 두 탭 |
| FR-11 | 사내 HTTPS 인증서 미설치 PC에서 마이크 불가(R-09) | M | 점검 1단계에서 `isSecureContext` 검사·안내 | 수동 |
| FR-12 | 최신 도구 체인 호환성(TypeScript 7과 typescript-eslint) | L | TS 6.0.x 고정, lockfile | CI |
| FR-13 | 편집기 한글 입력(주석·문자열) 문제 | L | CodeMirror 6, 한글 조합 수동 테스트 | 수동 |
| FR-14 | PDF iframe이 보안 헤더로 차단 | L | PDF 응답만 `frame-ancestors 'self'`, 새 탭 열기 대체 | E2E |
| FR-15 | 백그라운드 탭 타이머 지연으로 경고·남은 시간 표시 오류 | L | 표시 복귀 시 재계산, 서버 기준 확정 | E2E 탭 전환 |

## 7. 이견이 예상되는 지점

1. **질문 음성 경로(C)**: 권장은 HTTP 오디오 파일. 8.1 "음성은 WebRTC로 직접 주고받는다"를 TTS까지 포함한다고 읽으면 서버→클라이언트 오디오 트랙이 된다. 그 경우 재생 종료 시점을 서버가 `tts_end` 이벤트로 알려야 하고 지터 버퍼만큼 마이크 개방 여유가 필요하다. 프런트는 어느 쪽이든 수용(2.8). 팀 해석 필요.
2. **명령 경로(B·C)**: [답변 완료]·개방 알림을 데이터채널이 아닌 HTTP로 보내자는 제안. 데이터채널만 쓰자는 의견이 있으면 최소한 요청별 ack 메시지와 멱등 키를 요구한다.
3. **이벤트 경로(A·C)**: 데이터채널 유지가 기본이나 R-10 결과에 따라 SSE 전환. 결정 시점은 aiortc 프로토타입 직후.
4. **스피커 테스트 차단 여부**: AC-08은 마이크·카메라·WebRTC만 언급하고 INT-02는 P1이다. 권장은 "확인하되 차단하지 않음". INT-01 "모두 통과"에 스피커를 넣자는 의견이 있을 수 있다.
5. **TTS 실패 시 마이크 개방 시점**: 권장은 즉시 개방(PRD에 읽기 시간 규정 없음, 카운트다운 금지). 텍스트를 읽을 시간이 필요하다는 의견이면 팀이 수치를 정해야 하며 그때까지 60초 안에 읽기 시간이 포함된다.
6. **지원자에게 보이는 '평가 보류'**: 6.6은 평가 진행 상태를 지원자에게 바로 보여 주라고 한다. 그러나 '보류'는 채용 결정 보류(X-12에서 없앤 상태)로 오해될 수 있다. 제안: 지원자 화면만 "평가 진행 중(재처리 대기)" 등으로 표시. 팀 문구 결정 필요.
7. **텍스트 모드(INT-18) 제한시간**: PRD는 60/45초를 음성 답변 기준으로 정했다. 텍스트에도 같은 값을 쓰는 것이 기본안이나 타이핑에는 짧다. 팀 결정 필요(값을 임의로 바꾸지 않음).
8. **담당자의 지원자 화면 접근(AC-02)**: 권장은 지원자 경로 전체를 가드로 막고 안내(USR-05). AC-02의 "[지원하기]를 누르면 막힌다"를 문자 그대로 시험하려면 담당자도 공고 상세를 보게 하고 버튼 클릭 시 403 안내를 띄우는 안이 있다. 두 안 모두 서버 403은 같다.
9. **지원자·담당자 진입점 분리 강도(E)**: 권장은 한 SPA + 지연 로딩 청크 + 린트 경계. 보안 쪽이 더 강한 분리를 원하면 Vite 다중 진입점(지원자용·담당자용 HTML 두 개)으로 바꿀 수 있다. 비용은 로그인 후 전체 페이지 이동과 공통 코드 중복 빌드.
10. **완료 화면 자동 이동**: RPT-01 "마이페이지로 보낸다"를 버튼으로 구현(자동 이동 없음). 자동 이동을 원하면 접근성상 충분한 시간과 취소 수단이 필요하다.
11. **10MB 정의(B·E)**: 10×1024×1024 바이트(10MiB)와 10,000,000 바이트 중 하나를 서버·프런트 공통 상수로 정해야 한다. 경계값 테스트가 이 값에 의존한다.
12. **코딩 결과 문구**: 6.6의 "정답을 모두 맞힘 / 맞히지 못함"을 두 값으로 나눠 그대로 쓴다. "정답을 모두 맞히지 못함"으로 읽어야 하는지 팀 확인 필요.
13. **[실행]의 "정상 작동" 정의(B)**: 시간·메모리 제한 안에서 오류 없이 종료한 것인지, 공개 예시 출력까지 일치한 것인지 PRD가 정하지 않았다. 프런트는 서버 `verdict`만 표시하고 예시별 통과·실패는 보여 주지 않는다(6.6이 응시 중 공개 범위를 정상/비정상으로 한정).
14. **코딩테스트 사용 여부를 바꿀 때 가중치(B)**: 항목 목록이 달라지므로 서버가 기본값으로 재설정할지, 422 `WEIGHTS_ITEMS_MISMATCH`로 거부할지 정해야 한다. 프런트는 둘 다 처리 가능하며 바꾼 직후 "가중치를 다시 설정해야 합니다" 안내를 띄운다.
15. **진행 중 응시와 공고 마감(B·F)**: 7.4는 마감 후 응시를 막는다. 면접·코딩 도중 마감 시각이 지나면 즉시 EXPIRED로 끊을지, 진행 중인 응시는 끝까지 허용할지 PRD가 명시하지 않는다. 프런트는 `status_changed`로 둘 다 처리한다(T26).
16. **가입 시 중복 이메일 문구(E)**: USR-04는 로그인 실패만 다룬다. 가입 화면에서 "이미 가입된 이메일입니다"를 보여 주면 계정 존재가 드러난다. 기본안은 "사용할 수 없는 이메일입니다."이며 E의 판단을 따른다.
17. **비밀번호 정책(E·B)**: PRD에 길이·조합 규정이 없다. 프런트는 서버가 정한 규칙을 zod에 그대로 옮기며 임의로 정하지 않는다.
