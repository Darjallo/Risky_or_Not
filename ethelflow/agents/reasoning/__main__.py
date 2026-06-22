from __future__ import annotations

import base64
import io
import os
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from ethelflow.agents.reasoning.models import ReasoningRequest, ReasoningResponse
from ethelflow.assets.s3 import s3_manager
from ethelflow.model_catalog import ModelCatalog

# DEFAULT_API_VERSION = os.getenv("ETHELFLOW_AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
INFERENCE_CLASS = os.getenv("ETHELFLOW_INFERENCE_CLASS", "reasoning")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.catalog = ModelCatalog.load()
    app.state.clients: Dict[str, AsyncOpenAI] = {}
    await s3_manager.init()
    yield
    await s3_manager.close()
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


async def stream_generator(response_stream):
    async for chunk in response_stream:
        if chunk.choices and chunk.choices[0].delta:
            content = chunk.choices[0].delta.content
            if content:
                yield content


def _messages_with_optional_images(req: ReasoningRequest, base64_items: list[tuple[str, str]]):
    messages = list(req.messages or [])
    if req.prompt is not None or not messages:
        base_message = {"role": "user", "content": [{"type": "text", "text": req.prompt or ""}]}
        for content_type, content_b64 in base64_items:
            base_message["content"].append(
                {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{content_b64}"}}
            )
        messages.append(base_message)
        return messages

    # attach images to the last message
    last_message = messages[-1]
    last_content = last_message.setdefault("content", [])
    if isinstance(last_content, str):
        last_message["content"] = [{"type": "text", "text": last_content}]
        last_content = last_message["content"]
    for content_type, content_b64 in base64_items:
        last_content.append(
            {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{content_b64}"}}
        )
    return messages


async def _collect_base64_items(req: ReasoningRequest):
    if req.images:
        return [(img.content_type, img.data_base64) for img in req.images]

    if req.document_ids:
        base64_items = []
        for document_id, content_type in zip(req.document_ids, req.content_types or []):
            if not content_type.startswith("image/"):
                raise NotImplementedError(f"Unsupported content type: {content_type}")
            file_object = io.BytesIO()
            await s3_manager.download_file(str(document_id), file_object)
            file_object.seek(0)
            base64_items.append((content_type, base64.b64encode(file_object.read()).decode("utf-8")))
        return base64_items

    if req.document_id:
        file_object = io.BytesIO()
        await s3_manager.download_file(str(req.document_id), file_object)
        file_object.seek(0)
        file_content = file_object.read()
        return [(req.content_type or "application/octet-stream", base64.b64encode(file_content).decode("utf-8"))]

    return []


@app.post("/reasoning_with_document")
async def reasoning_with_document(req: ReasoningRequest, request: Request):
    catalog = _get_catalog(request)

    route = catalog.tenant_inference_route(tenant=req.tenant, class_name=INFERENCE_CLASS)
    provider = route.provider
    client = await _get_openai_client(request, provider.name, provider.endpoint, provider.api_key_env)

    deployment = req.deployment or route.deployment

    base64_items = await _collect_base64_items(req)
    messages = _messages_with_optional_images(req, base64_items)

    completion_params = {
        "model": deployment,  # Azure deployment name
        "messages": messages,
        "max_completion_tokens": 4096,
        "stream": req.stream,
    }
    if req.reasoning_effort is not None:
        completion_params["reasoning_effort"] = req.reasoning_effort

    response = await client.chat.completions.create(**completion_params)

    if req.stream:
        return StreamingResponse(stream_generator(response), media_type="text/event-stream")
    return ReasoningResponse(
        response=response.choices[0].message.content,
        tenant=req.tenant,
        provider=provider.name,
        deployment=deployment,
    )


@app.post("/reasoning")
async def reasoning(req: ReasoningRequest, request: Request):
    catalog = _get_catalog(request)

    route = catalog.tenant_inference_route(tenant=req.tenant, class_name=INFERENCE_CLASS)
    provider = route.provider
    client = await _get_openai_client(request, provider.name, provider.endpoint, provider.api_key_env)

    deployment = req.deployment or route.deployment

    messages = [{"role": "user", "content": [{"type": "text", "text": req.prompt or ""}]}]

    completion_params = {
        "model": deployment,
        "messages": messages,
        "max_completion_tokens": 4096,
        "stream": req.stream,
    }
    if req.reasoning_effort is not None:
        completion_params["reasoning_effort"] = req.reasoning_effort

    response = await client.chat.completions.create(**completion_params)

    if req.stream:
        return StreamingResponse(stream_generator(response), media_type="text/event-stream")
    return ReasoningResponse(
        response=response.choices[0].message.content,
        tenant=req.tenant,
        provider=provider.name,
        deployment=deployment,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

