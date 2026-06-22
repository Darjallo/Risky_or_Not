from __future__ import annotations

import logging
from typing import Any, Dict

import chevron
from fastapi import FastAPI, HTTPException

from ethelflow.agents.complete_template.models import (
    CompleteTemplateRequest,
    CompleteTemplateResponse,
)

logger = logging.getLogger("uvicorn.error")

app = FastAPI()


def _normalize_value(v: Any) -> Any:
    """Turn "empty" values into False so Mustache sections don't render."""
    if v is None:
        return False

    if isinstance(v, str):
        return v if v.strip() else False

    if isinstance(v, (list, tuple, set)):
        return [_normalize_value(x) for x in v] if len(v) else False

    if isinstance(v, dict):
        if not v:
            return False
        return {k: _normalize_value(val) for k, val in v.items()}

    # numbers/bools/other objects: keep as-is
    return v


def _normalize_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _normalize_value(v) for k, v in (fields or {}).items()}


@app.post("/complete_template", response_model=CompleteTemplateResponse)
async def complete_template(req: CompleteTemplateRequest) -> CompleteTemplateResponse:
    """
    Render a Mustache template with optional conditional blocks.

    - Use sections for conditional blocks:
        {{#weather}} ... {{weather}} ... {{/weather}}

    - Use nested sections for "all variables present":
        {{#prompt}}{{#weather}} ... {{/weather}}{{/prompt}}
    """
    try:
        template = req.template
        fields = req.fields or {}

        if req.normalize_empties:
            fields = _normalize_fields(fields)

        # chevron escapes HTML for {{var}} by default (like Mustache).
        # If you want raw insertion, use {{{var}}} in the template.
        rendered = chevron.render(template, fields)

        return CompleteTemplateResponse(success=True, rendered=rendered)

    except Exception as e:
        logger.exception("complete_template failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

