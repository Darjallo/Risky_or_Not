"""normalize embedding vector indices

Revision ID: d17bf809d47c
Revises: daee4d2eb24c
Create Date: 2025-12-28 14:06:10.557135

"""
from alembic import op

revision = "d17bf809d47c"
down_revision = "daee4d2eb24c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Normalize vector indexes:
    # - Drop any existing index with the canonical name
    # - Recreate using halfvec+halfvec_cosine_ops if available
    # - Otherwise fallback to vector+vector_cosine_ops
    #
    # Also safe if the "small" table isn't present yet.
    op.execute(
        r"""
DO $$
BEGIN
  -- LARGE (3072)
  IF to_regclass('public.embeddings_text_embedding_3_large') IS NOT NULL THEN
    EXECUTE 'DROP INDEX IF EXISTS embeddings_text_embedding_3_large_vector_idx';

    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'halfvec')
       AND EXISTS (SELECT 1 FROM pg_opclass WHERE opcname = 'halfvec_cosine_ops')
    THEN
      EXECUTE 'CREATE INDEX embeddings_text_embedding_3_large_vector_idx
               ON embeddings_text_embedding_3_large
               USING hnsw ((vector::halfvec(3072)) halfvec_cosine_ops)';
    ELSE
      EXECUTE 'CREATE INDEX embeddings_text_embedding_3_large_vector_idx
               ON embeddings_text_embedding_3_large
               USING hnsw (vector vector_cosine_ops)';
    END IF;
  END IF;

  -- SMALL (1536) - only if the table exists
  IF to_regclass('public.embeddings_text_embedding_3_small') IS NOT NULL THEN
    EXECUTE 'DROP INDEX IF EXISTS embeddings_text_embedding_3_small_vector_idx';

    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'halfvec')
       AND EXISTS (SELECT 1 FROM pg_opclass WHERE opcname = 'halfvec_cosine_ops')
    THEN
      EXECUTE 'CREATE INDEX embeddings_text_embedding_3_small_vector_idx
               ON embeddings_text_embedding_3_small
               USING hnsw ((vector::halfvec(1536)) halfvec_cosine_ops)';
    ELSE
      EXECUTE 'CREATE INDEX embeddings_text_embedding_3_small_vector_idx
               ON embeddings_text_embedding_3_small
               USING hnsw (vector vector_cosine_ops)';
    END IF;
  END IF;
END $$;
"""
    )


def downgrade() -> None:
    # Downgrade: drop the normalized indexes (simple and safe)
    op.execute("DROP INDEX IF EXISTS embeddings_text_embedding_3_large_vector_idx;")
    op.execute("DROP INDEX IF EXISTS embeddings_text_embedding_3_small_vector_idx;")

