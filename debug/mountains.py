#!/usr/bin/env python3
"""
Async streaming client for EthelFlow /flow endpoint.

Requires:
  pip install httpx

This version is a minimum-change drop-in replacement for your old script, but
it now calls the flow using the *catalog-based routing* parameters:
  - tenant is provided (top-level AND inside context, since /flow currently
    doesn't pass flow_request.tenant into the flow context)
  - inference_class is provided (so flows/services can resolve deployments
    via model_catalog.yaml instead of hardcoding deployments)
  - deployment is NOT provided
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, Optional

import httpx


FLOW_URL = "http://localhost:8080/flow"


async def stream_flow_response(
    url: str,
    payload: Dict[str, Any],
    timeout_s: float = 300.0,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    """
    Send request and print streaming response incrementally.

    Handles common streaming formats:
      - plain newline-delimited text
      - SSE where lines start with "data: ..."
    """
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)

    timeout = httpx.Timeout(timeout_s)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=merged_headers, json=payload) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line:
                    continue

                # SSE-style "data: ..." lines
                if line.startswith("data:"):
                    line = line[len("data:") :].lstrip()

                print(line, flush=True)


async def amain() -> int:
    tenant = "ethz"
    inference_class = "reasoning"

    payload: Dict[str, Any] = {
        "flow": "reasoning_multiprompt",

        # Keep this for forward-compat (even though /flow currently doesn't
        # inject it into flow context).
        "tenant": tenant,

        # IMPORTANT: also put tenant/class into context so the flow can route
        # correctly today (given current flows.py behavior).
        "context": {
            "tenant": tenant,
            "inference_class": inference_class,

            "prompt_1": "Can you give me 20 mountain peaks over 5000m?",
            "prompt_2": "Now can you list these peaks by their height, in descending order?",
            "reasoning_effort": "low",

            # Deliberately NOT sending "deployment" anymore; routing should come
            # from (tenant, inference_class) via the model catalog.
        },

        "stream": True,
    }

    # Optional: also pass tenant as a header (harmless if unused, helpful if
    # you later standardize on header-based tenant propagation).
    headers = {
        "X-Tenant": tenant,
        "X-Ethelflow-Tenant": tenant,
    }

    try:
        await stream_flow_response(FLOW_URL, payload, headers=headers)
    except httpx.HTTPStatusError as e:
        print(f"HTTP error {e.response.status_code}: {e}", file=sys.stderr)
        try:
            print(e.response.text, file=sys.stderr)
        except Exception:
            pass
        return 1
    except httpx.RequestError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))

