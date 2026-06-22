#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_DIR = "/ethz/physics/per"
DEFAULT_FLOW = "rag_chat"
DEFAULT_TENANT = "ethz"

DEFAULT_EXTRACTOR = "file_to_text"
DEFAULT_CHUNK_METHOD = "recursive_char_1000_100_htmlstrip"


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


async def stream_flow_answer(base_url: str, flow: str, tenant: str, context: Dict[str, Any]) -> str:
    """
    Stream /flow response and return the full concatenated assistant answer.
    IMPORTANT: expects flow to stream *strings*, not dicts.
    """
    url = base_url.rstrip("/") + "/flow"
    payload = {"flow": flow, "tenant": tenant, "context": context, "stream": True}

    full = ""
    timeout = httpx.Timeout(600.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for b in resp.aiter_bytes():
                if not b:
                    continue
                s = b.decode("utf-8", errors="replace")
                full += s
                print(s, end="", flush=True)

    print()  # newline after streaming finishes
    return full


def read_template_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def amain() -> int:
    ap = argparse.ArgumentParser(description="Interactive CLI RAG chatbot via rag_chat flow (streams final answer only).")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"Assets directory (default: {DEFAULT_DIR})")
    ap.add_argument("--flow", default=DEFAULT_FLOW)
    ap.add_argument("--tenant", default=DEFAULT_TENANT)

    ap.add_argument("--template-file", required=True, help="Path to Mustache template on *this machine* (host)")
    ap.add_argument("--embedding-space", default=None, help="Override embedding space (default: tenant default via catalog)")
    ap.add_argument("--extractor", default=DEFAULT_EXTRACTOR)
    ap.add_argument("--method", default=DEFAULT_CHUNK_METHOD)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--reasoning-effort", default=None)

    args = ap.parse_args()

    template_text = read_template_file(args.template_file)

    # Fetch doc IDs once at startup
    with httpx.Client(base_url=args.base_url.rstrip("/")) as client:
        doc_ids = get_document_ids_from_dir(client, args.dir)

    print(f"Base URL:       {args.base_url}")
    print(f"Directory:      {args.dir}")
    print(f"Flow:           {args.flow}")
    print(f"Tenant:         {args.tenant}")
    print(f"Docs found:     {len(doc_ids)}")
    for d in doc_ids:
        print(f"  - {d}")
    if not doc_ids:
        print("WARNING: no documents found; retrieval will be empty (still chats).")

    # History as list of {"role","content"} for your own tracking + template rendering
    history: List[Dict[str, str]] = []

    print("\nType your messages. Type 'exit' to quit.\n")

    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            break

        context: Dict[str, Any] = {
            # IMPORTANT: /flow may not inject FlowRequest.tenant into context, so include it here.
            "tenant": args.tenant,

            "prompt": user_text,
            "history": history,

            # retrieval inputs
            "document_ids": doc_ids,
            "extractor": args.extractor,
            "method": args.method,
            "embedding_space": args.embedding_space,
            "top_k": args.top_k,

            # template: send inline for rapid iteration (no image rebuild required)
            "template": template_text,

            # reasoning tweak
            "reasoning_effort": args.reasoning_effort,
        }

        print("Assistant: ", end="", flush=True)
        try:
            answer = await stream_flow_answer(args.base_url, args.flow, args.tenant, context)
        except httpx.HTTPStatusError as e:
            print(f"\nHTTP error {e.response.status_code}: {e}", file=sys.stderr)
            try:
                print(e.response.text, file=sys.stderr)
            except Exception:
                pass
            continue
        except Exception as e:
            print(f"\nRequest failed: {e}", file=sys.stderr)
            continue

        # Update history
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})

    print("\nBye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

