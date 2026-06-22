import asyncio
import importlib
import json
import logging
import uuid
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from ethelflow.handler import handler
from ethelflow.models import FlowContinueRequest, FlowRequest

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/flow", tags=["Flows"])


async def get_checkpointer(request: Request) -> AsyncPostgresSaver:
    checkpointer: AsyncPostgresSaver = request.app.state.checkpointer
    if not checkpointer:
        raise ValueError("Checkpointer is not initialized")
    return checkpointer


@router.post("/{run_id}/continue", summary="Continue Flow")
async def continue_flow(
    run_id: UUID = Path(..., description="Identifier of the flow run returned by `/flow/start`."),
    continue_request: FlowContinueRequest = Body(...),
    checkpointer=Depends(get_checkpointer),
):
    checkpoint: CheckpointTuple = await checkpointer.aget_tuple(
        {"configurable": {"thread_id": str(run_id)}}
    )
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Run ID not found")

    context = checkpoint.checkpoint.get("channel_values") or {}
    mod = importlib.import_module(f"ethelflow.flows.{checkpoint.metadata.get('flow')}")

    logger.info(f"Continuing flow {checkpoint.metadata.get('flow')} for run_id: {run_id}")

    command = Command(resume=continue_request.data)
    return await handler(
        mod=mod,
        context=context,
        stream=continue_request.stream,
        checkpointer=checkpointer,
        thread_id=run_id,
        command=command,
    )


@router.post("", summary="Run Flow")
async def run_flow(
    flow_request: FlowRequest = Body(...),
    checkpointer=Depends(get_checkpointer),
):
    mod = importlib.import_module(f"ethelflow.flows.{flow_request.flow}")

    thread_id = uuid.uuid4()
    logger.info(f"Starting flow {flow_request.flow} with run_id: {thread_id}")

    # IMPORTANT: tenant must be part of the state so nodes can route.
    context = dict(flow_request.context or {})
    context["tenant"] = flow_request.tenant

    return await handler(
        mod=mod,
        context=context,
        stream=flow_request.stream,
        checkpointer=checkpointer,
        thread_id=thread_id,
        command=None,
    )


flow_streams = defaultdict(asyncio.Queue)


@router.post("/start", summary="Start Flow")
async def start_flow(
    flow_request: FlowRequest = Body(...),
    checkpointer=Depends(get_checkpointer),
):
    mod = importlib.import_module(f"ethelflow.flows.{flow_request.flow}")
    thread_id = uuid.uuid4()

    # IMPORTANT: tenant must be part of the state so nodes can route.
    context = dict(flow_request.context or {})
    context["tenant"] = flow_request.tenant

    async def run():
        await flow_streams[thread_id].put("event: start\n data: {}\n\n")
        async for event in mod.run(
            thread_id=thread_id,
            context=context,
            stream=True,
            checkpointer=checkpointer,
        ):
            await flow_streams[thread_id].put(event)
        await flow_streams[thread_id].put(None)

    asyncio.create_task(run())
    return {"run_id": thread_id}


@router.get("/{run_id}/attach", summary="Attach to Flow (SSE)")
async def attach(run_id: UUID = Path(...)):
    queue = flow_streams.get(run_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Run ID not found")

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                yield "event: complete\n data: {}\n\n"
                break
            yield f"event: stream\n data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{run_id}/history", summary="Get Flow History")
async def get_run_history(run_id: UUID = Path(...), checkpointer=Depends(get_checkpointer)):
    logger.info(f"Fetching history for run_id: {run_id}")
    checkpoints = [
        checkpoint._asdict()
        async for checkpoint in checkpointer.alist({"configurable": {"thread_id": str(run_id)}})
    ]
    return checkpoints


@router.get("/{run_id}/status", summary="Get Flow Status")
async def get_run_status(run_id: UUID = Path(...), checkpointer=Depends(get_checkpointer)):
    logger.info(f"Fetching status for run_id: {run_id}")
    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": str(run_id)}})
    return checkpoint._asdict()

