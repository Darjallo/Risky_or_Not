# store-images

## Overview
`store-images` takes one or more **temporary rendered images** (produced by `file-to-images`) and:
1) Copies/moves them into a **permanent S3 location** under the document’s namespace, and
2) Writes metadata to Postgres tables `document_image_sets` and `document_images`.

It supports “idempotent” storage via a `params_hash` unique constraint per `(document_id, params_hash)`. Depending on request flags, it may reuse an existing image set or create a new one.

## Endpoint(s)

### `POST /store_images`
Persist a rendered image set: write DB rows and store images at permanent S3 keys.

### `GET /docs`
FastAPI Swagger UI.

## Request/Response (shape)
> Note: exact Pydantic field names may vary slightly; this documents the observed behavior and conventions.

### Request fields (typical)
- `document_id` (uuid): Document being annotated.
- Render params:
  - `renderer` (string) default `"pymupdf"`
  - `dpi` (int) default `150`
  - `image_format` (string) default `"png"`
  - `layout` (string) default `"vertical"`
- `images` (list): each image includes at least:
  - `position` (int)
  - `temp_s3_key` (string)
  - `byte_size` (int)
  - optionally `width`, `height`, `mime_type`, `pages`
- Behavior flags (as used by your debug script):
  - `override` (bool): if true, overwrite/recreate existing image_set for the same params
  - `cleanup` (bool): if true, delete temp objects after successful store
- Optional:
  - `temp_prefix` (string): for cleanup convenience

### Response fields (observed)
- `success` (bool)
- `image_set_id` (uuid) when successful
- `images` (int) or `image_count` (int): number stored
- `message` (string) on error

## Example
### Example request (conceptual)
```json
{
  "document_id": "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b",
  "renderer": "pymupdf",
  "dpi": 150,
  "image_format": "png",
  "layout": "vertical",
  "images": [
    {
      "position": 1,
      "temp_s3_key": "tmp/file_to_images/4a70cc94-93a2-4ef2-b751-e2a6399fdf6b/<run_id>/1_150.png",
      "byte_size": 404152
    }
  ],
  "override": false,
  "cleanup": true
}
```

### Example outcome (DB + S3)
**S3 permanent key convention**
```
documents/<document_id>/images/<image_set_id>/<position>_<dpi>.<ext>
```

Example row (from your DB query):
```
documents/4a70cc94-93a2-4ef2-b751-e2a6399fdf6b/images/34a84d85-4c0f-4336-93ca-1d59414556fe/1_150.png
```

## Node adapter contract (LangGraph)
This is typically called after `file-to-images`.

**Inputs (typical)**
- `document_id`
- render params (`renderer`, `dpi`, `image_format`, `layout`)
- `rendered_images` list, each with `temp_s3_key` and `position`
- flags: `override`, `cleanup`

**Outputs (typical)**
- `image_set_id`
- `images` (stored count)
- optionally, stored image metadata including permanent `s3_key`

(Exact key names depend on `agents/store_images/node_adapter.py`.)

## Routing/Model catalog expectations (tenant/space)
No model calls. No model catalog/tenant routing required.

## Storage/DB interaction
### Tables
- `document_image_sets`
  - Unique constraint: `(document_id, params_hash)`
  - Defaults: `renderer='pymupdf'`, `dpi=150`, `image_format='png'`, `layout='vertical'`
  - `created_at` default `now()`
- `document_images`
  - FK to `document_image_sets` with `ON DELETE CASCADE`
  - Unique constraint: `(image_set_id, position)`
  - Stores `s3_key`, `byte_size` (and potentially width/height/mime/pages depending on schema)

### Behavior
- Looks up the document in DB.
- Creates or reuses an image set depending on `params_hash` + `override`.
- Copies each `temp_s3_key` to permanent `documents/<document_id>/images/<image_set_id>/...`.
- Inserts one `document_images` row per stored image.
- If `cleanup` is enabled, removes temp objects/prefix after success (best-effort).

## k8s deployment/service name
- Deployment: `store-images`
- Service: `store-images`
- Container command: `python -m ethelflow.agents.store_images`
- Port: `8000`

## Required environment
### Postgres (writes image metadata)
- `ETHELFLOW_POSTGRES_HOST`
- `ETHELFLOW_POSTGRES_PORT`
- `ETHELFLOW_POSTGRES_DB`
- `ETHELFLOW_POSTGRES_USER`
- `ETHELFLOW_POSTGRES_PASSWORD`

### S3 (copy to permanent keys; optional temp cleanup)
- `ETHELFLOW_S3_ENDPOINT_URL`
- `ETHELFLOW_S3_ACCESS_KEY`
- `ETHELFLOW_S3_SECRET_KEY`
- `ETHELFLOW_S3_BUCKET_NAME`

## Troubleshooting
- **`password authentication failed for user ...`**
  - Env vars missing or wrong in the Deployment (compare to `store-text`).
  - Verify with:
    - `kubectl exec deploy/store-images -- env | egrep "ETHELFLOW_(POSTGRES|S3)_" | sort`
- **Flow returns 500 but `/docs` works**
  - `/docs` doesn’t hit DB. A DB auth/config error will show up only when `/store_images` runs.
- **Objects exist in temp prefix but not in permanent keys**
  - Check S3 env, bucket name, and that `temp_s3_key` is correct.
- **Duplicate / unexpected reuse**
  - `params_hash` uniqueness may be reusing an existing image_set (expected unless `override=true`).
- **Cannot “view in browser”**
  - MinIO API port (9000) returns 403 for anonymous requests; use authenticated client or presigned URLs.
  - MinIO console typically runs on port 9001 (and often expects HTTPS depending on your setup).
