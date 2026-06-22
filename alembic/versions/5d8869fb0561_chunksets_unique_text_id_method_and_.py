"""chunksets unique text_id method and created_at timestamp

Revision ID: 5d8869fb0561
Revises: d0ba8ffdd976
Create Date: 2026-01-08 19:38:18.749520

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5d8869fb0561"
down_revision = "d0ba8ffdd976"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Make created_at a proper timestamp (and give it a server default)
    #
    # If you have existing rows with non-parseable created_at strings, this cast can fail.
    # In your current dev DB it's empty, so it's safe.
    op.alter_column(
        "chunksets",
        "created_at",
        existing_type=sa.VARCHAR(),
        type_=sa.DateTime(),  # timestamp without time zone
        nullable=False,
        server_default=sa.text("now()"),
        postgresql_using="created_at::timestamp",
    )

    # 2) Enforce: only one chunkset per (text_id, method)
    op.create_unique_constraint(
        "uq_chunksets_text_id_method",
        "chunksets",
        ["text_id", "method"],
    )


def downgrade():
    raise NotImplementedError("Irreversible for this deployment.")

