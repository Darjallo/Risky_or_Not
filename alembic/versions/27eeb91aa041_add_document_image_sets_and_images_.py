"""add document image sets and images tables

Revision ID: 27eeb91aa041
Revises: 5d8869fb0561
Create Date: 2026-01-13 13:15:36.254283

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text as sa_text
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "27eeb91aa041"
down_revision = "5d8869fb0561"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_image_sets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa_text("uuid_generate_v4()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("etheldocuments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("renderer", sa.Text(), nullable=False, server_default=sa_text("'pymupdf'")),
        sa.Column("dpi", sa.Integer(), nullable=False, server_default=sa_text("150")),
        sa.Column("image_format", sa.Text(), nullable=False, server_default=sa_text("'png'")),
        sa.Column("layout", sa.Text(), nullable=False, server_default=sa_text("'vertical'")),
        sa.Column("groups", postgresql.JSONB(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa_text("now()"),
        ),
        sa.UniqueConstraint(
            "document_id",
            "params_hash",
            name="uq_document_image_sets_document_id_params_hash",
        ),
    )
    op.create_index(
        op.f("ix_document_image_sets_document_id"),
        "document_image_sets",
        ["document_id"],
        unique=False,
    )

    op.create_table(
        "document_images",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa_text("uuid_generate_v4()"),
        ),
        sa.Column(
            "image_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_image_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("pages", postgresql.JSONB(), nullable=False),  # e.g. [5,7,8]
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False, server_default=sa_text("'image/png'")),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa_text("now()"),
        ),
        sa.UniqueConstraint(
            "image_set_id",
            "position",
            name="uq_document_images_image_set_id_position",
        ),
    )
    op.create_index(
        op.f("ix_document_images_image_set_id"),
        "document_images",
        ["image_set_id"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("Irreversible for this deployment.")

