# AI 모의면접 플랫폼 (MVP)

구직자가 직무와 난이도를 골라 AI 면접관과 모의면접을 보고, 루브릭 기반 피드백 리포트를 받는 웹 서비스입니다. 관리자는 회원, 질문 데이터, 면접 결과를 관리합니다.

```
AI 음성 질문(TTS) → 실시간 음성 답변(WebRTC) → 꼬리질문 → 루브릭 기반 피드백 리포트
```

- 1인 포트폴리오 프로젝트, 개발 기간 3주
- 유료 API 없이 Hugging Face 모델을 내려받아 로컬에서 실행 (GTX 1660 Super 6GB 기준)

## 기술 스택

| 구분 | 기술 |
|---|---|
| 프론트엔드 | React + Vite, TypeScript |
| 백엔드 | FastAPI, SQLAlchemy, Pydantic |
| DB / 벡터 DB | PostgreSQL 16 + pgvector |
| 실시간 음성 | WebRTC (브라우저) + aiortc (서버), silero-vad |
| LLM | Qwen2.5-7B-Instruct (GGUF Q4_K_M) + llama-cpp-python |
| STT | faster-whisper |
| TTS | MeloTTS 한국어 |
| 임베딩 | BAAI/bge-m3 |

## 문서

- [기능정의서](docs/기능정의서.md): 기능 목록, 기능 상세, 평가 루브릭, 데이터 모델, API, 일정
- [화면정의서](docs/화면정의서.md): 화면 목록, 화면 흐름도, 화면별 와이어프레임과 구성 요소
