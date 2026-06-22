#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Optional, Tuple, Dict, Any

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Capture BOTH value and id from the SAME Interrupt(...) occurrence.
INTERRUPT_RE = re.compile(
    r"Interrupt\(\s*value=(?P<lit>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"),\s*id='(?P<id>[0-9a-fA-F]+)'\s*\)",
    re.DOTALL,
)

THREAD_ID_RE = re.compile(r"thread_id': '(" + UUID_RE.pattern + r")'")
RUN_ID_RE = re.compile(r"run_id': '(" + UUID_RE.pattern + r")'")

FEEDBACK_FIELD_RE = re.compile(
    r"(?:'feedback'\s*:\s*|\"feedback\"\s*:\s*)(?P<lit>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")",
    re.DOTALL,
)


def _http_get_json(url: str, timeout_s: int = 30) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def _print_block(text: str) -> None:
    line = "=" * 72
    print("\n" + line)
    print(text.rstrip())
    print(line + "\n")
    sys.stdout.flush()


def _decode_python_string_literal(lit: str) -> str:
    try:
        return ast.literal_eval(lit)
    except Exception:
        if len(lit) >= 2 and lit[0] == lit[-1] and lit[0] in ("'", '"'):
            return lit[1:-1]
        return lit


def _discover_paths(base_url: str) -> Optional[list[str]]:
    code, spec = _http_get_json(base_url.rstrip("/") + "/openapi.json")
    if code != 200 or "paths" not in spec:
        return None
    return sorted(spec["paths"].keys())


def _stream_post(url: str, payload: Dict[str, Any], timeout_s: int = 300):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            yield chunk.decode("utf-8", errors="replace")


def _extract_run_id(buf: str) -> Optional[str]:
    m = THREAD_ID_RE.search(buf)
    if m:
        return m.group(1)
    m = RUN_ID_RE.search(buf)
    if m:
        return m.group(1)
    m = UUID_RE.search(buf)
    return m.group(0) if m else None


def _extract_interrupt(buf: str) -> Tuple[Optional[str], Optional[str]]:
    m = INTERRUPT_RE.search(buf)
    if not m:
        return None, None
    interrupt_id = m.group("id")
    interrupt_value = _decode_python_string_literal(m.group("lit"))
    return interrupt_id, interrupt_value


def _extract_feedback(buf: str) -> Optional[str]:
    m = FEEDBACK_FIELD_RE.search(buf)
    if m:
        return _decode_python_string_literal(m.group("lit"))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Endpoint-only CLI client for EthelFlow quiz flow.")
    ap.add_argument("topic", help="Quiz topic (e.g., Multiplication)")
    ap.add_argument("--base-url", default="http://localhost:8080", help="EthelFlow base URL")
    ap.add_argument("--tenant", default="ethz", help="Tenant")
    ap.add_argument("--inference-class", default="reasoning", help="Inference class (default: reasoning)")
    ap.add_argument("--deployment", default=None, help="Optional deployment override (if omitted, tenant routing decides)")
    ap.add_argument("--reasoning-effort", default=None, help="Optional reasoning effort hint (e.g., low/medium/high)")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    start_url = base + "/flow"

    ctx: Dict[str, Any] = {
        "topic": args.topic,
        "tenant": args.tenant,
        "inference_class": args.inference_class,
    }
    if args.deployment:
        ctx["deployment"] = args.deployment
    if args.reasoning_effort:
        ctx["reasoning_effort"] = args.reasoning_effort

    start_payload = {
        "flow": "quiz",
        "tenant": args.tenant,  # keep top-level too
        "context": ctx,
        "stream": True,
    }

    buf = ""
    run_id: Optional[str] = None
    interrupt_id: Optional[str] = None
    question_text: Optional[str] = None

    try:
        for chunk in _stream_post(start_url, start_payload):
            buf += chunk
            if run_id is None:
                run_id = _extract_run_id(buf)

            iid, qtxt = _extract_interrupt(buf)
            if iid and qtxt:
                interrupt_id = iid
                question_text = qtxt
                break

            if len(buf) > 2_000_000:
                buf = buf[-1_000_000:]

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} starting quiz at {start_url}\n{raw}")
        return 2
    except Exception as e:
        print(f"Error starting quiz: {e}")
        return 2

    if not run_id:
        print("ERROR: Could not detect a run_id/thread_id in the stream.")
        return 1
    if not interrupt_id or not question_text:
        print("ERROR: Did not find an Interrupt(value=..., id='...') in the stream.")
        return 1

    _print_block(question_text)
    answer = input("Your answer> ").strip()

    continue_url = f"{base}/flow/{run_id}/continue"
    cont_payload = {"data": {interrupt_id: answer}, "stream": True}

    # helpful debug line so you can see what key you’re using
    print(f"(debug) continuing run_id={run_id} with interrupt_id={interrupt_id}", file=sys.stderr)

    paths = _discover_paths(base)
    if paths is not None and "/flow/{run_id}/continue" not in paths:
        print(
            "\nWARNING: /flow/{run_id}/continue is NOT listed in /openapi.json for this server.\n"
            "That usually means you are NOT talking to the same EthelFlow app/router that the repo describes.\n"
        )
        print("Paths containing '/flow' from OpenAPI:")
        for p in paths:
            if "/flow" in p:
                print(" ", p)
        print()

    buf2 = ""
    try:
        for chunk in _stream_post(continue_url, cont_payload):
            buf2 += chunk
            fb = _extract_feedback(buf2)
            if fb:
                _print_block(fb)
                return 0

            if len(buf2) > 2_000_000:
                buf2 = buf2[-1_000_000:]

        fb = _extract_feedback(buf2)
        if fb:
            _print_block(fb)
            return 0

        print("Resumed, but did not detect a 'feedback' field in the streamed output.")
        print("\n--- tail of response ---\n" + buf2[-2000:])
        return 0

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} on continue URL: {continue_url}\n{raw}\n")
        return 3
    except Exception as e:
        print(f"Error continuing quiz: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

