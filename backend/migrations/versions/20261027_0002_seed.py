"""Seed data: draft consent document, question bank, one sample coding problem.

The consent text is a pre-legal-review draft (X-13, OQ-04). The coding problem is an example only; the team
supplies real problems and test cases (X-10, OQ-14).
Revision ID: 0002
"""
import hashlib
import json
import re
import unicodedata

from alembic import op
from sqlalchemy import text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CONSENT_ITEMS = [
    {"code": "purpose", "required": True,
     "label": "처리 목적: 채용 지원 접수, AI 면접 진행, 평가 리포트 작성과 채용 결정에 개인정보를 이용합니다."},
    {"code": "items", "required": True,
     "label": "수집 항목: 이름, 이메일, 연락처, 이력서 내용, 면접 답변 음성과 텍스트, 코딩테스트 제출 코드. 생년월일·성별·사진은 받지 않습니다."},
    {"code": "retention", "required": True,
     "label": "보유 기간: 답변 음성 원본은 30일, 답변 텍스트·리포트·제출 코드·이력서는 365일 뒤 자동 삭제합니다(법률 검토 후 확정)."},
    {"code": "ai_use", "required": True,
     "label": "AI 사용: AI가 면접 질문을 만들고 답변을 평가하지만, 합격·불합격은 채용 담당자가 직접 결정합니다."},
    {"code": "on_premise", "required": True,
     "label": "처리 위치: 모든 데이터와 AI 처리는 회사 내부 서버에서만 이루어지며 외부 AI 서비스로 보내지 않습니다."},
    {"code": "disclosure", "required": True,
     "label": "결과 공개: 담당자의 최종 결정 후 항목별 점수와 강점·개선점 피드백을 공개합니다. 총점·근거 인용·담당자 메모는 공개하지 않습니다."},
]
CONSENT_BODY = (
    "# 개인정보 수집·이용 동의 (법률 검토 전 초안)\n\n"
    "이 문서는 법률 검토를 거치지 않은 초안입니다. 아래 항목을 모두 확인하고 동의해야 지원할 수 있습니다. "
    "동의하지 않으면 지원이 진행되지 않습니다.\n"
)

PERSONALITY = [
    "팀원과 의견이 달랐던 경험과 그 상황을 어떻게 해결했는지 말씀해 주세요.",
    "가장 어려웠던 목표를 끝까지 해낸 경험을 구체적으로 말씀해 주세요.",
    "실수를 했을 때 이를 발견하고 바로잡은 경험을 말씀해 주세요.",
    "새로운 것을 짧은 시간에 배워야 했던 경험과 배운 방법을 말씀해 주세요.",
    "다른 사람의 피드백을 받아 일하는 방식을 바꾼 경험을 말씀해 주세요.",
    "여러 일이 동시에 몰렸을 때 우선순위를 정한 방법을 사례로 말씀해 주세요.",
    "동료를 도와 팀의 결과를 높인 경험을 말씀해 주세요.",
    "원칙과 일정 사이에서 고민했던 경험과 내린 결정을 말씀해 주세요.",
]
COMPANY = [
    "우리 회사에 지원한 이유와 회사에서 이루고 싶은 목표를 말씀해 주세요.",
    "이 직무에서 입사 후 1년 동안 기여할 수 있는 일을 구체적으로 말씀해 주세요.",
    "우리 회사가 하는 사업에서 관심 있는 부분과 그 이유를 말씀해 주세요.",
    "회사의 업무 방식이 본인과 맞지 않을 때 어떻게 적응할지 말씀해 주세요.",
]
JOB = {
    "BACKEND": [
        "트래픽이 늘었을 때 API 응답 지연을 줄이기 위해 어떤 순서로 원인을 찾겠습니까?",
        "데이터베이스 트랜잭션 격리 수준을 선택했던 경험이나 기준을 말씀해 주세요.",
        "외부 서비스 장애가 내 서비스로 번지지 않게 하는 방법을 설명해 주세요.",
        "캐시를 도입할 때 데이터 일관성 문제를 어떻게 다루겠습니까?",
        "API를 설계할 때 하위 호환성을 지키는 방법을 말씀해 주세요.",
        "운영 중 장애를 분석하고 재발을 막았던 경험을 말씀해 주세요.",
    ],
    "FRONTEND": [
        "화면 렌더링 성능 문제를 찾고 개선했던 방법을 말씀해 주세요.",
        "상태 관리 방식을 선택할 때 고려하는 기준을 설명해 주세요.",
        "웹 접근성을 높이기 위해 적용했던 방법을 말씀해 주세요.",
        "API 오류와 로딩 상태를 사용자에게 보여 주는 방식을 설명해 주세요.",
        "컴포넌트를 재사용 가능하게 설계했던 경험을 말씀해 주세요.",
        "브라우저 호환성 문제를 해결했던 경험을 말씀해 주세요.",
    ],
    "DATA": [
        "데이터 품질 문제를 발견하고 해결했던 경험을 말씀해 주세요.",
        "데이터 파이프라인이 실패했을 때 복구하는 방법을 설명해 주세요.",
        "분석 결과를 비전문가에게 설명했던 경험을 말씀해 주세요.",
        "대용량 데이터를 처리할 때 성능을 높였던 방법을 말씀해 주세요.",
    ],
    "AI_ML": [
        "모델 성능이 운영 환경에서 떨어졌을 때 원인을 찾는 방법을 설명해 주세요.",
        "학습 데이터의 편향을 확인하고 줄였던 경험을 말씀해 주세요.",
        "모델을 서비스에 배포할 때 지연 시간과 정확도를 함께 맞춘 방법을 말씀해 주세요.",
        "평가 지표를 고른 기준과 그 한계를 설명해 주세요.",
    ],
    "DEVOPS": [
        "배포 자동화 파이프라인을 만들거나 개선한 경험을 말씀해 주세요.",
        "모니터링 지표와 알림 기준을 정했던 방법을 설명해 주세요.",
        "인프라 비용을 줄이면서 안정성을 유지한 경험을 말씀해 주세요.",
        "장애 대응 절차를 만들거나 개선한 경험을 말씀해 주세요.",
    ],
}

PROBLEM = {
    "title": "구간 합 구하기",
    "statement": (
        "정수 N개로 이루어진 수열과 질의 Q개가 주어집니다. 각 질의 (i, j)에 대해 i번째부터 j번째 수까지의 합을 출력하세요.\n\n"
        "입력: 첫 줄에 N과 Q, 둘째 줄에 N개의 정수, 이어서 Q줄에 i j (1 ≤ i ≤ j ≤ N).\n"
        "출력: 질의마다 합을 한 줄에 하나씩 출력합니다.\n"
        "제한: 1 ≤ N, Q ≤ 100,000, 각 수의 절댓값 ≤ 1,000"
    ),
    "cases": [
        ("PUBLIC", "5 3\n1 2 3 4 5\n1 3\n2 4\n5 5\n", "6\n9\n5\n"),
        ("PUBLIC", "3 1\n-1 0 1\n1 3\n", "0\n"),
        ("HIDDEN", "1 1\n7\n1 1\n", "7\n"),
        ("HIDDEN", "4 2\n-5 5 -5 5\n1 4\n2 3\n", "0\n0\n"),
        ("HIDDEN", "6 3\n10 20 30 40 50 60\n1 6\n3 5\n6 6\n", "210\n120\n60\n"),
    ],
}


def norm_hash(s: str) -> bytes:
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("INSERT INTO consent_documents(version, body_md, items, is_draft) "
                      "VALUES ('consent-v0-draft', :b, CAST(:i AS jsonb), true)"),
                 {"b": CONSENT_BODY, "i": json.dumps(CONSENT_ITEMS, ensure_ascii=False)})
    rows = [("PERSONALITY", None, q) for q in PERSONALITY] + [("COMPANY", None, q) for q in COMPANY]
    rows += [("JOB", fam, q) for fam, qs in JOB.items() for q in qs]
    for kind, fam, q in rows:
        conn.execute(text("INSERT INTO question_bank(kind, job_family, text, text_norm_hash, source) "
                          "VALUES (:k, :f, :t, :h, 'SEED')"), {"k": kind, "f": fam, "t": q, "h": norm_hash(q)})
    pid = conn.execute(text("INSERT INTO coding_problems(title, statement_md, time_limit_ms, memory_mb) "
                            "VALUES (:t, :s, 2000, 256) RETURNING id"),
                       {"t": PROBLEM["title"], "s": PROBLEM["statement"]}).scalar_one()
    for n, (vis, inp, out) in enumerate(PROBLEM["cases"], start=1):
        conn.execute(text("INSERT INTO coding_testcases(problem_id, ordinal, visibility, input, expected_output) "
                          "VALUES (:p, :n, :v, :i, :o)"), {"p": pid, "n": n, "v": vis, "i": inp, "o": out})


def downgrade() -> None:
    op.execute("DELETE FROM coding_testcases; DELETE FROM coding_problems; DELETE FROM question_bank WHERE source='SEED'; "
               "DELETE FROM consent_documents WHERE version='consent-v0-draft'")
