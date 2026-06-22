"""drop legacy embedding tables

Revision ID: ceffea04e71b
Revises: d887eb3fc69f
Create Date: 2026-01-07 21:27:46.655667

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "ceffea04e71b"
down_revision = "d887eb3fc69f"
branch_labels = None
depends_on = None


def upgrade():
    # Drop legacy pgvector tables (indexes will drop with the tables).
    op.drop_table("embeddings_text_embedding_3_large", if_exists=True)
    op.drop_table("embeddings_text_embedding_3_small", if_exists=True)


def downgrade():
    # Irreversible on purpose.
    raise NotImplementedError("This migration drops legacy embedding tables and is not reversible.")

