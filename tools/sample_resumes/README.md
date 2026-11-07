# 테스트용 가상 이력서

RAG와 이력서 추출을 IT 외 산업군으로 시험하기 위한 PDF입니다. 인물, 회사, 학교, 수치는 모두 지어낸 것이며 실제 채용에 쓰지 않습니다. 이름이 실제 회사나 사람과 같더라도 우연입니다. 이메일은 `example.com`, 전화번호는 `010-0000-` 형식의 가짜 값입니다.

| 산업군 | 파일 | 직무 | 시험에 쓸 고유 표현 |
|---|---|---|---|
| 제조 | `pdf/resume_manufacturing.pdf` | 자동차 부품 생산기술 | 배터리 케이스 기밀 불량 4,200PPM, 금형 교체 42분, 코어 핀 수명 |
| 유통 | `pdf/resume_retail.pdf` | 신선식품 SCM·수요 계획 | 폐기율 6.8%, 시간당 피킹 168건, 산지 직거래 사과 |
| 의류 | `pdf/resume_apparel.pdf` | 아웃도어 MD·소싱 | 정상 판매율 58%, 리드타임 110일, GRS 재생 폴리에스터 |
| 화학 | `pdf/resume_chemical.pdf` | 정밀화학 공정·공정안전 | 에폭시 수율 87.2%, HAZOP 노드 46개, 총 염소 500ppm |
| 농·축산 | `pdf/resume_agriculture.pdf` | 양돈·한우 농장 운영 | MSY 18.4두, 조사료 자급률 64%, 환기 경보 사고 12두 |
| 금융 | `pdf/resume_finance.pdf` | 기업여신·신용리스크 | 조기경보 포착률 74%, 부동산 PF 1,350억 원, IFRS 9 ECL |

## 다시 만들기

```powershell
.venv\Scripts\python tools\sample_resumes\generate.py
.venv\Scripts\python tools\sample_resumes\check_pdfs.py
```

`check_pdfs.py`는 앱의 추출기(`backend/app/resumes/pdf_extract.py`)로 읽어 페이지 수, 글자 수, 깨진 글자, 고유 문구 누락을 확인합니다. 글꼴은 Windows의 맑은 고딕을 쓰므로 생성은 Windows에서 합니다.
