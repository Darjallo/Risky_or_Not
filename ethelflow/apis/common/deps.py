from __future__ import annotations

from fastapi import Depends, Request
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession

from ethelflow.data.db_utils import get_session
from ethelflow.data.pods import PostgresPodStore, PodStore


async def get_checkpointer(request: Request) -> AsyncPostgresSaver:
    checkpointer: AsyncPostgresSaver = request.app.state.checkpointer
    if not checkpointer:
        raise ValueError("Checkpointer is not initialized")
    return checkpointer


async def get_pod_store(session: AsyncSession = Depends(get_session)) -> PodStore:
    return PostgresPodStore(session=session)

