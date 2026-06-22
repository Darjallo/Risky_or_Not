#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from typing import Any, Dict, List

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_DIR = "/ethz/physics/per"
DEFAULT_FLOW = "rag_intent_chat"
DEFAULT_TENANT = "ethz"

DEFAULT_EXTRACTOR = "file_to_text"
DEFAULT_CHUNK_METHOD = "recursive_char_1000_100_htmlstrip"


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def get_document_ids_from_dir(client: httpx.Client, dir_path: str) -> List[str]:
    r = client.get("/assets/ls", params={"path": dir_path}, timeout=30.0)
    r.raise_for_status()
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


def default_intent_options() -> Dict[str, Any]:
    return {
        "version": 1,
        "default_intent": "chat",
        "options": {
            "simulation": {
                "description": "User wants the system to generate or run a simulation (interactive or computed).",
                "examples": ["simulate", "model this", "run a simulation", "numerically solve"],
            },
            "exercise": {
                "description": "User wants an interactive exercise/problem (practice, hints, answer checking).",
                "examples": ["give me an exercise", "quiz me", "practice problems", "check my answer"],
            },
            "visualization": {
                "description": "User wants a visualization (plot/diagram/image).",
                "examples": ["plot", "visualize", "draw", "show me a graph", "make an image"],
            },
        },
        "confidence_threshold": 0.70,
    }


async def call_flow(base_url: str, flow: str, tenant: str, context: Dict[str, Any]) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/flow"
    payload = {"flow": flow, "tenant": tenant, "context": context, "stream": False}
    timeout = httpx.Timeout(600.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected /flow response: {data!r}")
        return data


def read_template_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _safe_get(d: Any, path: List[str], default: Any = None) -> Any:
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _short(s: Any, n: int = 220) -> str:
    if s is None:
        return ""
    t = s if isinstance(s, str) else str(s)
    t = t.replace("\n", " ").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def summarize_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    msgs = ctx.get("messages")
    msg_len = len(msgs) if isinstance(msgs, list) else None

    routing = ctx.get("routing_state") if isinstance(ctx.get("routing_state"), dict) else {}
    intent = routing.get("intent")
    conf = routing.get("confidence")
    topic = routing.get("topic")
    lang = routing.get("language")

    dbg = ctx.get("debug") if isinstance(ctx.get("debug"), dict) else {}
    dbg_keys = sorted(list(dbg.keys()))[:50]

    return {
        "messages_len": msg_len,
        "routing_state": {"intent": intent, "confidence": conf, "topic": topic, "language": lang},
        "debug_keys": dbg_keys,
    }


def diff_summary(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    b = summarize_context(before)
    a = summarize_context(after)
    out: List[str] = []

    if b.get("messages_len") != a.get("messages_len"):
        out.append(f"- messages_len: {b.get('messages_len')} -> {a.get('messages_len')}")

    b_rs = b.get("routing_state", {})
    a_rs = a.get("routing_state", {})
    if b_rs != a_rs:
        out.append(f"- routing_state: {b_rs} -> {a_rs}")

    b_dk = b.get("debug_keys", [])
    a_dk = a.get("debug_keys", [])
    if b_dk != a_dk:
        out.append(f"- debug keys: {b_dk} -> {a_dk}")

    return out


def extract_intent_debug(result: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    # Prefer top-level fields from flow result
    intent = result.get("intent")
    topic = result.get("topic")
    matched = result.get("intent_matched")
    conf = result.get("intent_confidence")

    # Threshold + options live in ctx.intent_options
    intent_options = ctx.get("intent_options") if isinstance(ctx.get("intent_options"), dict) else {}
    thr = _as_float(intent_options.get("confidence_threshold"), 0.70)
    options = intent_options.get("options") if isinstance(intent_options.get("options"), dict) else {}
    allowed = sorted(list(options.keys()))

    # Raw intent_response is inside ctx.debug.intent_response (flow stores it there)
    raw = _safe_get(ctx, ["debug", "intent_response"], default=None)

    lang = _safe_get(raw, ["result", "language"], default=None)
    raw_intent = _safe_get(raw, ["result", "intent"], default=None)
    raw_conf = _safe_get(raw, ["result", "confidence"], default=None)
    raw_topic = _safe_get(raw, ["result", "topic"], default=None)

    return {
        "allowed": allowed,
        "threshold": thr,
        "flow_result": {"intent": intent, "topic": topic, "confidence": conf, "matched": matched},
        "raw_result": {"intent": raw_intent, "topic": raw_topic, "language": lang, "confidence": raw_conf},
        "raw_intent_response": raw,
    }


def maybe_print_intent_line(result: Dict[str, Any]) -> bool:
    if result.get("intent_matched"):
        intent = result.get("intent") or "something"
        topic = result.get("topic") or "something"
        print(f"Assistant: *** I would make a {intent} about {topic} ***")
        return True
    return False


def print_intent_debug(info: Dict[str, Any]) -> None:
    allowed = info.get("allowed") or []
    thr = info.get("threshold")
    fr = info.get("flow_result") or {}
    rr = info.get("raw_result") or {}

    print("\n[INTENT DEBUG]")
    print(f"  allowed intents: {allowed}")
    print(f"  threshold:       {thr}")
    print(f"  flow intent:     {fr.get('intent')!r}   matched={fr.get('matched')}   conf={fr.get('confidence')}")
    print(f"  flow topic:      {fr.get('topic')!r}")
    print(f"  raw intent:      {rr.get('intent')!r}   conf={rr.get('confidence')}   lang={rr.get('language')!r}")
    print(f"  raw topic:       {rr.get('topic')!r}")

    raw = info.get("raw_intent_response")
    if raw is None:
        print("  raw response:    (missing)  <-- flow didn't store debug.intent_response?")
    else:
        print("  raw response:")
        s = json.dumps(raw, indent=2, ensure_ascii=False)
        print(s[:5000] + ("\n…(truncated)…" if len(s) > 5000 else ""))
    print("[/INTENT DEBUG]\n")


def print_final_prompt(ctx: Dict[str, Any], max_chars: int = 2000) -> None:
    fp = _safe_get(ctx, ["debug", "final_prompt"])
    if not fp:
        print("[DEBUG] No debug.final_prompt in context.")
        return
    print("\n[DEBUG final_prompt]")
    s = fp if isinstance(fp, str) else str(fp)
    print(s[:max_chars] + ("\n…(truncated)…" if len(s) > max_chars else ""))
    print("[/DEBUG final_prompt]\n")


def print_chunks(ctx: Dict[str, Any], max_chunks: int = 5, max_chars: int = 350) -> None:
    chunks = _safe_get(ctx, ["debug", "chunk_texts"], default=[])
    if not isinstance(chunks, list) or not chunks:
        print("[DEBUG] No debug.chunk_texts (or empty).")
        return
    print("\n[DEBUG chunks]")
    for i, ch in enumerate(chunks[:max_chunks], start=1):
        print(f"  [{i}] {_short(ch, max_chars)}")
    if len(chunks) > max_chunks:
        print(f"  … ({len(chunks) - max_chunks} more)")
    print("[/DEBUG chunks]\n")


async def amain() -> int:
    ap = argparse.ArgumentParser(
        description="Interactive CLI for rag_intent_chat flow (non-stream; caller stores context)."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--flow", default=DEFAULT_FLOW)
    ap.add_argument("--tenant", default=DEFAULT_TENANT)

    ap.add_argument("--template-file", required=True, help="Path to Mustache template on this machine")
    ap.add_argument("--embedding-space", default=None)
    ap.add_argument("--extractor", default=DEFAULT_EXTRACTOR)
    ap.add_argument("--method", default=DEFAULT_CHUNK_METHOD)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--reasoning-effort", default=None)

    ap.add_argument("--intent-options-file", default=None, help="Optional JSON file defining intent options")

    # Debug toggles
    ap.add_argument("--debug-intent", action="store_true", help="Print intent classification details each turn")
    ap.add_argument("--print-context-diff", action="store_true", help="Print a small summary diff of context changes")
    ap.add_argument("--show-debug-final-prompt", action="store_true", help="Print debug.final_prompt each turn")
    ap.add_argument("--show-chunks", action="store_true", help="Print debug.chunk_texts snippet each turn")
    ap.add_argument("--max-chunks", type=int, default=5)
    ap.add_argument("--max-chunk-chars", type=int, default=350)
    ap.add_argument("--max-final-prompt-chars", type=int, default=2000)

    ap.add_argument("--dry-run", action="store_true", help="Print context and do not call /flow")
    ap.add_argument("--no-memory-update", action="store_true", help="Do not replace local context with ctx_out")
    ap.add_argument("--force-threshold", type=float, default=None, help="Override intent_options.confidence_threshold")
    ap.add_argument("--print-raw-response", action="store_true", help="Print full /flow JSON response (truncated)")

    args = ap.parse_args()

    template_text = read_template_file(args.template_file)

    # Fetch doc IDs once at startup
    with httpx.Client(base_url=args.base_url.rstrip("/")) as client:
        doc_ids = get_document_ids_from_dir(client, args.dir)

    if args.intent_options_file:
        with open(args.intent_options_file, "r", encoding="utf-8") as f:
            intent_options = json.load(f)
    else:
        intent_options = default_intent_options()

    if args.force_threshold is not None:
        intent_options = dict(intent_options)
        intent_options["confidence_threshold"] = float(args.force_threshold)

    # Canonical context (this is the "memory")
    context: Dict[str, Any] = {
        "tenant": args.tenant,
        "messages": [],
        "routing_state": {},
        "intent_options": intent_options,
        "rag": {
            "document_ids": doc_ids,
            "extractor": args.extractor,
            "method": args.method,
            "embedding_space": args.embedding_space,
            "top_k": args.top_k,
            "template": template_text,
            "reasoning_effort": args.reasoning_effort,
        },
        "debug": {},
    }

    print(f"Base URL:   {args.base_url}")
    print(f"Flow:       {args.flow}")
    print(f"Tenant:     {args.tenant}")
    print(f"Docs found: {len(doc_ids)}")
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

        before_ctx = deepcopy(context)

        # Caller appends user turn (caller owns memory)
        msgs = context.get("messages")
        if not isinstance(msgs, list):
            msgs = []
            context["messages"] = msgs
        msgs.append({"role": "user", "content": user_text})

        if args.dry_run:
            print("\n[DRY RUN] Context being sent to /flow (truncated):")
            s = json.dumps(context, indent=2, ensure_ascii=False)
            print(s[:4000] + ("\n…(truncated)…" if len(s) > 4000 else ""))
            print()
            continue

        try:
            result = await call_flow(args.base_url, args.flow, args.tenant, context)
        except httpx.HTTPStatusError as e:
            eprint(f"\nHTTP error {e.response.status_code}: {e}")
            try:
                eprint(e.response.text)
            except Exception:
                pass
            continue
        except Exception as e:
            eprint(f"\nRequest failed: {e}")
            continue

        if args.print_raw_response:
            raw = json.dumps(result, indent=2, ensure_ascii=False)
            print("\n[RAW /flow response] (truncated)")
            print(raw[:5000] + ("\n…(truncated)…" if len(raw) > 5000 else ""))
            print("[/RAW]\n")

        # Replace memory with updated context from flow (unless disabled)
        ctx_out = result.get("context")
        if not args.no_memory_update and isinstance(ctx_out, dict):
            context = ctx_out

        if args.print_context_diff and isinstance(ctx_out, dict):
            diffs = diff_summary(before_ctx, ctx_out)
            if diffs:
                print("\n[CONTEXT DIFF]")
                for line in diffs:
                    print(line)
                print("[/CONTEXT DIFF]\n")

        if args.debug_intent:
            info = extract_intent_debug(result, context if isinstance(context, dict) else before_ctx)
            print_intent_debug(info)

        # Print output (intent banner if matched, else normal answer)
        if not maybe_print_intent_line(result):
            answer = result.get("answer")
            if not isinstance(answer, str):
                answer = str(answer)
            print(f"Assistant: {answer}")

        if args.show_debug_final_prompt:
            print_final_prompt(context if isinstance(context, dict) else {}, max_chars=args.max_final_prompt_chars)

        if args.show_chunks:
            print_chunks(
                context if isinstance(context, dict) else {},
                max_chunks=args.max_chunks,
                max_chars=args.max_chunk_chars,
            )

    print("\nBye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

