"""create embedding tables from model catalog

Revision ID: d0ba8ffdd976
Revises: ceffea04e71b
Create Date: 2026-01-08 13:24:22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "d0ba8ffdd976"
down_revision = "ceffea04e71b"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # space='ada3_large' dim=3072 table='ada3_large'
    op.create_table(
        'ada3_large',
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("vector", Vector(3072), nullable=False),
    )
    # dim > 2000: use halfvec-cast expression index (required for ANN indexes)
    op.execute('CREATE INDEX ix_ada3_large_vector_hnsw ON ada3_large USING hnsw ((vector::halfvec(3072)) halfvec_cosine_ops)')


def downgrade():
    raise NotImplementedError("Irreversible: embedding tables are created from the model catalog.")
