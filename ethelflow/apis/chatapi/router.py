from __future__ import annotations

import importlib
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ethelflow.apis.common.deps import get_checkpointer, get_pod_store
from ethelflow.data.pods import PodConflict, PodNotFound, PodStore
from ethelflow.handler import handler

from .schemas import ChatCompletionsRequest, ResponsesRequest

router = APIRouter(prefix="/v1", tags=["ChatAPI"])

OWNER_API = "chatapi"
POD_TYPE = "conversation_context"

# environment pods (follow-latest)
ENV_POD_TYPE = "environment"

DEFAULT_TENANT = "ethz"
DEFAULT_FLOW = "rag_intent_chat"


def _strip_debug(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ctx or {})
    out.pop("debug", None)
    return out


def _ensure_thread_id(ctx: Dict[str, Any]) -> uuid.UUID:
    raw = ctx.get("_thread_id")
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except Exception:
            pass
    tid = uuid.uuid4()
    ctx["_thread_id"] = str(tid)
    return tid


def _ensure_base_context(ctx: Dict[str, Any]) -> None:
    # Keep router generic: only normalize basic container keys.
    if not isinstance(ctx.get("messages"), list):
        ctx["messages"] = []
    if not isinstance(ctx.get("routing_state"), dict):
        ctx["routing_state"] = {}
    if not isinstance(ctx.get("rag"), dict):
        ctx["rag"] = {}
    if not isinstance(ctx.get("debug"), dict):
        ctx["debug"] = {}


def _env_pod_id_for_course(*, tenant: str, course_id: str) -> uuid.UUID:
    # Deterministic, not a secret.
    name = f"ethelflow:{OWNER_API}:env:course:{tenant}:{course_id}"
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


async def _apply_course_environment(
    *,
    pod_store: PodStore,
    tenant: str,
    course_id: str,
    ctx: Dict[str, Any],
    env_pod_id_override: Optional[str] = None,
) -> None:
    # Determine env pod id (override allowed for testing/admin tooling)
    if env_pod_id_override:
        try:
            env_pod_id = uuid.UUID(str(env_pod_id_override))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid env_pod_id")
    else:
        env_pod_id = _env_pod_id_for_course(tenant=tenant, course_id=course_id)

    try:
        env_pod = await pod_store.get_pod(pod_id=env_pod_id, tenant=tenant, owner_api=OWNER_API)
    except PodNotFound:
        return

    data = env_pod.data if isinstance(env_pod.data, dict) else {}
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        cfg = data

    # Generic merge: environment can provide template/intents/rag knobs/docs.
    if isinstance(cfg.get("intent_options"), dict) and cfg["intent_options"]:
        ctx["intent_options"] = dict(cfg["intent_options"])

    rag = ctx.get("rag")
    if not isinstance(rag, dict):
        rag = {}
        ctx["rag"] = rag

    template_text = cfg.get("template_text")
    if isinstance(template_text, str) and template_text.strip():
        rag["template"] = template_text

    doc_ids = cfg.get("document_ids")
    if isinstance(doc_ids, list):
        rag["document_ids"] = [str(x) for x in doc_ids if x]

    rag_cfg = cfg.get("rag")
    if isinstance(rag_cfg, dict):
        for k, v in rag_cfg.items():
            if v is not None:
                rag[k] = v


async def _run_flow_once(
    *,
    flow_name: str,
    tenant: str,
    ctx: Dict[str, Any],
    checkpointer: AsyncPostgresSaver,
) -> Dict[str, Any]:
    mod = importlib.import_module(f"ethelflow.flows.{flow_name}")

    # mirror /flow behavior: tenant must be in context
    ctx = dict(ctx or {})
    ctx["tenant"] = tenant

    thread_id = _ensure_thread_id(ctx)

    result = await handler(
        mod=mod,
        context=ctx,
        stream=False,  # start non-streaming; add streaming later
        checkpointer=checkpointer,
        thread_id=thread_id,
        command=None,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected flow result type: {type(result)}")
    return result


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionsRequest,
    resp: Response,
    x_pod_id: Optional[str] = Header(default=None, alias="X-Pod-Id"),
    x_tenant: Optional[str] = Header(default=None, alias="X-Tenant"),
    pod_store: PodStore = Depends(get_pod_store),
    checkpointer: AsyncPostgresSaver = Depends(get_checkpointer),
):
    metadata = req.metadata or {}
    tenant = (x_tenant or metadata.get("tenant") or DEFAULT_TENANT).strip()
    end_user_id = req.user or metadata.get("end_user_id") or None

    course_id = str(metadata.get("course_id") or "default").strip()
    env_pod_id_override = metadata.get("env_pod_id")

    pod_id_raw = metadata.get("pod_id") or x_pod_id
    pod = None

    if pod_id_raw:
        try:
            pod_uuid = uuid.UUID(str(pod_id_raw))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pod_id")
        try:
            pod = await pod_store.get_pod(pod_id=pod_uuid, tenant=tenant, owner_api=OWNER_API)
        except PodNotFound:
            raise HTTPException(status_code=404, detail="Pod not found")
        ctx = dict(pod.data or {})
    else:
        initial_ctx = metadata.get("initial_context")
        ctx = dict(initial_ctx) if isinstance(initial_ctx, dict) else {}
        _ensure_base_context(ctx)

        pod = await pod_store.create_pod(
            tenant=tenant,
            owner_api=OWNER_API,
            pod_type=POD_TYPE,
            end_user_id=end_user_id,
            data=_strip_debug(ctx),
        )

    _ensure_base_context(ctx)

    # Append incoming messages
    msgs = ctx.get("messages")
    if not isinstance(msgs, list):
        msgs = []
        ctx["messages"] = msgs

    for m in req.messages:
        msgs.append({"role": m.role, "content": m.content})

    # Apply environment (follow latest) each request
    await _apply_course_environment(
        pod_store=pod_store,
        tenant=tenant,
        course_id=course_id,
        ctx=ctx,
        env_pod_id_override=env_pod_id_override if isinstance(env_pod_id_override, str) else None,
    )

    flow_name = str(metadata.get("flow") or DEFAULT_FLOW)

    try:
        result = await _run_flow_once(flow_name=flow_name, tenant=tenant, ctx=ctx, checkpointer=checkpointer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Flow error: {e}")

    ctx_out = result.get("context") if isinstance(result.get("context"), dict) else ctx
    ctx_store = _strip_debug(ctx_out)

    try:
        pod = await pod_store.update_pod(
            pod_id=pod.id,
            tenant=tenant,
            owner_api=OWNER_API,
            data=ctx_store,
            expected_rev=None,
        )
    except PodConflict:
        raise HTTPException(status_code=409, detail="Pod update conflict")

    resp.headers["X-Pod-Id"] = str(pod.id)

    answer = result.get("answer")
    if answer is None:
        answer = result.get("output")
    if answer is None:
        answer = ""

    return {
        "id": f"chatcmpl_{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "pod_id": str(pod.id),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": str(answer)},
                "finish_reason": "stop",
            }
        ],
        "debug": (ctx_out.get("debug") if isinstance(ctx_out, dict) else None),
    }


@router.post("/responses")
async def responses(
    req: ResponsesRequest,
    resp: Response,
    x_pod_id: Optional[str] = Header(default=None, alias="X-Pod-Id"),
    x_tenant: Optional[str] = Header(default=None, alias="X-Tenant"),
    pod_store: PodStore = Depends(get_pod_store),
    checkpointer: AsyncPostgresSaver = Depends(get_checkpointer),
):
    metadata = req.metadata or {}
    tenant = (x_tenant or metadata.get("tenant") or DEFAULT_TENANT).strip()
    end_user_id = req.user or metadata.get("end_user_id") or None

    course_id = str(metadata.get("course_id") or "default").strip()
    env_pod_id_override = metadata.get("env_pod_id")

    pod_id_raw = req.conversation or metadata.get("pod_id") or x_pod_id
    pod = None

    if pod_id_raw:
        try:
            pod_uuid = uuid.UUID(str(pod_id_raw))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid conversation/pod id")
        try:
            pod = await pod_store.get_pod(pod_id=pod_uuid, tenant=tenant, owner_api=OWNER_API)
        except PodNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found")
        ctx = dict(pod.data or {})
    else:
        initial_ctx = metadata.get("initial_context")
        ctx = dict(initial_ctx) if isinstance(initial_ctx, dict) else {}
        _ensure_base_context(ctx)

        pod = await pod_store.create_pod(
            tenant=tenant,
            owner_api=OWNER_API,
            pod_type=POD_TYPE,
            end_user_id=end_user_id,
            data=_strip_debug(ctx),
        )

    _ensure_base_context(ctx)

    msgs = ctx.get("messages")
    if not isinstance(msgs, list):
        msgs = []
        ctx["messages"] = msgs

    msgs.append({"role": "user", "content": req.input})

    await _apply_course_environment(
        pod_store=pod_store,
        tenant=tenant,
        course_id=course_id,
        ctx=ctx,
        env_pod_id_override=env_pod_id_override if isinstance(env_pod_id_override, str) else None,
    )

    ###### My MODIFICATION
    # Map request-supplied RAG document scope into flow context.
    # Accept both top-level req.document_ids and metadata["document_ids"].
    doc_ids = None

    # top-level field (preferred)
    if getattr(req, "document_ids", None):
        if isinstance(req.document_ids, list):
            doc_ids = [str(x) for x in req.document_ids if x]

    # fallback: metadata.document_ids
    if not doc_ids:
        meta_doc_ids = metadata.get("document_ids")
        if isinstance(meta_doc_ids, list):
            doc_ids = [str(x) for x in meta_doc_ids if x]

    if doc_ids:
        ctx["document_ids"] = doc_ids
    ######

    flow_name = str(metadata.get("flow") or DEFAULT_FLOW)

    try:
        result = await _run_flow_once(flow_name=flow_name, tenant=tenant, ctx=ctx, checkpointer=checkpointer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Flow error: {e}")

    ctx_out = result.get("context") if isinstance(result.get("context"), dict) else ctx
    ctx_store = _strip_debug(ctx_out)

    try:
        pod = await pod_store.update_pod(
            pod_id=pod.id,
            tenant=tenant,
            owner_api=OWNER_API,
            data=ctx_store,
            expected_rev=None,
        )
    except PodConflict:
        raise HTTPException(status_code=409, detail="Pod update conflict")

    resp.headers["X-Pod-Id"] = str(pod.id)

    answer = result.get("answer")
    if answer is None:
        answer = result.get("output")
    if answer is None:
        answer = ""

    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "model": req.model,
        "conversation": str(pod.id),
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": str(answer)}],
            }
        ],
        "debug": (ctx_out.get("debug") if isinstance(ctx_out, dict) else None),
    }

