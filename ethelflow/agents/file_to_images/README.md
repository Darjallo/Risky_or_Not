# file-to-images

## Overview
`file-to-images` renders a PDF document (stored in S3 and registered in the DB) into one or more raster images and uploads those rendered images to S3 under a **temporary prefix**. It does **not** write image metadata to the database; it only produces temporary objects suitable for later ingestion by `store-images`.

Rendering is currently implemented with **PyMuPDF** and supports **vertical stacking** of one or more pages into a single output image per group.

## Endpoint(s)

### `POST /file_to_images`
Render a PDF into one image per group and upload each image to S3 under `temp_prefix`.

### `POST /cleanup_temp`
Delete all objects under a temporary S3 prefix (best-effort bulk cleanup).

### `GET /docs`
FastAPI Swagger UI.

## Request/Response

### `POST /file_to_images`

#### Request fields
- `document_id` (uuid): Document ID in DB (`EthelDocument.id`). The PDF bytes are downloaded from S3 using key `str(document_id)`.
- `renderer` (string): Must be `"pymupdf"`.
- `dpi` (int): Must be `> 0`.
- `image_format` (string): e.g. `"png"`, `"jpeg"`.
- `layout` (string): Must be `"vertical"`.
- `groups` (list[list[int]]): Each group describes pages (1-based).
  - If group has exactly two ints `[start, end]` and `end >= start`, it expands to an inclusive range.
  - Otherwise it is treated as an explicit list of page numbers, e.g. `[1, 3, 7]`.
- `temp_prefix` (string, optional): If empty, the service creates:
  - `tmp/file_to_images/{document_id}/{run_id}/`

#### Response fields
- Echoes request params (`document_id`, `renderer`, `dpi`, `image_format`, `layout`, `groups`, `temp_prefix`)
- `images` (list): one entry per group:
  - `position` (int): 1-based group index
  - `pages` (list[int]): expanded pages
  - `temp_s3_key` (string): key of uploaded temporary object
  - `mime_type` (string): derived from format
  - `byte_size` (int)
  - `width` (int), `height` (int)

#### Example request
```json
{
  "document_id": "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b",
  "renderer": "pymupdf",
  "dpi": 150,
  "image_format": "png",
  "layout": "vertical",
  "groups": [[1, 1]],
  "temp_prefix": ""
}
```

#### Example response (shape)
```json
{
  "document_id": "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b",
  "renderer": "pymupdf",
  "dpi": 150,
  "image_format": "png",
  "layout": "vertical",
  "groups": [[1, 1]],
  "temp_prefix": "tmp/file_to_images/4a70cc94-93a2-4ef2-b751-e2a6399fdf6b/<run_id>/",
  "images": [
    {
      "position": 1,
      "pages": [1],
      "temp_s3_key": "tmp/file_to_images/4a70cc94-93a2-4ef2-b751-e2a6399fdf6b/<run_id>/1_150.png",
      "mime_type": "image/png",
      "byte_size": 404152,
      "width": 2550,
      "height": 3300
    }
  ]
}
```

### `POST /cleanup_temp`

#### Request fields
- `temp_prefix` (string): S3 prefix to delete under.

#### Response fields
- `success` (bool)
- `deleted` (int): number of deleted objects attempted
- `message` (string, optional): error message on failure

## Node adapter contract (LangGraph)
This service is typically invoked before `store-images`.

**Inputs (typical)**
- `document_id`
- render params (`renderer`, `dpi`, `image_format`, `layout`, `groups`)
- optional `temp_prefix`

**Outputs (typical)**
- `temp_prefix`
- `rendered_images` list with `temp_s3_key`, `position`, `pages`, `byte_size`, `width`, `height`, `mime_type`

(Exact key names depend on the calling flow/node adapter.)

## Routing/Model catalog expectations (tenant/space)
No model calls. No model catalog/tenant routing required.

## Storage/DB interaction
- Reads `EthelDocument` for `document_id` to confirm content type is `application/pdf`.
- Downloads the PDF bytes from S3 using key `str(document_id)`.
- Uploads temporary rendered images to S3 under `temp_prefix`.

## k8s deployment/service name
- Deployment: `file-to-images`
- Service: `file-to-images`
- Container command: `python -m ethelflow.agents.file_to_images`
- Port: `8000`

## Required environment
### Postgres (DB lookup of document metadata)
- `ETHELFLOW_POSTGRES_HOST`
- `ETHELFLOW_POSTGRES_PORT`
- `ETHELFLOW_POSTGRES_DB`
- `ETHELFLOW_POSTGRES_USER`
- `ETHELFLOW_POSTGRES_PASSWORD`

### S3 (download PDF + upload temp images)
- `ETHELFLOW_S3_ENDPOINT_URL`
- `ETHELFLOW_S3_ACCESS_KEY`
- `ETHELFLOW_S3_SECRET_KEY`
- `ETHELFLOW_S3_BUCKET_NAME`

## Troubleshooting
- **400 Unsupported content type**: The document must be `application/pdf`.
- **400 Unsupported layout/renderer**: Only `layout="vertical"` and `renderer="pymupdf"` are implemented.
- **500 Failed to download document from S3**: Check S3 env and bucket/object existence (`str(document_id)` key).
- **Empty / wrong images**: Verify page group expansion and 1-based page numbering.
- **Cleanup didn’t remove objects**: Ensure the prefix ends with `/` and is correct; deletion is best-effort.
