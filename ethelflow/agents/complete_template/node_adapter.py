from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp
import os


from ethelflow.agents.complete_template.models import (
    CompleteTemplateRequest,
    CompleteTemplateResponse,
)

# COMPLETE_TEMPLATE_URL: str = "http://complete-template.default.svc:8000/complete_template"
COMPLETE_TEMPLATE_URL: str = "http://complete-template:8000/complete_template"

def complete_template_node(
    template_key: str = "template",
    fields_key: str = "fields",
    normalize_empties_key: str = "normalize_empties",  # optional; defaults True
    output_key: str = "complete_template_response",
    output_rendered_key: str = "rendered_template",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        template = state.get(template_key)
        if not isinstance(template, str) or not template.strip():
            raise ValueError(f"Expected non-empty str for {template_key}, got {template!r}")

        fields = state.get(fields_key, {})
        if not isinstance(fields, dict):
            raise ValueError(f"Expected dict for {fields_key}, got {type(fields)}")

        normalize_empties = state.get(normalize_empties_key, True)
        if not isinstance(normalize_empties, bool):
            raise ValueError(f"Expected bool for {normalize_empties_key}, got {normalize_empties!r}")

        req = CompleteTemplateRequest(
            template=template,
            fields=fields,
            normalize_empties=normalize_empties,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                COMPLETE_TEMPLATE_URL,
                json=req.model_dump(mode="json"),
                timeout=60,
            ) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    raise ValueError(f"complete-template HTTP {resp.status}: {txt}")
                payload = await resp.json()

        data = CompleteTemplateResponse.model_validate(payload)
        if not data.success:
            raise ValueError(f"complete_template failed: {data.message}")

        yield {
            output_key: data.model_dump(mode="json"),
            output_rendered_key: data.rendered,
        }

    return node

