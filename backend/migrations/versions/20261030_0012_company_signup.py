"""Company signup (screen spec SC-R01): a recruiter signs up with a mail code instead of an operator issuing the account.

- companies.biz_no: 사업자등록번호 as typed (000-00-00000). Not unique: two offices of one business may sign up.
- companies.verification: APPROVED (company-domain mail, or an operator) / PENDING (personal mail, waits for an
  operator; posting stays blocked) / REJECTED. Companies made before this revision were operator-issued: APPROVED.
- companies.evidence_key / evidence_name: the 재직증명서 or 사업자등록증 a personal-mail signup attached.
- signup_codes: one row per code sent. Only the SHA-256 of the 6-digit code is kept; 10 minutes, 5 tries.

Revision ID: 0012
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        ALTER TABLE companies
          ADD COLUMN biz_no text CHECK (biz_no ~ '^[0-9]{3}-[0-9]{2}-[0-9]{5}$'),
          ADD COLUMN verification text NOT NULL DEFAULT 'APPROVED'
            CHECK (verification IN ('APPROVED','PENDING','REJECTED')),
          ADD COLUMN verification_method text CHECK (verification_method IN ('DOMAIN_MAIL','DOCUMENT','OPERATOR')),
          ADD COLUMN evidence_key text,
          ADD COLUMN evidence_name text CHECK (length(evidence_name) <= 200),
          ADD COLUMN verified_at timestamptz
    """)
    op.execute("""
        CREATE TABLE signup_codes (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          email text NOT NULL,
          code_hash bytea NOT NULL,
          expires_at timestamptz NOT NULL,
          attempts int NOT NULL DEFAULT 0,
          used_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_signup_codes_email ON signup_codes(email, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE signup_codes")
    op.execute("""ALTER TABLE companies DROP COLUMN biz_no, DROP COLUMN verification, DROP COLUMN verification_method,
                  DROP COLUMN evidence_key, DROP COLUMN evidence_name, DROP COLUMN verified_at""")
