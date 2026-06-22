# ethelflow/assets

This package provides **async S3-compatible object storage** for EthelFlow. It is intentionally small: it exposes a single `S3Manager` instance (`s3_manager`) used by HTTP routes and agents to **upload, download, and delete** binary document payloads.

- **Metadata lives in Postgres** (`ethelflow/data/models.py`).
- **Binary bytes live in S3** (or any S3-compatible service, e.g., MinIO).
- The common convention in this codebase is: **S3 object key = `str(document_id)`**, where `document_id` is an `EthelDocument.id` UUID.

---

## What this module is for

EthelFlow treats files as:

1. **Logical path metadata** (`Asset`, `EthelDocument`) stored in Postgres.
2. **Immutable binary content** stored in S3, referenced by the document UUID.

This separation enables:
- versioned assets without copying content into the DB,
- efficient streaming download/upload of large files,
- agents (e.g., `file_to_text`, `reasoning_with_document`) to fetch bytes by UUID.

---

## Public API

### `s3_manager`

A global instance:

```python
from ethelflow.assets.s3 import s3_manager
```

### `S3Manager` methods

All methods are **async** and require `await`.

| Method | Purpose | Notes |
|---|---|---|
| `await s3_manager.init()` | Create the async client + ensure bucket exists | Call once at app startup (recommended via FastAPI lifespan). |
| `await s3_manager.close()` | Close underlying client | Call once at shutdown. |
| `await s3_manager.upload_file(file_object, object_name)` | Upload bytes to `Key=object_name` | Reads from `file_object.read()`; caller typically passes `BytesIO`. |
| `await s3_manager.download_file(object_name, file_object)` | Download bytes into a `BytesIO` | Writes downloaded bytes into `file_object`. |
| `await s3_manager.delete_file(object_name)` | Delete object by key | Used for cleanup / version overwrite behavior. |

**Key naming convention (important):**
- For documents, use `object_name = str(document_id)` where `document_id` is a UUID.
- The `/assets` route does exactly this when uploading/downloading versions.

---

## How it is used in the system

### Assets route (`ethelflow/routes/assets.py`)
`/assets` endpoints:
- compute/lookup logical path + versioning in Postgres,
- upload/download raw bytes to/from S3,
- store the resulting `document_id` in Postgres.

Key interactions:
- **Upload**: `s3_manager.upload_file(BytesIO(data_bytes), str(new_doc_id))`
- **Download**: `s3_manager.download_file(str(doc.id), buf)`
- **Delete**: `s3_manager.delete_file(str(doc_id))` (best-effort on overwrite/removal)

### Agents that need file bytes
Some agents fetch bytes by UUID for processing:
- `file_to_text` downloads a document from S3 then extracts text (PDF/plain/HTML).
- `reasoning` (`/reasoning_with_document`) can download one or many documents from S3, base64-encode them, and attach them as inline images/messages to the model.

In those cases, **the agent trusts the `document_id`** it receives and uses it as the S3 key.

---

## Configuration

Configuration comes from:

```python
from ethelflow.settings.s3_settings import s3_settings
```

Expected fields on `s3_settings`:
- `endpoint_url`
- `access_key`
- `secret_key`
- `bucket_name`

How you provide these depends on your deployment approach (typically environment variables injected via Kubernetes).

**S3 compatibility**
- This code uses `aiobotocore`, so it supports AWS S3 and most S3-compatible endpoints (MinIO, Ceph, etc.).
- Uses signature v4 (`Config(signature_version="s3v4")`).

---

## Lifecycle and initialization

### Recommended pattern (FastAPI lifespan)
Because `aiobotocore` clients are async context managers, you should initialize once:

- call `await s3_manager.init()` in application startup/lifespan
- call `await s3_manager.close()` on shutdown

You can see this pattern in agents like `reasoning` and `file_to_text`, which call `s3_manager.init()` in their lifespan handlers.

**Important:** `S3Manager.init()` is idempotent (it tracks `_initialized`), so multiple calls are safe, but you should avoid re-creating clients per request.

---

## Data model contract

This package itself does **not** know about:
- tenants/collections/subpaths,
- versioning rules,
- content types,
- authorization.

Those are handled elsewhere.

The **contract** is:

- The caller chooses the object key (string).
- The caller controls how that key maps to DB rows.
- In this repo’s conventions, keys are UUID strings:
  - `EthelDocument.id` → S3 key
  - DB rows reference that UUID, not an S3 URL.

---

## Error handling and behavior

- `_ensure_bucket_exists()` uses `head_bucket`; on a 404 it attempts `create_bucket`.
- Upload/download/delete will raise exceptions from the underlying client if credentials, bucket, or endpoint are wrong.
- Higher layers decide rollback/cleanup:
  - `/assets` upload performs DB transaction handling + deletes orphaned S3 objects if metadata persistence fails.
  - `/assets` overwrite tries to delete the replaced object best-effort.

---

## Troubleshooting

### Common issues

**1) “Missing credentials” / auth errors**
- Verify `access_key` and `secret_key` are set correctly in your deployment.
- Verify the endpoint supports SigV4 and the keys have permission to `PutObject`, `GetObject`, `DeleteObject`, and `HeadBucket`.

**2) “Connection refused” / DNS errors**
- Check the `endpoint_url` is reachable from the pod/container.
- In Kubernetes, confirm Service/Ingress name and namespace.

**3) Bucket not found**
- `init()` attempts to create the bucket if it gets a 404 from `head_bucket`.
- Some S3 backends require additional parameters for bucket creation (region, etc.). If so, you may need to extend `_ensure_bucket_exists()` accordingly.

**4) Downloads return 404 / missing key**
- Confirm the `document_id` exists in Postgres and matches the key used on upload.
- Confirm you didn’t delete or overwrite the object (e.g., version overwrite behavior).

### Quick sanity checks

- Can the app list or head the bucket? (via `head_bucket`)
- Can the app upload and then immediately download a small object using a known key?

---

## Notes for extending

If you later need:
- streaming uploads/downloads (without reading all bytes into memory),
- multipart upload for very large files,
- server-side encryption or ACLs,
- per-tenant buckets/prefixes,

…this is the correct place to extend, but keep the **simple contract**: higher layers own the mapping between logical path/versioning and S3 keys.
