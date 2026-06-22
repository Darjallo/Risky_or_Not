#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List, Optional, Tuple

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_DIR = "/ethz/physics/per"
DEFAULT_FLOW = "e2e_images"
DEFAULT_TENANT = "ethz"


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def get_document_ids_from_dir(client: httpx.Client, dir_path: str) -> List[str]:
    r = client.get("/assets/ls", params={"path": dir_path}, timeout=30.0)
    if r.status_code >= 300:
        raise RuntimeError(f"GET /assets/ls failed HTTP {r.status_code}: {r.text}")
    data = r.json()
    files = data.get("files") or []

    out: List[str] = []
    seen = set()
    for f in files:
        doc_id = f.get("latest_document_id")
        if not doc_id:
            continue
        s = str(doc_id)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_groups(s: str) -> List[List[int]]:
    """
    Accepts a simple spec like:
      "1-2,3,4-6"  -> [[1,2],[3,3],[4,6]]
      "2-3,4-6,5-8" -> [[2,3],[4,6],[5,8]]
    (Ranges are inclusive.)

    If you want explicit lists like [5,7,8], use JSON mode:
      --groups-json '[[2,3],[4,6],[5,7,8]]'
    """
    out: List[List[int]] = []
    parts = [p.strip() for p in s.split(",") if p.strip()]
    for p in parts:
        if "-" in p:
            a, b = p.split("-", 1)
            a_i = int(a.strip())
            b_i = int(b.strip())
            if b_i < a_i:
                raise ValueError(f"Invalid range '{p}' (end < start)")
            out.append([a_i, b_i])
        else:
            i = int(p)
            out.append([i, i])
    if not out:
        raise ValueError("Empty groups spec")
    return out


async def run_flow(
    base_url: str,
    flow: str,
    tenant: str,
    context: Dict[str, Any],
    stream: bool = False,
) -> Dict[str, Any]:
    """
    Calls POST /flow and returns the final JSON object from the flow.
    This assumes your /flow endpoint returns JSON for non-stream runs.

    For stream=True, we still collect bytes and attempt to parse the last JSON object.
    But most of your flows are used non-stream for structured outputs, so default is non-stream.
    """
    url = base_url.rstrip("/") + "/flow"
    payload = {"flow": flow, "tenant": tenant, "context": context, "stream": stream}
    timeout = httpx.Timeout(600.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if not stream:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected /flow response type: {type(data)}")
            return data

        # streaming mode: collect and try parse as JSON at end
        buf = b""
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    buf += chunk

        # Try parse whole buffer as JSON
        try:
            return httpx.Response(200, content=buf).json()
        except Exception:
            # last resort: show raw output
            raise RuntimeError(f"Streamed response was not JSON. Raw:\n{buf.decode('utf-8', errors='replace')}")


def summarize_flow_result(doc_id: str, result: Dict[str, Any]) -> Tuple[str, str]:
    """
    Best-effort summarizer. Your flow returns a final state dict with keys like:
      - image_manifest
      - store_images_response
      - image_set_id
      - cleanup_temp_response
    """
    state = result.get("state") if isinstance(result.get("state"), dict) else result

    manifest = state.get("image_manifest") or {}
    images = manifest.get("images") or []

    store_resp = state.get("store_images_response") or {}
    image_set_id = state.get("image_set_id") or store_resp.get("image_set_id") or ""
    success = store_resp.get("success")
    msg = store_resp.get("message", "")

    if success is True:
        line1 = f"{doc_id}: OK  image_set_id={image_set_id}  images={len(images)}"
        line2 = ""
    elif success is False:
        line1 = f"{doc_id}: FAILED store_images  message={msg!r}"
        line2 = ""
    else:
        # render-only mode (store=False)
        line1 = f"{doc_id}: RENDERED (not stored)  temp_images={len(images)}"
        line2 = f"  temp_prefix={manifest.get('temp_prefix','')}"
    return line1, line2


async def amain() -> int:
    ap = argparse.ArgumentParser(description="Debug: render (and optionally store) page images for all docs in a directory.")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"Assets directory (default: {DEFAULT_DIR})")
    ap.add_argument("--flow", default=DEFAULT_FLOW)
    ap.add_argument("--tenant", default=DEFAULT_TENANT)

    # Render settings
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--image-format", default="png")
    ap.add_argument("--layout", default="vertical")
    ap.add_argument("--renderer", default="pymupdf")

    # Groups: either simple spec or JSON
    ap.add_argument("--groups", default="1-1", help="Grouping spec like '1-2,3,4-6' (ranges inclusive)")
    ap.add_argument("--groups-json", default=None, help="Explicit JSON, e.g. '[[2,3],[4,6],[5,7,8]]'")

    # Storage control
    ap.add_argument("--store", action="store_true", help="Store permanently (default)")
    ap.add_argument("--no-store", dest="store", action="store_false", help="Render only (temp S3), do not store to DB/permanent S3")
    ap.set_defaults(store=True)

    ap.add_argument("--override", action="store_true", help="Override existing image_set (same params_hash) if present")
    ap.add_argument("--no-cleanup", dest="cleanup", action="store_false", help="Do not cleanup temp prefix at end (debugging)")
    ap.set_defaults(cleanup=True)

    # Concurrency
    ap.add_argument("--limit", type=int, default=0, help="Only process first N docs (0 = all)")
    ap.add_argument("--concurrency", type=int, default=2, help="How many docs to process concurrently")

    args = ap.parse_args()

    # Resolve groups
    if args.groups_json:
        import json

        groups = json.loads(args.groups_json)
        if not isinstance(groups, list) or not groups:
            die("groups-json must decode to a non-empty list")
    else:
        try:
            groups = parse_groups(args.groups)
        except Exception as e:
            die(f"Invalid --groups: {e}")

    # Fetch doc IDs once at startup
    with httpx.Client(base_url=args.base_url.rstrip("/")) as client:
        doc_ids = get_document_ids_from_dir(client, args.dir)

    if args.limit and args.limit > 0:
        doc_ids = doc_ids[: args.limit]

    print(f"Base URL:       {args.base_url}")
    print(f"Directory:      {args.dir}")
    print(f"Flow:           {args.flow}")
    print(f"Tenant:         {args.tenant}")
    print(f"Docs found:     {len(doc_ids)}")
    print(f"Store:          {args.store}   Override: {args.override}   Cleanup: {args.cleanup}")
    print(f"Render:         dpi={args.dpi} format={args.image_format} layout={args.layout} renderer={args.renderer}")
    print(f"Groups:         {groups}")

    if not doc_ids:
        print("WARNING: no documents found.")
        return 0

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def process_one(doc_id: str) -> None:
        async with sem:
            context: Dict[str, Any] = {
                "tenant": args.tenant,
                "document_id": doc_id,
                "groups": groups,
                "dpi": args.dpi,
                "image_format": args.image_format,
                "layout": args.layout,
                "renderer": args.renderer,
                "store": args.store,
                "override_images": args.override,
                "cleanup_temp": args.cleanup,
            }
            try:
                result = await run_flow(args.base_url, args.flow, args.tenant, context, stream=False)
                l1, l2 = summarize_flow_result(doc_id, result)
                print(l1)
                if l2:
                    print(l2)
            except httpx.HTTPStatusError as e:
                print(f"{doc_id}: HTTP error {e.response.status_code}: {e}", file=sys.stderr)
                try:
                    print(e.response.text, file=sys.stderr)
                except Exception:
                    pass
            except Exception as e:
                print(f"{doc_id}: FAILED: {e}", file=sys.stderr)

    await asyncio.gather(*(process_one(d) for d in doc_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

