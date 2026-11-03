"""Resume OCR (RES-04): scanned PDF pages and resume photos are read by OCR in the extraction job.

- ocr_pages: 0-based pages without a text layer, read by OCR in the job (NULL = none; a photo is [0]).
- extraction_method TEXT / OCR, ocr_confidence (mean line confidence), low_conf_fields (form fields to check).
- Upload files may be PNG / JPEG photos as well as PDF; the storage key keeps the extension.

Revision ID: 0009
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE resume_files
          ADD COLUMN ocr_pages jsonb,
          ADD COLUMN extraction_method text CHECK (extraction_method IN ('TEXT','OCR')),
          ADD COLUMN ocr_confidence numeric(3,2) CHECK (ocr_confidence BETWEEN 0 AND 1),
          ADD COLUMN low_conf_fields jsonb
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE resume_files DROP COLUMN ocr_pages, DROP COLUMN extraction_method, "
               "DROP COLUMN ocr_confidence, DROP COLUMN low_conf_fields")
