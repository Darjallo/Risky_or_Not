#!/usr/bin/env python3
"""
update_embedding_dbs.py

Reads embedding spaces from the model catalog (k8s/model_catalog.yaml),
checks which embedding tables exist in Postgres, and generates a new Alembic
revision to create missing tables + vector indexes.

Novice-friendly behavior:
- If Postgres is not reachable, prints port-forward instructions and exits.

Index strategy:
- dimension <= 2000:
    - use normal pgvector operator class on the vector column (fastest, no quantization)
- dimension > 2000:
    - use an expression index that casts vector -> halfvec(dim) and uses halfvec_*_ops
      (required to avoid the 2000-dim ANN index limit for vector)

Usage:
  ./update_embedding_dbs.py
  ./update_embedding_dbs.py --host localhost
  ./update_embedding_dbs.py --index ivfflat --lists 200
  ./update_embedding_dbs.py --distance l2
  ./update_embedding_dbs.py --no-db-check
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import os
import re
import socket
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

VALID_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _port_reachable(host: str, port: str | int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except Exception:
        return False


def _run_cmd(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return out.decode("utf-8", errors="replace").strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Command failed (exit {e.returncode}): {' '.join(cmd)}\n"
            f"{e.output.decode('utf-8', errors='replace')}"
        ) from e


def _detect_kubectl(cmd_override: Optional[str]) -> List[str]:
    if cmd_override:
        return cmd_override.split()

    try:
        _run_cmd(["kubectl", "version", "--client=true"])
        return ["kubectl"]
    except Exception:
        pass

    try:
        _run_cmd(["microk8s", "kubectl", "version", "--client=true"])
        return ["microk8s", "kubectl"]
    except Exception:
        pass

    raise RuntimeError(
        "Could not find a working kubectl. Install/configure kubectl, or pass --kubectl-cmd "
        "(e.g. 'kubectl' or 'microk8s kubectl')."
    )


def kubectl_get_secret_decoded(
    kubectl_cmd: List[str],
    namespace: str,
    secret_name: str,
    key: str,
) -> str:
    jsonpath = f"{{.data.{key}}}"
    raw_b64 = _run_cmd(
        kubectl_cmd
        + ["get", "secret", "-n", namespace, secret_name, "-o", f"jsonpath={jsonpath}"]
    )
    if not raw_b64:
        raise RuntimeError(
            f"Secret {secret_name} in namespace {namespace} has no key {key!r} (or is empty)."
        )
    try:
        return base64.b64decode(raw_b64).decode("utf-8", errors="strict")
    except Exception as e:
        raise RuntimeError(f"Failed to base64-decode secret {secret_name}:{key}") from e


def normalize_db_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    return url


def _host_port_hint(args: argparse.Namespace) -> Tuple[str, str]:
    host = (
        args.host
        or os.getenv("ETHELFLOW_POSTGRES_HOST")
        or os.getenv("POSTGRES_HOST")
        or "postgres"
    )
    port = (
        args.port
        or os.getenv("ETHELFLOW_POSTGRES_PORT")
        or os.getenv("POSTGRES_PORT")
        or "5432"
    )
    return host, port


def _require_db_reachable_or_exit(args: argparse.Namespace) -> None:
    host, port = _host_port_hint(args)
    ok_configured = _port_reachable(host, port)
    ok_local = _port_reachable("localhost", port)

    if ok_configured:
        return
    if ok_local:
        if args.host is None:
            args.host = "localhost"
        return

    print(
        'Run\n'
        'kubectl port-forward -n default svc/postgres 5432:5432\n'
        'in another terminal (or similar command for your k8s),\n'
        'then run again in this terminal.\n'
    )
    raise SystemExit(1)


def read_catalog_from_k8s_configmap(path: Path) -> Dict[str, Any]:
    outer = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(outer, dict):
        raise ValueError(f"{path} does not parse to a YAML mapping")

    data = outer.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{path} has no top-level 'data:' mapping")

    inner_text = data.get("catalog.yaml")
    if not isinstance(inner_text, str) or not inner_text.strip():
        raise ValueError(f"{path} does not contain data['catalog.yaml']")

    inner = yaml.safe_load(inner_text)
    if not isinstance(inner, dict):
        raise ValueError("catalog.yaml content does not parse to a YAML mapping")

    return inner


def read_catalog(path: Path) -> Dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        isinstance(doc, dict)
        and isinstance(doc.get("data"), dict)
        and "catalog.yaml" in doc["data"]
    ):
        return read_catalog_from_k8s_configmap(path)

    if not isinstance(doc, dict):
        raise ValueError(f"{path} does not parse to a YAML mapping")

    return doc


def extract_embedding_spaces(catalog: Dict[str, Any]) -> List[Tuple[str, int, str]]:
    embeddings = catalog.get("embeddings", {})
    if not isinstance(embeddings, dict):
        raise ValueError("catalog.embeddings must be a mapping")

    spaces = embeddings.get("spaces", {})
    if not isinstance(spaces, dict):
        raise ValueError("catalog.embeddings.spaces must be a mapping")

    out: List[Tuple[str, int, str]] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict):
            eprint(f"WARNING: embeddings.spaces.{space_name} is not a mapping; skipping")
            continue

        dim = spec.get("dimension")
        store = spec.get("store", {})
        table = store.get("table") if isinstance(store, dict) else None

        if dim is None or table is None:
            eprint(
                f"WARNING: embeddings.spaces.{space_name} missing dimension or store.table; skipping"
            )
            continue

        if not isinstance(dim, int) or dim <= 0:
            raise ValueError(f"Invalid dimension for space {space_name!r}: {dim!r}")

        if not isinstance(table, str) or not table.strip():
            raise ValueError(f"Invalid store.table for space {space_name!r}: {table!r}")

        table = table.strip()
        if not VALID_IDENT_RE.match(table):
            raise ValueError(
                f"Unsafe table name {table!r} for space {space_name!r}. "
                f"Use only letters/digits/underscore and don't start with a digit."
            )

        out.append((space_name, dim, table))

    return out


def build_db_params_from_env_or_k8s(args: argparse.Namespace) -> Tuple[str, str, str, str, str]:
    host, port = _host_port_hint(args)

    db = os.getenv("ETHELFLOW_POSTGRES_DB") or os.getenv("POSTGRES_DB")
    user = os.getenv("ETHELFLOW_POSTGRES_USER") or os.getenv("POSTGRES_USER")
    pwd = os.getenv("ETHELFLOW_POSTGRES_PASSWORD") or os.getenv("POSTGRES_PASSWORD")

    if db and user and pwd:
        return host, port, db, user, pwd

    kubectl_cmd = _detect_kubectl(args.kubectl_cmd)
    if not db:
        db = kubectl_get_secret_decoded(
            kubectl_cmd, args.kube_namespace, args.kube_secret, "POSTGRES_DB"
        )
    if not user:
        user = kubectl_get_secret_decoded(
            kubectl_cmd, args.kube_namespace, args.kube_secret, "POSTGRES_USER"
        )
    if not pwd:
        pwd = kubectl_get_secret_decoded(
            kubectl_cmd, args.kube_namespace, args.kube_secret, "POSTGRES_PASSWORD"
        )

    return host, port, db, user, pwd


def build_db_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return normalize_db_url(args.database_url)

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return normalize_db_url(env_url)

    host, port, db, user, pwd = build_db_params_from_env_or_k8s(args)
    return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"


def table_exists(conn, table: str, schema: str = "public") -> bool:
    res = conn.exec_driver_sql("SELECT to_regclass(%s)", (f"{schema}.{table}",))
    return res.scalar_one_or_none() is not None


def find_alembic_ini(root: Path, override: Optional[str]) -> Path:
    if override:
        p = (root / override).resolve() if not Path(override).is_absolute() else Path(override)
        if not p.exists():
            raise FileNotFoundError(f"--alembic-ini points to missing file: {p}")
        return p

    for c in (root / "alembic.ini", root / "ethelflow" / "alembic.ini"):
        if c.exists():
            return c.resolve()

    raise FileNotFoundError(
        "Could not find alembic.ini. Provide --alembic-ini, or place alembic.ini in the project root."
    )


def _ops_classes(distance: str) -> Tuple[str, str]:
    """
    Returns (vector_opclass, halfvec_opclass) for the chosen distance.
    """
    if distance == "cosine":
        return "vector_cosine_ops", "halfvec_cosine_ops"
    if distance == "l2":
        return "vector_l2_ops", "halfvec_l2_ops"
    raise ValueError("distance must be 'cosine' or 'l2'")


def _index_lines_for_table(
    *,
    table: str,
    dim: int,
    index_kind: str,
    distance: str,
    lists: int,
) -> List[str]:
    """
    Generates lines to create an index.
    - For dim <= 2000, use op.create_index on the raw vector column.
    - For dim > 2000, use op.execute with a halfvec cast expression index.
    """
    vector_op, halfvec_op = _ops_classes(distance)
    idx_name = f"ix_{table}_vector_{index_kind}"

    if index_kind == "none":
        return ["# (no vector index requested)"]

    if dim <= 2000:
        # Native index on vector column
        if index_kind == "hnsw":
            return [
                "op.create_index(",
                f"    {idx_name!r}, {table!r}, ['vector'],",
                "    postgresql_using='hnsw',",
                f"    postgresql_ops={{'vector': {vector_op!r}}},",
                ")",
            ]
        if index_kind == "ivfflat":
            return [
                "op.create_index(",
                f"    {idx_name!r}, {table!r}, ['vector'],",
                "    postgresql_using='ivfflat',",
                f"    postgresql_ops={{'vector': {vector_op!r}}},",
                f"    postgresql_with={{'lists': {int(lists)}}},",
                ")",
            ]
        raise ValueError("index_kind must be 'hnsw', 'ivfflat', or 'none'")

    # dim > 2000: expression index on vector::halfvec(dim)
    if index_kind == "hnsw":
        sql = (
            f"CREATE INDEX {idx_name} ON {table} "
            f"USING hnsw ((vector::halfvec({dim})) {halfvec_op})"
        )
        return [
            "# dim > 2000: use halfvec-cast expression index (required for ANN indexes)",
            f"op.execute({sql!r})",
        ]

    if index_kind == "ivfflat":
        sql = (
            f"CREATE INDEX {idx_name} ON {table} "
            f"USING ivfflat ((vector::halfvec({dim})) {halfvec_op}) "
            f"WITH (lists = {int(lists)})"
        )
        return [
            "# dim > 2000: use halfvec-cast expression index (required for ANN indexes)",
            f"op.execute({sql!r})",
        ]

    raise ValueError("index_kind must be 'hnsw', 'ivfflat', or 'none'")


def generate_revision_file(
    *,
    alembic_ini: Path,
    message: str,
    down_revision: str,
    create_specs: List[Tuple[str, int, str]],
    index_kind: str,
    distance: str,
    lists: int,
) -> Path:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(alembic_ini))
    script_dir = ScriptDirectory.from_config(cfg)

    versions_dir = Path(script_dir.versions).resolve()
    if not versions_dir.exists():
        raise FileNotFoundError(f"Alembic versions dir not found: {versions_dir}")

    revision_id = uuid.uuid4().hex[:12]
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    blocks: List[str] = []
    for space_name, dim, table in create_specs:
        lines: List[str] = []
        lines.append(f"# space={space_name!r} dim={dim} table={table!r}")
        lines.extend(
            [
                "op.create_table(",
                f"    {table!r},",
                "    sa.Column(",
                '        "chunk_id",',
                "        postgresql.UUID(as_uuid=True),",
                '        sa.ForeignKey("chunks.id", ondelete="CASCADE"),',
                "        primary_key=True,",
                "        nullable=False,",
                "    ),",
                f'    sa.Column("vector", Vector({dim}), nullable=False),',
                ")",
            ]
        )
        lines.extend(
            _index_lines_for_table(
                table=table,
                dim=dim,
                index_kind=index_kind,
                distance=distance,
                lists=lists,
            )
        )
        blocks.append("\n".join(lines))

    body = "\n\n".join(textwrap.indent(b, "    ") for b in blocks)

    fname_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", message.strip().lower()).strip("_")
    revision_filename = versions_dir / f"{revision_id}_{fname_slug}.py"

    content = f'''"""{message}

Revision ID: {revision_id}
Revises: {down_revision}
Create Date: {now}
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "{revision_id}"
down_revision = "{down_revision}"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

{body}


def downgrade():
    raise NotImplementedError("Irreversible: embedding tables are created from the model catalog.")
'''
    revision_filename.write_text(content, encoding="utf-8")
    return revision_filename


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Alembic revision to create missing embedding tables.")
    parser.add_argument("--catalog", default="k8s/model_catalog.yaml")
    parser.add_argument("--alembic-ini", default=None)
    parser.add_argument("--database-url", default=None)

    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)

    parser.add_argument("--kube-namespace", default="default")
    parser.add_argument("--kube-secret", default="postgres-secret")
    parser.add_argument("--kubectl-cmd", default=None)

    parser.add_argument("--index", default="hnsw", choices=["hnsw", "ivfflat", "none"])
    parser.add_argument("--distance", default="cosine", choices=["cosine", "l2"])
    parser.add_argument("--lists", type=int, default=100, help="IVFFlat lists (only used for ivfflat). Default: 100")
    parser.add_argument("--no-db-check", action="store_true")

    args = parser.parse_args()
    root = Path.cwd()

    if not args.no_db_check:
        _require_db_reachable_or_exit(args)

    catalog_path = (root / args.catalog).resolve() if not Path(args.catalog).is_absolute() else Path(args.catalog)
    if not catalog_path.exists():
        eprint(f"ERROR: catalog file not found: {catalog_path}")
        return 2

    catalog = read_catalog(catalog_path)
    spaces = extract_embedding_spaces(catalog)
    if not spaces:
        print("No embedding spaces found in catalog. Nothing to do.")
        return 0

    existing: List[Tuple[str, int, str]] = []
    missing: List[Tuple[str, int, str]] = []

    if args.no_db_check:
        missing = spaces[:]
        print("DB check disabled (--no-db-check). Will generate tables for ALL catalog entries.")
    else:
        from sqlalchemy import create_engine

        db_url = build_db_url(args)
        engine = create_engine(db_url, future=True)
        with engine.connect() as conn:
            for spec in spaces:
                _, _, table = spec
                if table_exists(conn, table):
                    existing.append(spec)
                else:
                    missing.append(spec)

    if existing:
        print("Embedding tables already present:")
        for space_name, dim, table in existing:
            print(f"  - {table}  (space={space_name}, dim={dim})")

    if not missing:
        print("All embedding tables from catalog already exist. No revision generated.")
        return 0

    print("Embedding tables to create:")
    for space_name, dim, table in missing:
        print(f"  - {table}  (space={space_name}, dim={dim})")

    alembic_ini = find_alembic_ini(root, args.alembic_ini)

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(alembic_ini))
    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    if not heads:
        eprint("ERROR: Could not determine Alembic head revision.")
        return 3
    if len(heads) > 1:
        eprint(f"ERROR: Multiple Alembic heads detected: {heads}. Resolve before generating.")
        return 3
    head = heads[0]

    revision_path = generate_revision_file(
        alembic_ini=alembic_ini,
        message="create embedding tables from model catalog",
        down_revision=head,
        create_specs=missing,
        index_kind=args.index,
        distance=args.distance,
        lists=args.lists,
    )

    print(f"\nGenerated Alembic revision:\n  {revision_path}")
    print("\nNext steps:")
    print("  1) Review the revision file")
    print("  2) Run: alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

