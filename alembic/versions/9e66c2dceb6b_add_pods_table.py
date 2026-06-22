"""add pods table

Revision ID: 9e66c2dceb6b
Revises: 27eeb91aa041
Create Date: 2026-01-22 19:48:12.594057

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "9e66c2dceb6b"
down_revision = "27eeb91aa041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("owner_api", sa.Text(), nullable=False),
        sa.Column("pod_type", sa.Text(), nullable=False),
        sa.Column("end_user_id", sa.Text(), nullable=True),
        sa.Column("rev", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_pods_tenant_owner", "pods", ["tenant", "owner_api"])
    op.create_index("ix_pods_owner_user", "pods", ["owner_api", "end_user_id"])
    op.create_index("ix_pods_owner_type", "pods", ["owner_api", "pod_type"])


def downgrade() -> None:
    op.drop_index("ix_pods_owner_type", table_name="pods")
    op.drop_index("ix_pods_owner_user", table_name="pods")
    op.drop_index("ix_pods_tenant_owner", table_name="pods")
    op.drop_table("pods")
