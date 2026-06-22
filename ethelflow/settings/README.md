# ethelflow/settings

Central configuration for EthelFlow services.

This package uses **Pydantic Settings** (`pydantic_settings.BaseSettings`) to load configuration from:
- Environment variables (preferred in Kubernetes)
- Optional `.env` files (useful for local development)

> Tip: In production, prefer env vars and Kubernetes Secrets/ConfigMaps. Treat `.env` files as local-only.

---

## How settings are loaded

Each settings module defines a `BaseSettings` subclass with a specific `env_prefix`, and then instantiates it at import time (e.g., `postgres_settings = PostgresSettings()`).

That means:
- Missing **required** settings will raise an error **when the module is imported**.
- If you add a new settings module, keep it import-safe (or avoid instantiating at import time if it may not be configured everywhere).

---

## Modules

### `postgres_settings.py`

**Used by:** `ethelflow/data/db_utils.py` (DB engine + sessions)

**Env prefix:** `ETHELFLOW_POSTGRES_` (case-insensitive)

**Fields (with defaults):**
- `ETHELFLOW_POSTGRES_USER` (default: `ethel`)
- `ETHELFLOW_POSTGRES_PASSWORD` (default: `ethel`)
- `ETHELFLOW_POSTGRES_HOST` (default: `postgres`)
- `ETHELFLOW_POSTGRES_PORT` (default: `5432`)
- `ETHELFLOW_POSTGRES_DB` (default: `ethel`)

**Computed URLs:**
- `postgres_settings.url` → `postgresql+psycopg://...` (sync SQLAlchemy)
- `postgres_settings.async_url` → `postgresql+asyncpg://...` (async SQLAlchemy)
- `postgres_settings.db_url` → `postgresql://...` (raw psycopg-style URL)

**Example (local dev):**
```bash
export ETHELFLOW_POSTGRES_HOST=localhost
export ETHELFLOW_POSTGRES_USER=ethel
export ETHELFLOW_POSTGRES_PASSWORD=ethel
export ETHELFLOW_POSTGRES_DB=ethel
```

---

### `s3_settings.py`

**Used by:** `ethelflow/assets/s3.py` (S3Manager / MinIO integration)

**Env prefix:** `ETHELFLOW_S3_` (case-insensitive)

**Fields (with defaults):**
- `ETHELFLOW_S3_ENDPOINT_URL` (default: `http://minio:9000`)
- `ETHELFLOW_S3_ACCESS_KEY` (default: `ethel`)
- `ETHELFLOW_S3_SECRET_KEY` (default: `ethel_secret`)
- `ETHELFLOW_S3_BUCKET_NAME` (default: `ethel-documents`)

**Example (local MinIO):**
```bash
export ETHELFLOW_S3_ENDPOINT_URL=http://localhost:9000
export ETHELFLOW_S3_ACCESS_KEY=ethel
export ETHELFLOW_S3_SECRET_KEY=ethel_secret
export ETHELFLOW_S3_BUCKET_NAME=ethel-documents
```

---

### `embedding_settings.py`

**Env prefix:** `ETHELFLOW_EMBEDDING_`

**Reads env file:** `.env`

**Fields (required unless provided via env):**
- `ETHELFLOW_EMBEDDING_API_KEY`
- `ETHELFLOW_EMBEDDING_AZURE_ENDPOINT`
- `ETHELFLOW_EMBEDDING_API_VERSION` (default: `2025-04-01-preview`)

**Important note (current architecture):**
Many embedding-related services in this repo route via the **model catalog** and provider-specific env vars (e.g., `ETHELFLOW_PROVIDER_..._API_KEY`) rather than this module. Keep `embedding_settings.py` as:
- local/dev convenience, **or**
- backward compatibility for any code paths that still import it.

Because the module creates `settings = EmbeddingSettings()` at import time, importing it without those env vars present will raise a validation error.

---

### `reasoning_settings.py`

**Env prefix:** `ETHELFLOW_REASONING_`

**Reads env file:** `secret/reasoning.env`

**Fields (required unless provided via env):**
- `ETHELFLOW_REASONING_API_KEY`
- `ETHELFLOW_REASONING_AZURE_ENDPOINT`
- `ETHELFLOW_REASONING_API_VERSION` (default: `2025-04-01-preview`)

**Important note (current architecture):**
As with embeddings, reasoning is typically routed via the **model catalog** and provider-specific env vars rather than this module. Keep this module for compatibility/local runs only unless a service explicitly uses it.

Also note the `env_file="secret/reasoning.env"` default: in containers, that file typically **does not exist** unless you mount it. Prefer Kubernetes Secrets → environment variables.

---

## Interplay with the rest of the system

Even though this package focuses on “classic” settings, several system-wide configuration knobs are used elsewhere and are worth knowing when developing services and flows:

### Model catalog routing (agents + flows)
Many agents route model/provider selection using the model catalog:
- `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`
- K8s mounts a ConfigMap at `/etc/ethelflow/catalog.yaml`

Agents then resolve:
- `tenant` → default embedding space / inference routes
- `space` or `inference_class` → provider + deployment + dimensions + store table

### Provider API keys (Azure OpenAI via catalog)
Provider keys are typically injected as environment variables specified by the catalog (e.g. something like):
- `ETHELFLOW_PROVIDER_<PROVIDERNAME>_AZURE_OPENAI_API_KEY`

In your Kubernetes setup, this is usually sourced from a Secret and passed into each agent Deployment.

### Shared OpenAI/Azure knobs (used directly by agents)
Some agents read these directly (not via `BaseSettings`):
- `ETHELFLOW_AZURE_OPENAI_API_VERSION` (defaulted in-code if unset)
- `ETHELFLOW_DEFAULT_TENANT` (optional; many flows still require tenant explicitly)
- `ETHELFLOW_INFERENCE_CLASS` (defaulted to `reasoning` in the reasoning agent)

---

## Adding a new settings module (recommended pattern)

1. Create `my_settings.py` with:
   - a clear `env_prefix` like `ETHELFLOW_MYCOMPONENT_`
   - safe defaults where possible
2. Prefer **not** to instantiate at import time if:
   - it is not universally configured, or
   - only some services use it

Example:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class MySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ETHELFLOW_MY_", case_sensitive=False)
    required_value: str
    optional_value: int = 123

# Option A: instantiate (simple, but import-time failure if missing)
my_settings = MySettings()

# Option B: lazy getter (safer across services)
_cached: MySettings | None = None
def get_my_settings() -> MySettings:
    global _cached
    if _cached is None:
        _cached = MySettings()
    return _cached
```

---

## Quick reference

| Component | Module | Prefix |
|---|---|---|
| Postgres DB | `postgres_settings.py` | `ETHELFLOW_POSTGRES_` |
| S3/MinIO | `s3_settings.py` | `ETHELFLOW_S3_` |
| Embedding (legacy/local) | `embedding_settings.py` | `ETHELFLOW_EMBEDDING_` |
| Reasoning (legacy/local) | `reasoning_settings.py` | `ETHELFLOW_REASONING_` |
