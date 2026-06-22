#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_DIR = "/ethz/physics/per"
DEFAULT_FLOW = "rag_retrieve_test"
DEFAULT_TENANT = "ethz"

# Defaults (match your existing nomenclature)
DEFAULT_EXTRACTOR = "file_to_text"
DEFAULT_CHUNK_METHOD = "recursive_char_1000_100_htmlstrip"
DEFAULT_EMBEDDING_SPACE = None  # None => tenant default via catalog (recommended)


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def get_document_ids_from_dir(
    client: httpx.Client,
    dir_path: str,
) -> List[str]:
    """
    Uses /assets/ls to get latest_document_id for each file in a directory.
    Returns a list of unique UUID strings (order preserved as much as possible).
    """
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


def run_flow_nostream(
    client: httpx.Client,
    flow: str,
    flow_tenant: str,
    context: Dict[str, Any],
) -> Any:
    """
    Call POST /flow with stream=False and return decoded response.
    """
    body = {
        "flow": flow,
        "tenant": flow_tenant,
        "context": context,
        "stream": False,
    }
    r = client.post("/flow", json=body, timeout=1200.0)
    if r.status_code >= 300:
        raise RuntimeError(f"POST /flow failed HTTP {r.status_code}: {r.text}")

    # should be JSON, but be robust
    try:
        return r.json()
    except Exception:
        return r.text


def _pretty_print_chunks(result: Any) -> bool:
    """
    Try to find a chunks list in common places and print it nicely.
    Returns True if it printed a chunks list.
    """
    if not isinstance(result, dict):
        return False

    # Common locations depending on how your flow returns:
    # - {"chunks": [...]}
    # - {"result": {"chunks": [...]}}
    # - {"output": {"chunks": [...]}}
    # - {"final": {...}}
    candidates: List[Any] = []
    for key in ("chunks", "retrieved_chunks"):
        if key in result:
            candidates.append(result[key])

    for container_key in ("result", "output", "final", "data"):
        v = result.get(container_key)
        if isinstance(v, dict):
            for key in ("chunks", "retrieved_chunks"):
                if key in v:
                    candidates.append(v[key])

    for c in candidates:
        if isinstance(c, list) and all(isinstance(x, str) for x in c):
            print("\n--- retrieved chunks ---")
            for i, t in enumerate(c, start=1):
                print(f"\n[{i}]")
                print(t)
            return True

    return False


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run rag_retrieve_test non-streaming: list doc IDs from a directory, then retrieve chunks for a prompt."
    )
    ap.add_argument("prompt", help="Prompt/question to retrieve for")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Base URL (default: {DEFAULT_BASE_URL})")
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"Assets directory path (default: {DEFAULT_DIR})")
    ap.add_argument("--flow", default=DEFAULT_FLOW, help=f"Flow name (default: {DEFAULT_FLOW})")
    ap.add_argument("--flow-tenant", default=DEFAULT_TENANT, help=f"Tenant to send in FlowRequest (default: {DEFAULT_TENANT})")

    # Retrieval config (defaults match your current pipeline labels)
    ap.add_argument("--extractor", default=DEFAULT_EXTRACTOR, help=f"Text extractor label (default: {DEFAULT_EXTRACTOR})")
    ap.add_argument("--chunk-method", default=DEFAULT_CHUNK_METHOD, help=f"Chunking method label (default: {DEFAULT_CHUNK_METHOD})")
    ap.add_argument("--embedding-space", default=DEFAULT_EMBEDDING_SPACE, help="Embedding space override (default: tenant default)")

    # If you want to cap results:
    ap.add_argument("--top-k", type=int, default=10, help="How many best chunks to request (default: 10)")

    ap.add_argument("--insecure", action="store_true", help="Disable TLS verify (only for self-signed https)")
    args = ap.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), verify=not args.insecure) as client:
        print(f"Base URL:        {args.base_url}")
        print(f"Directory path:  {args.dir}")

        doc_ids = get_document_ids_from_dir(client, args.dir)

        print(f"Found {len(doc_ids)} document_id(s) (latest only):")
        for d in doc_ids:
            print(f"  - {d}")

        if not doc_ids:
            die("No documents found in that directory (no latest_document_id values).", code=2)

        # IMPORTANT: /flow currently does not always propagate FlowRequest.tenant into flow context,
        # so we include tenant inside context to match your established pattern.
        context: Dict[str, Any] = {
            "tenant": args.flow_tenant,
            "document_ids": doc_ids,
            "prompt": args.prompt,
            "extractor": args.extractor,
            "method": args.chunk_method,
            "embedding_space": args.embedding_space,  # None allowed => tenant default in catalog
            "top_k": args.top_k,
        }

        print("\nCalling flow (non-streaming)...")
        result = run_flow_nostream(client, args.flow, args.flow_tenant, context=context)

        # Print chunks nicely if present; otherwise print raw JSON/text
        if not _pretty_print_chunks(result):
            print("\n--- flow result ---")
            if isinstance(result, (dict, list)):
                print(json.dumps(result, indent=2))
            else:
                print(str(result))


if __name__ == "__main__":
    main()

