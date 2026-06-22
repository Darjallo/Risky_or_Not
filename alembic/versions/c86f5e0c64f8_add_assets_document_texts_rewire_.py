"""Add assets + document_texts; rewire chunksets

Revision ID: c86f5e0c64f8
Revises: d17bf809d47c
Create Date: 2025-12-29 21:24:17.925867

"""


from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c86f5e0c64f8"
down_revision: Union[str, Sequence[str], None] = "d17bf809d47c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) Create assets table (logical filesystem overlay) ---
    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant", sa.String(), nullable=False),
        sa.Column("collection", sa.String(), nullable=False),
        sa.Column("subpath", sa.String(), nullable=False, server_default=""),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("latest_document_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant", "collection", "subpath", "filename", name="uq_assets_path"),
    )

    op.create_index("ix_assets_tenant", "assets", ["tenant"])
    op.create_index("ix_assets_tenant_collection", "assets", ["tenant", "collection"])
    op.create_index("ix_assets_path", "assets", ["tenant", "collection", "subpath", "filename"])

    # FK from assets.latest_document_id -> etheldocuments.id (SET NULL if version is deleted)
    op.create_foreign_key(
        "fk_assets_latest_document_id",
        "assets",
        "etheldocuments",
        ["latest_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- 2) Extend etheldocuments to become "document versions" ---
    op.add_column("etheldocuments", sa.Column("asset_id", sa.UUID(), nullable=True))
    op.add_column("etheldocuments", sa.Column("version", sa.Integer(), nullable=True))

    op.create_index("ix_etheldocuments_asset_id", "etheldocuments", ["asset_id"])
    op.create_unique_constraint(
        "uq_etheldocuments_asset_id_version",
        "etheldocuments",
        ["asset_id", "version"],
    )

    op.create_foreign_key(
        "fk_etheldocuments_asset_id",
        "etheldocuments",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # --- 3) Create document_texts table (OCR/bs4/etc text versions) ---
    # Note: text is nullable so legacy chunksets can be represented cleanly.
    op.create_table(
        "document_texts",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("extractor", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_document_texts_document_id", "document_texts", ["document_id"])
    op.create_unique_constraint(
        "uq_document_texts_document_id_extractor",
        "document_texts",
        ["document_id", "extractor"],
    )

    op.create_foreign_key(
        "fk_document_texts_document_id",
        "document_texts",
        "etheldocuments",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # --- 4) Rewire chunksets: document_id -> text_id ---
    op.add_column("chunksets", sa.Column("text_id", sa.UUID(), nullable=True))

    # Create FK + index for new column
    op.create_index("ix_chunksets_text_id", "chunksets", ["text_id"])
    op.create_foreign_key(
        "fk_chunksets_text_id",
        "chunksets",
        "document_texts",
        ["text_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Backfill document_texts for existing chunksets (one synthetic legacy text per document)
    op.execute(
        """
        INSERT INTO document_texts (id, document_id, extractor, text, created_at)
        SELECT uuid_generate_v4(), t.document_id, 'legacy_chunks', NULL, now()
        FROM (
            SELECT DISTINCT document_id
            FROM chunksets
        ) AS t
        ON CONFLICT (document_id, extractor) DO NOTHING;
        """
    )

    # Update chunksets.text_id based on its old document_id
    op.execute(
        """
        UPDATE chunksets cs
        SET text_id = dt.id
        FROM document_texts dt
        WHERE dt.document_id = cs.document_id
          AND dt.extractor = 'legacy_chunks';
        """
    )

    # Now enforce NOT NULL on chunksets.text_id
    op.alter_column("chunksets", "text_id", existing_type=sa.UUID(), nullable=False)

    # Drop old FK + index + column (clean break)
    op.drop_constraint("chunksets_document_id_fkey", "chunksets", type_="foreignkey")
    op.drop_index("ix_chunksets_document_id", table_name="chunksets")
    op.drop_column("chunksets", "document_id")


def downgrade() -> None:
    # Re-add chunksets.document_id
    op.add_column("chunksets", sa.Column("document_id", sa.UUID(), nullable=True))

    # Backfill document_id from document_texts
    op.execute(
        """
        UPDATE chunksets cs
        SET document_id = dt.document_id
        FROM document_texts dt
        WHERE cs.text_id = dt.id;
        """
    )

    op.alter_column("chunksets", "document_id", existing_type=sa.UUID(), nullable=False)

    # Restore index + FK on chunksets.document_id
    op.create_index("ix_chunksets_document_id", "chunksets", ["document_id"])
    op.create_foreign_key(
        "chunksets_document_id_fkey",
        "chunksets",
        "etheldocuments",
        ["document_id"],
        ["id"],
    )

    # Drop new chunksets->document_texts wiring
    op.drop_constraint("fk_chunksets_text_id", "chunksets", type_="foreignkey")
    op.drop_index("ix_chunksets_text_id", table_name="chunksets")
    op.drop_column("chunksets", "text_id")

    # Drop document_texts
    op.drop_constraint("fk_document_texts_document_id", "document_texts", type_="foreignkey")
    op.drop_constraint("uq_document_texts_document_id_extractor", "document_texts", type_="unique")
    op.drop_index("ix_document_texts_document_id", table_name="document_texts")
    op.drop_table("document_texts")

    # Revert etheldocuments extensions
    op.drop_constraint("fk_etheldocuments_asset_id", "etheldocuments", type_="foreignkey")
    op.drop_constraint("uq_etheldocuments_asset_id_version", "etheldocuments", type_="unique")
    op.drop_index("ix_etheldocuments_asset_id", table_name="etheldocuments")
    op.drop_column("etheldocuments", "version")
    op.drop_column("etheldocuments", "asset_id")

    # Drop assets
    op.drop_constraint("fk_assets_latest_document_id", "assets", type_="foreignkey")
    op.drop_index("ix_assets_path", table_name="assets")
    op.drop_index("ix_assets_tenant_collection", table_name="assets")
    op.drop_index("ix_assets_tenant", table_name="assets")
    op.drop_constraint("uq_assets_path", "assets", type_="unique")
    op.drop_table("assets")

