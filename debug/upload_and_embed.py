#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def default_title(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def now_tag() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def build_logical_path(tenant: str, collection: str, subdir: str, filename: str) -> str:
    # subdir may be "" or "a/b/c"
    subdir = (subdir or "").strip("/")
    if subdir:
        return f"/{tenant}/{collection}/{subdir}/{filename}"
    return f"/{tenant}/{collection}/{filename}"


def upload_document(
    client: httpx.Client,
    file_path: str,
    logical_path: str,
    title: str,
    overwrite: bool = True,
) -> Dict[str, Any]:
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
        params = {
            "path": logical_path,
            "title": title,
            "overwrite": str(overwrite).lower(),
        }
        r = client.post("/assets", params=params, files=files, timeout=300.0)

    if r.status_code >= 300:
        raise RuntimeError(f"Upload failed HTTP {r.status_code}: {r.text}")

    doc = r.json()

    # Current /assets returns document_id, not id
    if "document_id" not in doc:
        raise RuntimeError(f"Upload response missing 'document_id': {doc}")

    # sanity check
    try:
        uuid.UUID(str(doc["document_id"]))
    except Exception:
        raise RuntimeError(f"Upload response document_id is not a UUID: {doc['document_id']!r}")

    return doc


def run_flow_sync(
    client: httpx.Client,
    flow: str,
    context: Dict[str, Any],
    tenant: str = "ethz",
) -> Any:
    body = {
        "flow": flow,
        "tenant": tenant,
        "context": context,
        "stream": False,
    }
    r = client.post("/flow", json=body, timeout=1200.0)
    if r.status_code >= 300:
        raise RuntimeError(f"POST /flow failed HTTP {r.status_code}: {r.text}")
    try:
        return r.json()
    except Exception:
        return r.text


def start_flow_async(
    client: httpx.Client,
    flow: str,
    context: Dict[str, Any],
    tenant: str = "ethz",
) -> str:
    body = {
        "flow": flow,
        "tenant": tenant,
        "context": context,
        "stream": False,
    }
    r = client.post("/flow/start", json=body, timeout=60.0)
    if r.status_code >= 300:
        raise RuntimeError(f"POST /flow/start failed HTTP {r.status_code}: {r.text}")
    data = r.json()
    run_id = data.get("run_id")
    if not run_id:
        raise RuntimeError(f"No run_id in response: {data}")
    return str(run_id)


def poll_status(client: httpx.Client, run_id: str, timeout_s: float = 600.0, poll_s: float = 2.0) -> Any:
    t0 = time.time()
    last: Any = None
    while time.time() - t0 < timeout_s:
        r = client.get(f"/flow/{run_id}/status", timeout=20.0)
        if r.status_code < 300:
            try:
                last = r.json()
            except Exception:
                last = r.text
            print("\n--- status ---")
            print(json.dumps(last, indent=2) if isinstance(last, (dict, list)) else str(last))
        time.sleep(poll_s)
    return last


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload a file to EthelFlow and run e2e_embedding.")
    ap.add_argument("file", help="Path to file (e.g. ./foo/bar.pdf)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Base URL (default: {DEFAULT_BASE_URL})")

    # Logical path controls
    ap.add_argument("--asset-path", default=None, help="Full logical path, e.g. /ethz/physics/mechanics/demo/x.pdf")
    ap.add_argument("--tenant", default="ethz", help="Logical tenant for /assets path (default: ethz)")
    ap.add_argument("--collection", default="physics", help="Logical collection for /assets path (default: physics)")
    ap.add_argument("--subdir", default=f"uploads/_run_{now_tag()}", help="Subdir under collection (default: unique run dir)")

    ap.add_argument("--title", default=None, help="Document title (default: filename stem)")
    ap.add_argument("--method", default="recursive_char_1000_100_htmlstrip", help="Chunking method label")
    ap.add_argument("--flow", default="e2e_embedding", help="Flow name under ethelflow.flows (default: e2e_embedding)")
    ap.add_argument("--flow-tenant", default="ethz", help="Tenant string to send in FlowRequest (default: ethz)")
    ap.add_argument("--async-flow", action="store_true", help="Use /flow/start + status polling")
    ap.add_argument("--insecure", action="store_true", help="Disable TLS verify (only for self-signed https)")
    ap.add_argument("--no-overwrite", action="store_true", help="Set overwrite=false on upload")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        die(f"Not a file: {args.file}")

    title = args.title or default_title(args.file)
    filename = os.path.basename(args.file)

    logical_path = args.asset_path
    if not logical_path:
        logical_path = build_logical_path(args.tenant, args.collection, args.subdir, filename)

    with httpx.Client(base_url=args.base_url.rstrip("/"), verify=not args.insecure) as client:
        print(f"Base URL:    {args.base_url}")
        print(f"Uploading:   {args.file}")
        print(f"Asset path:  {logical_path}")
        print(f"Overwrite:   {not args.no_overwrite}")

        doc = upload_document(
            client,
            args.file,
            logical_path=logical_path,
            title=title,
            overwrite=not args.no_overwrite,
        )
        print("\n--- upload response ---")
        print(json.dumps(doc, indent=2))

        document_id = str(doc["document_id"])

        # Context expected by e2e_embedding.py: context.get("document_id")
        context = {"document_id": document_id, "method": args.method}

        if args.async_flow:
            print(f"\nStarting async flow '{args.flow}'...")
            run_id = start_flow_async(client, args.flow, context=context, tenant=args.flow_tenant)
            print(f"run_id = {run_id}")
            poll_status(client, run_id)
        else:
            print(f"\nRunning flow '{args.flow}' via POST /flow ...")
            result = run_flow_sync(client, args.flow, context=context, tenant=args.flow_tenant)
            print("\n--- flow result ---")
            print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result))


if __name__ == "__main__":
    main()

