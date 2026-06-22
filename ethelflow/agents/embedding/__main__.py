from __future__ import annotations

import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from openai import RateLimitError, APITimeoutError, APIError 
from openai import AsyncOpenAI


from ethelflow.agents.embedding.models import EmbeddingRequest, EmbeddingResponse
from ethelflow.model_catalog import ModelCatalog

####### batching of embedding for long documents
from typing import List, Iterable, TypeVar

T = TypeVar("T")

def _batched(items: List[T], batch_size: int) -> Iterable[List[T]]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]
####### end of insertion

logger = logging.getLogger("uvicorn.error")

# DEFAULT_API_VERSION = os.getenv("ETHELFLOW_AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
DEFAULT_TENANT = os.getenv("ETHELFLOW_DEFAULT_TENANT", "")  # optional


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load once; routes are resolved per request.
    app.state.catalog = ModelCatalog.load()
    # app.state.clients: Dict[str, AsyncAzureOpenAI] = {}  # provider_name -> client
    app.state.clients: Dict[str, AsyncOpenAI] = {}

    yield
    # Close any created clients
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


MAX_RETRY_TIME = 30.0
MAX_RETRIES = 11
BASE_DELAY = 2.0


async def with_backoff(fn, *args, **kwargs):
    for attempt in range(MAX_RETRIES):
        try:
            return await fn(*args, **kwargs)
        except (RateLimitError, APITimeoutError, APIError) as e:
            status_code = getattr(e, "status_code", None)
            if status_code not in (429, 500, 503):
                raise

            backoff_delay = BASE_DELAY * (2**attempt) + random.uniform(0, 0.5)
            delay = min(backoff_delay, MAX_RETRY_TIME)

            if attempt == MAX_RETRIES - 1:
                raise

            logger.warning(
                f"Transient error (HTTP {status_code}); retry in {delay:.2f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )
            await asyncio.sleep(delay)


@app.post("/embedding", response_model=EmbeddingResponse)
async def embed(req: EmbeddingRequest, request: Request) -> EmbeddingResponse:
    tenant = req.tenant or DEFAULT_TENANT
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant is required (or set ETHELFLOW_DEFAULT_TENANT)")

    catalog = _get_catalog(request)

    # Resolve via catalog unless caller explicitly overrides deployment (debug only)
    if req.deployment:
        route = catalog.tenant_embedding_route(tenant=tenant, space=req.space)
        deployment = req.deployment
    else:
        route = catalog.tenant_embedding_route(tenant=tenant, space=req.space)
        deployment = route.deployment

    provider = route.provider
    client = await _get_openai_client(request, provider.name, provider.endpoint, provider.api_key_env)

    logger.info(
        f"Embedding tenant={tenant} space={route.space} provider={provider.name} deployment={deployment} n={len(req.texts)}"
    )

    # response = await with_backoff(
    #     client.embeddings.create,
    #     input=req.texts,
    #     model=deployment,  # Azure deployment name
    # )

    
    # usage = None
    # try:
    #     usage = response.usage.model_dump() if response.usage is not None else None
    # except Exception:
    #     usage = None
    
    ###### start of insertion
    BATCH_SIZE = int(os.getenv("ETHELFLOW_EMBEDDING_BATCH_SIZE", "64"))

    all_embeddings: List[List[float]] = []
    usage_total = {"prompt_tokens": 0, "total_tokens": 0}

    for batch in _batched(req.texts, BATCH_SIZE):
        response = await with_backoff(
            client.embeddings.create,
            input=batch,
            model=deployment,
        )
        all_embeddings.extend([item.embedding for item in response.data])
    
        # best-effort usage accumulation (fields vary by provider)
        usage = None
        try:
            if response.usage is not None:
                usage = response.usage.model_dump()
                usage_total["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
                usage_total["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        except Exception:
            usage = None
            pass

    return EmbeddingResponse(
        #embeddings=[item.embedding for item in response.data],
        embeddings=all_embeddings,
        tenant=tenant,
        space=route.space,
        provider=provider.name,
        deployment=deployment,
        usage=usage,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

