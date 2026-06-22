import datetime
import uuid
from typing import List, Optional
from typing import Any, Dict

import sqlalchemy as sa
from sqlalchemy import text as sa_text
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, Index, Relationship, SQLModel, text


class Asset(SQLModel, table=True):
    """
    Logical filesystem entry:

        /{tenant}/{collection}/{subpath}/{filename}

    - `subpath` is the remaining "directories" below collection ('' allowed).
    - `latest_document_id` points at the most recent version (an EthelDocument row).
    """
    __tablename__ = "assets"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=text("uuid_generate_v4()"),
        ),
    )

    tenant: str = Field(nullable=False, index=True)
    collection: str = Field(nullable=False)
    subpath: str = Field(nullable=False, sa_column_kwargs={"server_default": ""})
    filename: str = Field(nullable=False)

    latest_document_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="etheldocuments.id",
    )

    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )
    updated_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        Index("ix_assets_tenant_collection", "tenant", "collection"),
        Index("ix_assets_path", "tenant", "collection", "subpath", "filename"),
        sa.UniqueConstraint("tenant", "collection", "subpath", "filename", name="uq_assets_path"),
    )

    documents: List["EthelDocument"] = Relationship(
        back_populates="asset",
        sa_relationship_kwargs={"foreign_keys": "[EthelDocument.asset_id]"},
    )

    latest_document: Optional["EthelDocument"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Asset.latest_document_id]",
            "post_update": True,
        },
    )


class EthelDocument(SQLModel, table=True):
    """
    One concrete stored version of an asset.
    """
    __tablename__ = "etheldocuments"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(PGUUID(as_uuid=True), primary_key=True),
    )

    asset_id: uuid.UUID = Field(foreign_key="assets.id", nullable=False, index=True)
    version: int = Field(nullable=False, default=1)

    title: str
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now, nullable=False)

    content_type: str = Field(
        default="application/octet-stream",
        nullable=False,
        sa_column_kwargs={"server_default": "application/octet-stream"},
    )

    asset: Asset = Relationship(
        back_populates="documents",
        sa_relationship_kwargs={"foreign_keys": "[EthelDocument.asset_id]"},
    )

    text_versions: List["DocumentText"] = Relationship(back_populates="document", cascade_delete=True)

    # NEW: page-image artifacts
    image_sets: List["DocumentImageSet"] = Relationship(back_populates="document", cascade_delete=True)


class DocumentText(SQLModel, table=True):
    """
    Extracted text for a given document version (OCR, BeautifulSoup, etc.).
    """
    __tablename__ = "document_texts"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=text("uuid_generate_v4()"),
        ),
    )

    document_id: uuid.UUID = Field(
        foreign_key="etheldocuments.id",
        nullable=False,
        index=True,
    )

    extractor: str = Field(nullable=False)
    text: Optional[str] = Field(default=None, sa_column=Column(sa.Text))

    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        sa.UniqueConstraint("document_id", "extractor", name="uq_document_texts_document_id_extractor"),
    )

    document: EthelDocument = Relationship(back_populates="text_versions")
    chunk_sets: List["ChunkSet"] = Relationship(back_populates="text_version", cascade_delete=True)


class ChunkSet(SQLModel, table=True):
    __tablename__ = "chunksets"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(PGUUID(as_uuid=True), primary_key=True),
    )

    text_id: uuid.UUID = Field(
        foreign_key="document_texts.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    method: str  # e.g. "recursive_char_1000_100_htmlstrip"

    # IMPORTANT: must match DB: timestamp NOT NULL DEFAULT now()
    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        sa.UniqueConstraint("text_id", "method", name="uq_chunksets_text_id_method"),
    )

    text_version: DocumentText = Relationship(back_populates="chunk_sets")
    chunks: List["Chunk"] = Relationship(back_populates="chunk_set", cascade_delete=True)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(PGUUID(as_uuid=True), primary_key=True),
    )
    chunk_set_id: uuid.UUID = Field(
        foreign_key="chunksets.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )
    text: str
    position: int
    
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    chunk_set: ChunkSet = Relationship(back_populates="chunks")


class DocumentImageSet(SQLModel, table=True):
    """
    Rendered image set for a given document version.

    Uniqueness is enforced as: one set per (document_id, params_hash).
    """
    __tablename__ = "document_image_sets"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=text("uuid_generate_v4()"),
        ),
    )

    document_id: uuid.UUID = Field(
        foreign_key="etheldocuments.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    # Use sa_column for server_default; do NOT pass nullable=... to Field in these cases
    renderer: str = Field(
        default="pymupdf",
        sa_column=Column(sa.Text, nullable=False, server_default=sa_text("'pymupdf'")),
    )
    dpi: int = Field(
        default=150,
        sa_column=Column(sa.Integer, nullable=False, server_default=sa_text("150")),
    )
    image_format: str = Field(
        default="png",
        sa_column=Column(sa.Text, nullable=False, server_default=sa_text("'png'")),
    )
    layout: str = Field(
        default="vertical",
        sa_column=Column(sa.Text, nullable=False, server_default=sa_text("'vertical'")),
    )

    groups: dict = Field(sa_column=Column(JSONB, nullable=False))
    params_hash: str = Field(nullable=False)

    manifest: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "document_id",
            "params_hash",
            name="uq_document_image_sets_document_id_params_hash",
        ),
    )

    document: EthelDocument = Relationship(back_populates="image_sets")
    images: List["DocumentImage"] = Relationship(back_populates="image_set", cascade_delete=True)


class DocumentImage(SQLModel, table=True):
    """
    One rendered image artifact in an image set.

    `pages` stores the explicit page list (e.g. [2,3] or [5,7,8]).
    """
    __tablename__ = "document_images"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=text("uuid_generate_v4()"),
        ),
    )

    image_set_id: uuid.UUID = Field(
        foreign_key="document_image_sets.id",
        nullable=False,
        index=True,
        ondelete="CASCADE",
    )

    position: int = Field(nullable=False)
    pages: dict = Field(sa_column=Column(JSONB, nullable=False))

    s3_key: str = Field(nullable=False)

    mime_type: str = Field(
        default="image/png",
        sa_column=Column(sa.Text, nullable=False, server_default=sa_text("'image/png'")),
    )

    byte_size: Optional[int] = Field(default=None, sa_column=Column(sa.BigInteger))
    width: Optional[int] = Field(default=None)
    height: Optional[int] = Field(default=None)

    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "image_set_id",
            "position",
            name="uq_document_images_image_set_id_position",
        ),
    )

    image_set: DocumentImageSet = Relationship(back_populates="images")


# --- Pods: opaque, capability-style JSONB storage for API memory/state ---

class Pod(SQLModel, table=True):
    """
    Generic JSONB pod storage.

    - Opaque `id` is the capability handle.
    - `owner_api` namespaces data (e.g. "chatapi") so APIs don't interfere.
    - `data` is arbitrary JSON (per-api schema).
    """
    __tablename__ = "pods"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(PGUUID(as_uuid=True), primary_key=True, nullable=False),
    )

    tenant: str = Field(nullable=False, index=True)
    owner_api: str = Field(nullable=False, index=True)
    pod_type: str = Field(nullable=False, index=True)  # e.g. "conversation_context"

    end_user_id: Optional[str] = Field(default=None, index=True)

    rev: int = Field(
        default=1,
        sa_column=Column(sa.Integer, nullable=False, server_default=sa_text("1")),
    )

    data: Dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False),
    )

    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )
    updated_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now,
        sa_column=Column(sa.DateTime(), nullable=False, server_default=sa_text("now()")),
    )

    __table_args__ = (
        Index("ix_pods_tenant_owner", "tenant", "owner_api"),
        Index("ix_pods_owner_user", "owner_api", "end_user_id"),
        Index("ix_pods_owner_type", "owner_api", "pod_type"),
    )
