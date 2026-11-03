"""Screen spec SC-A05: the consent document as a three-row table (수집 항목 / 이용 목적 / 보관 기간), agreed with one
'동의합니다' choice. Each item carries its purpose and retention period; the record keeps the version shown.

The periods are what the system actually does (camera video is never stored, answers and resumes are deleted after
365 days), not the wireframe's sample wording; the legal review (X-13) may still change them.

Revision ID: 0008
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

VERSION = "consent-v1-draft"
BODY = ("AI 모의면접을 위해 아래 개인정보를 수집·이용합니다. 동의하지 않으면 AI 면접을 진행할 수 없습니다. "
        "모든 데이터와 AI 처리는 회사 내부 서버에서만 이루어지며 외부 AI 서비스로 보내지 않습니다. "
        "합격·불합격은 AI가 아니라 채용담당자가 직접 정합니다.")
ITEMS = [
    {"code": "media", "label": "면접 음성 (카메라 영상은 저장하지 않습니다)",
     "purpose": "답변 텍스트 변환, 음성 분석", "retention": "분석 후 30일", "required": True},
    {"code": "content", "label": "답변 텍스트, 이력서, 자기소개서",
     "purpose": "맞춤 질문 생성, AI 평가와 결과 리포트", "retention": "365일 (법률 검토 후 확정)", "required": True},
    {"code": "contact", "label": "이름, 연락처, 이메일",
     "purpose": "지원 기업의 결과 안내와 일정 연락", "retention": "회원 탈퇴 시까지", "required": True},
]


def upgrade() -> None:
    op.get_bind().execute(
        text("INSERT INTO consent_documents(version, body_md, items, is_draft) VALUES (:v, :b, CAST(:i AS jsonb), true)"),
        {"v": VERSION, "b": BODY, "i": json.dumps(ITEMS, ensure_ascii=False)})


def downgrade() -> None:
    op.execute(f"DELETE FROM consent_documents WHERE version = '{VERSION}' "
               f"AND NOT EXISTS (SELECT 1 FROM consent_records WHERE document_version = '{VERSION}')")
