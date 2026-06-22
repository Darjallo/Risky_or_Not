# ethelflow/agents/intent/__main__.py
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Request
# from openai import AsyncAzureOpenAI
from openai import AsyncOpenAI
from openai import BadRequestError  # type: ignore

from ethelflow.agents.intent.models import IntentRequest, IntentResponse
from ethelflow.model_catalog import ModelCatalog

logger = logging.getLogger("uvicorn.error")

# DEFAULT_API_VERSION = os.getenv("ETHELFLOW_AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
INFERENCE_CLASS = os.getenv("ETHELFLOW_INFERENCE_CLASS", "low_latency")

# Intent models may burn hidden tokens; give them enough room to emit visible JSON.
DEFAULT_MAX_COMPLETION_TOKENS = int(os.getenv("ETHELFLOW_INTENT_MAX_COMPLETION_TOKENS", "1024"))

# If supported by the deployment, this can reduce hidden reasoning and help avoid empty outputs.
DEFAULT_REASONING_EFFORT = os.getenv("ETHELFLOW_INTENT_REASONING_EFFORT", "low").strip().lower()
if DEFAULT_REASONING_EFFORT not in ("low", "medium", "high"):
    DEFAULT_REASONING_EFFORT = "low"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.catalog = ModelCatalog.load()
    # app.state.clients: Dict[str, AsyncAzureOpenAI] = {}
    app.state.clients: Dict[str, AsyncOpenAI] = {}

    yield
    for c in app.state.clients.values():
        try:
            await c.close()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)


def _get_catalog(request: Request) -> ModelCatalog:
    cat = getattr(request.app.state, "catalog", None)
    if not cat:
        raise RuntimeError("Model catalog not initialized")
    return cat



async def _get_openai_client(
    request: Request,
    provider_name: str,
    endpoint: str,
    api_key_env: str,
) -> AsyncOpenAI:
    clients: Dict[str, AsyncOpenAI] = request.app.state.clients
    if provider_name in clients:
        return clients[provider_name]

    api_key = os.getenv(api_key_env)
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=f"Missing provider API key env var {api_key_env} for provider {provider_name}",
        )

    # For public OpenAI, endpoint should typically be: https://api.openai.com/v1
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
    )
    clients[provider_name] = client
    return client



def _build_system_prompt(intent_options: dict) -> str:
    """
    Keep it compact for low-latency models.
    """
    default_intent = str(intent_options.get("default_intent") or "chat").strip()
    options = intent_options.get("options") if isinstance(intent_options.get("options"), dict) else {}

    lines: List[str] = []
    lines.append("You are an intent classifier for an educational assistant.")
    lines.append("Choose exactly ONE intent from the allowed list.")
    lines.append("If uncertain, choose the default intent.")
    lines.append("")
    lines.append(f"Default intent: {default_intent}")
    lines.append("Allowed intents:")
    for k, v in options.items():
        desc = ""
        exs: List[str] = []
        if isinstance(v, dict):
            desc = str(v.get("description") or "").strip()
            if isinstance(v.get("examples"), list):
                exs = [str(x).strip() for x in v.get("examples") if str(x).strip()]
        if desc:
            lines.append(f"- {k}: {desc}")
        else:
            lines.append(f"- {k}")
        if exs:
            lines.append(f"  examples: {', '.join(exs[:4])}")
    lines.append("")
    lines.append("Return ONLY a JSON object (no markdown, no extra text):")
    lines.append('{"intent": <string>, "confidence": <number 0..1>, "topic": <string|null>, "language": <string|null>}')
    lines.append("topic: short phrase if obvious, else null. language: if obvious, else null.")
    return "\n".join(lines).strip()


def _content_to_text(content: Any) -> str:
    """
    Extract text from possible OpenAI content shapes:
    - str
    - list[{type:"text", text:"..."}]
    - dict with "text"
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
        return ""

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
                continue
            if isinstance(item, dict):
                if item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
                else:
                    t = item.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
        return "\n".join(parts).strip()

    return ""


def _normalize_messages_to_parts(messages: List[dict]) -> List[dict]:
    """
    Normalize to list-of-parts content: [{"type":"text","text":...}]
    """
    out: List[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")

        if isinstance(content, list):
            out.append({"role": role, "content": content})
            continue

        text = _content_to_text(content)
        out.append({"role": role, "content": [{"type": "text", "text": text}]})
    return out


def _extract_raw_from_choice(choice: Any) -> str:
    """
    Robustly extract model output:
    - message.content may be str OR parts list
    - tool_calls may carry function.arguments
    """
    msg = getattr(choice, "message", None)
    if msg is None:
        return ""

    content = getattr(msg, "content", None)
    text = _content_to_text(content)
    if text:
        return text

    tool_calls = getattr(msg, "tool_calls", None)
    if isinstance(tool_calls, list) and tool_calls:
        tc0 = tool_calls[0]
        fn = getattr(tc0, "function", None)
        args = getattr(fn, "arguments", None) if fn is not None else None
        if isinstance(args, str) and args.strip():
            return args.strip()

    return ""


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _coerce_to_result(raw: str, allowed: set[str], default_intent: str) -> Tuple[dict, str]:
    s = (raw or "").strip()
    if not s:
        return (
            {"intent": default_intent, "topic": None, "confidence": 0.0, "language": None, "reason": "empty_raw"},
            "empty_raw",
        )

    s2 = _CODE_FENCE_RE.sub("", s).strip()

    # JSON path
    try:
        obj = json.loads(s2)
        if isinstance(obj, dict):
            intent = str(obj.get("intent") or default_intent).strip()
            conf = obj.get("confidence", 0.0)
            topic = obj.get("topic", None)
            lang = obj.get("language", None)

            if intent not in allowed:
                intent = default_intent

            try:
                conf_f = float(conf)
            except Exception:
                conf_f = 0.0
            conf_f = max(0.0, min(1.0, conf_f))

            if topic is not None and not isinstance(topic, str):
                topic = str(topic)
            if isinstance(topic, str):
                topic = topic.strip() or None

            if lang is not None and not isinstance(lang, str):
                lang = str(lang)
            if isinstance(lang, str):
                lang = lang.strip() or None

            return (
                {"intent": intent, "topic": topic, "confidence": conf_f, "language": lang, "reason": "ok"},
                "ok",
            )
    except Exception:
        pass

    # single-word fallback
    token = s2.split()[0].strip().strip('",.()[]{}')
    if token in allowed:
        return (
            {"intent": token, "topic": None, "confidence": 1.0, "language": None, "reason": "ok_word"},
            "ok_word",
        )

    return (
        {"intent": default_intent, "topic": None, "confidence": 0.0, "language": None, "reason": "parse_failed"},
        "parse_failed",
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}


async def _call_once(
    client: AsyncOpenAI,
    deployment: str,
    messages: List[dict],
    *,
    max_completion_tokens: int,
    reasoning_effort: str | None,
) -> Any:
    """
    IMPORTANT: do NOT pass temperature for reasoning deployments (often unsupported).
    Some deployments also reject reasoning_effort; we try with it and fall back without.
    """
    base_params: Dict[str, Any] = {
        "model": deployment,
        "messages": messages,
        "max_tokens": max_completion_tokens,
        "stream": False,
    }

    if reasoning_effort:
        try:
            return await client.chat.completions.create(**base_params, reasoning_effort=reasoning_effort)
        except BadRequestError as e:
            # If the deployment rejects reasoning_effort, retry once without it.
            logger.warning(f"[intent] deployment={deployment} rejected reasoning_effort={reasoning_effort}: {e}")
            return await client.chat.completions.create(**base_params)

    return await client.chat.completions.create(**base_params)


@app.post("/intent")
async def intent(req: IntentRequest, request: Request):
    catalog = _get_catalog(request)

    route = catalog.tenant_inference_route(tenant=req.tenant, class_name=INFERENCE_CLASS)
    provider = route.provider
    client = await _get_openai_client(request, provider.name, provider.endpoint, provider.api_key_env)

    deployment = req.deployment or route.deployment

    if not isinstance(req.intent_options, dict) or not req.intent_options:
        raise HTTPException(status_code=400, detail="intent_options must be a non-empty object")

    intent_options = req.intent_options
    default_intent = str(intent_options.get("default_intent") or "chat").strip()
    options = intent_options.get("options") if isinstance(intent_options.get("options"), dict) else {}
    allowed = set(str(k) for k in options.keys()) if options else set()
    if not allowed:
        allowed = {"chat"}
        default_intent = "chat"

    system_prompt = _build_system_prompt(intent_options)

    # Prefer req.messages; fallback to req.prompt
    user_msgs: List[dict]
    if req.messages is not None:
        user_msgs = [m for m in req.messages if isinstance(m, dict)]
    else:
        user_msgs = [{"role": "user", "content": req.prompt or ""}]

    messages = [{"role": "system", "content": system_prompt}] + user_msgs
    messages = _normalize_messages_to_parts(messages)

    response = await _call_once(
        client=client,
        deployment=deployment,
        messages=messages,
        max_completion_tokens=DEFAULT_MAX_COMPLETION_TOKENS,
        reasoning_effort=DEFAULT_REASONING_EFFORT,
    )
    choice0 = response.choices[0]
    raw = _extract_raw_from_choice(choice0)

    # If raw is empty, retry once with ONLY the last user message.
    if not raw:
        last_user = None
        for m in reversed(user_msgs):
            if str(m.get("role", "")).lower() == "user":
                last_user = m
                break
        if last_user is None:
            last_user = {"role": "user", "content": ""}

        retry_messages = [{"role": "system", "content": system_prompt}, last_user]
        retry_messages = _normalize_messages_to_parts(retry_messages)

        response2 = await _call_once(
            client=client,
            deployment=deployment,
            messages=retry_messages,
            max_completion_tokens=DEFAULT_MAX_COMPLETION_TOKENS,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
        )
        raw = _extract_raw_from_choice(response2.choices[0]) or raw

    result_dict, reason = _coerce_to_result(raw=raw, allowed=allowed, default_intent=default_intent)

    # Helpful but not “debug-flag” gated: a single line when output is empty.
    if reason == "empty_raw":
        fr = getattr(choice0, "finish_reason", None)
        logger.warning(f"[intent] empty_raw (deployment={deployment} finish_reason={fr})")

    return IntentResponse(
        result=result_dict,
        tenant=req.tenant,
        provider=provider.name,
        deployment=deployment,
        raw=raw,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

