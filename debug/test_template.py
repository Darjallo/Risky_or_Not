#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

import httpx


DEFAULT_BASE_URL = "http://localhost:8080"  # if you port-forward the service, adjust below
DEFAULT_SERVICE_URL = "http://localhost:8000"  # direct port-forward to complete-template svc


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def call_complete_template(base_url: str, template: str, fields: Dict[str, Any], normalize_empties: bool = True) -> str:
    url = base_url.rstrip("/") + "/complete_template"
    body = {
        "template": template,
        "fields": fields,
        "normalize_empties": normalize_empties,
    }

    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=body)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        data = r.json()

    if not data.get("success"):
        raise RuntimeError(f"Service returned success=false: {data}")

    return data.get("rendered", "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Test complete-template service (chevron/mustache).")
    ap.add_argument(
        "--url",
        default=DEFAULT_SERVICE_URL,
        help=f"Base URL for complete-template service (default: {DEFAULT_SERVICE_URL})",
    )
    ap.add_argument("--no-normalize", action="store_true", help="Disable normalize_empties")

    args = ap.parse_args()

    template = """You are an assistant.

{{#prompt}}
The user asks:
{{prompt}}
{{/prompt}}

Note the context.

{{#weather}}
The weather is:
{{weather}}
{{/weather}}

{{#prompt}}{{#weather}}
(Only shown if BOTH prompt and weather are present)
{{/weather}}{{/prompt}}

Answer in the language of the dialogue.
"""

    # Case A: weather present
    fields_a = {
        "prompt": "What is the meaning of life?",
        "weather": "Sunny day",
    }

    # Case B: weather empty -> weather blocks should disappear
    fields_b = {
        "prompt": "What is the meaning of life?",
        "weather": "",
    }

    print("\n=== TEMPLATE ===\n")
    print(template)

    print("\n=== CASE A (weather present) ===\n")
    print("fields =", json.dumps(fields_a, indent=2))
    rendered_a = call_complete_template(args.url, template, fields_a, normalize_empties=not args.no_normalize)
    print("\n--- rendered ---\n")
    print(rendered_a)

    print("\n=== CASE B (weather empty string) ===\n")
    print("fields =", json.dumps(fields_b, indent=2))
    rendered_b = call_complete_template(args.url, template, fields_b, normalize_empties=not args.no_normalize)
    print("\n--- rendered ---\n")
    print(rendered_b)


if __name__ == "__main__":
    main()

