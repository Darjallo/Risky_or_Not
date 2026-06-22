# ethelflow/apis/admin/router.py
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ethelflow.data.db_utils import get_session
from ethelflow.data.pods import PostgresPodStore, PodNotFound, deterministic_pod_id

router = APIRouter(prefix="/admin", tags=["Admin"])


class EnvironmentUpsert(BaseModel):
    # Arbitrary JSON. This is intentionally schema-less and API-agnostic.
    config: Dict[str, Any]


@router.get("/environments/{owner_api}/{tenant}/{env_type}/{env_id}")
async def get_environment(
    owner_api: str,
    tenant: str,
    env_type: str,
    env_id: str,
    session: AsyncSession = Depends(get_session),
):
    store = PostgresPodStore(session)
    key = f"{env_type}:{env_id}"
    pod_id = deterministic_pod_id(tenant=tenant, owner_api=owner_api, pod_type="environment", key=key)
    try:
        pod = await store.get_pod(pod_id=pod_id, tenant=tenant, owner_api=owner_api)
    except PodNotFound:
        raise HTTPException(status_code=404, detail="Environment not found")

    data = pod.data if isinstance(pod.data, dict) else {}
    return {
        "pod_id": str(pod.id),
        "tenant": pod.tenant,
        "owner_api": pod.owner_api,
        "env_type": env_type,
        "env_id": env_id,
        "config": data.get("config", data),
        "rev": pod.rev,
        "updated_at": getattr(pod, "updated_at", None),
    }


@router.put("/environments/{owner_api}/{tenant}/{env_type}/{env_id}")
async def put_environment(
    owner_api: str,
    tenant: str,
    env_type: str,
    env_id: str,
    req: EnvironmentUpsert = Body(...),
    session: AsyncSession = Depends(get_session),
):
    store = PostgresPodStore(session)
    key = f"{env_type}:{env_id}"
    pod_id = deterministic_pod_id(tenant=tenant, owner_api=owner_api, pod_type="environment", key=key)

    data = {
        "env_type": env_type,
        "env_id": env_id,
        "config": req.config,
    }

    try:
        pod = await store.get_pod(pod_id=pod_id, tenant=tenant, owner_api=owner_api)
        pod = await store.update_pod(pod_id=pod_id, tenant=tenant, owner_api=owner_api, data=data)
        created = False
    except PodNotFound:
        pod = await store.create_pod(
            pod_id=pod_id,
            tenant=tenant,
            owner_api=owner_api,
            pod_type="environment",
            end_user_id=None,
            data=data,
        )
        created = True

    return {"ok": True, "created": created, "pod_id": str(pod.id), "rev": pod.rev}

