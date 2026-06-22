import logging
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional

import magic
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.assets.s3 import s3_manager
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import Asset, EthelDocument

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/assets", tags=["Assets"])

DIR_MARKER_FILENAME = ".keep"
_VERSIONED_RE = re.compile(r"^(?P<stem>.+)\.(?P<ver>\d+)\.(?P<ext>[^./]+)$")


@dataclass(frozen=True)
class ParsedPath:
    raw: str
    tenant: str
    collection: str
    subpath: str                # '' or 'a/b/c'
    filename: str               # BASE filename stored in DB (e.g. angular.pdf)
    requested_filename: str     # what the user asked for (e.g. angular.1.pdf)
    requested_version: Optional[int]  # from foo.N.ext


def _normalize_logical_path(path: str) -> ParsedPath:
    if path is None:
        raise HTTPException(status_code=400, detail="path is required")

    p = re.sub(r"/{2,}", "/", path.strip())
    if not p.startswith("/"):
        raise HTTPException(status_code=400, detail="path must start with '/'")

    parts = [seg for seg in p.split("/") if seg]
    if len(parts) < 3:
        raise HTTPException(
            status_code=400,
            detail="path must be /tenant/collection/[subdirs]/filename.ext",
        )

    if any(seg in (".", "..") for seg in parts):
        raise HTTPException(
            status_code=400, detail="path must not contain '.' or '..' segments"
        )

    tenant = parts[0]
    collection = parts[1]
    requested_filename = parts[-1]
    subdirs = parts[2:-1]
    subpath = "/".join(subdirs) if subdirs else ""

    requested_version: Optional[int] = None
    filename = requested_filename  # default (non-versioned)

    m = _VERSIONED_RE.match(requested_filename)
    if m:
        requested_version = int(m.group("ver"))
        # IMPORTANT: normalize filename used for asset lookup/storage
        filename = f"{m.group('stem')}.{m.group('ext')}"

    return ParsedPath(
        raw=p,
        tenant=tenant,
        collection=collection,
        subpath=subpath,
        filename=filename,
        requested_filename=requested_filename,
        requested_version=requested_version,
    )


async def _get_asset_by_path(session: AsyncSession, pp: ParsedPath) -> Optional[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.tenant == pp.tenant)
        .where(Asset.collection == pp.collection)
        .where(Asset.subpath == pp.subpath)
        .where(Asset.filename == pp.filename)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def _get_doc(session: AsyncSession, doc_id: uuid.UUID) -> Optional[EthelDocument]:
    res = await session.execute(select(EthelDocument).where(EthelDocument.id == doc_id))
    return res.scalar_one_or_none()


async def _get_doc_for_version(
    session: AsyncSession, asset_id: uuid.UUID, version: int
) -> Optional[EthelDocument]:
    res = await session.execute(
        select(EthelDocument)
        .where(EthelDocument.asset_id == asset_id)
        .where(EthelDocument.version == version)
    )
    return res.scalar_one_or_none()


async def _max_version(session: AsyncSession, asset_id: uuid.UUID) -> int:
    res = await session.execute(
        select(EthelDocument.version).where(EthelDocument.asset_id == asset_id)
    )
    versions = [r[0] for r in res.all() if r[0] is not None]
    return max(versions) if versions else 0


async def _recompute_latest(session: AsyncSession, asset: Asset) -> None:
    res = await session.execute(
        select(EthelDocument.id, EthelDocument.version).where(EthelDocument.asset_id == asset.id)
    )
    rows = [(r[0], r[1]) for r in res.all() if r[1] is not None]
    if not rows:
        asset.latest_document_id = None
        return
    latest_id, _ = max(rows, key=lambda x: x[1])
    asset.latest_document_id = latest_id


async def _ensure_dir_markers(session: AsyncSession, tenant: str, collection: str, subpath: str) -> None:
    prefixes: List[str] = []
    if subpath:
        segs = subpath.split("/")
        for i in range(1, len(segs) + 1):
            prefixes.append("/".join(segs[:i]))

    markers = [("", DIR_MARKER_FILENAME)] + [(pref, DIR_MARKER_FILENAME) for pref in prefixes]

    for sp, fn in markers:
        res = await session.execute(
            select(Asset)
            .where(Asset.tenant == tenant)
            .where(Asset.collection == collection)
            .where(Asset.subpath == sp)
            .where(Asset.filename == fn)
        )
        if res.scalar_one_or_none():
            continue

        session.add(
            Asset(
                tenant=tenant,
                collection=collection,
                subpath=sp,
                filename=fn,
                latest_document_id=None,
            )
        )


@router.post("", summary="Upload / overwrite an asset by logical path")
async def upload_asset(
    path: str = Query(..., description="e.g. /ethz/physics/mechanics/angular.pdf or angular.2.pdf"),
    overwrite: bool = Query(True, description="If false and the target exists, return 409"),
    title: Optional[str] = Query(None, description="Optional document title (defaults to filename)"),
    file: UploadFile = File(..., description="multipart/form-data file"),
    session: AsyncSession = Depends(get_session),
):
    pp = _normalize_logical_path(path)

    data_bytes = await file.read()
    if not data_bytes:
        raise HTTPException(status_code=400, detail="Empty upload")

    mime = magic.Magic(mime=True)
    content_type = mime.from_buffer(data_bytes)

    # ensure directory markers exist
    await _ensure_dir_markers(session, pp.tenant, pp.collection, pp.subpath)

    asset = await _get_asset_by_path(session, pp)
    existed_before = asset is not None
    if not asset:
        asset = Asset(
            tenant=pp.tenant,
            collection=pp.collection,
            subpath=pp.subpath,
            filename=pp.filename,  # BASE filename
            latest_document_id=None,
        )
        session.add(asset)
        await session.flush()  # assign asset.id
    # If the caller says overwrite=false and they did NOT request an explicit version,
    # then treat this as "fail if exists" (filesystem-like semantics).
    if existed_before and pp.requested_version is None and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="Asset already exists (overwrite=false). Use overwrite=true to create a new version, "
                   "or request an explicit version in the filename (foo.N.ext).",
        )

    # Decide target version
    if pp.requested_version is None:
        new_version = await _max_version(session, asset.id) + 1
        replace_existing_doc: Optional[EthelDocument] = None
    else:
        new_version = pp.requested_version
        existing_doc = await _get_doc_for_version(session, asset.id, new_version)
        if existing_doc and not overwrite:
            raise HTTPException(status_code=409, detail="That version already exists (overwrite=false)")
        replace_existing_doc = existing_doc

    new_doc_id = uuid.uuid4()

    try:
        await s3_manager.upload_file(BytesIO(data_bytes), str(new_doc_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to S3: {e}")

    try:
        if replace_existing_doc:
            old_id = replace_existing_doc.id
            await session.delete(replace_existing_doc)
            await session.flush()
            try:
                await s3_manager.delete_file(str(old_id))
            except Exception as e:
                logger.error(f"Failed to delete old S3 object {old_id} during overwrite: {e}")

        current_latest_ver = 0
        if asset.latest_document_id:
            latest_doc = await _get_doc(session, asset.latest_document_id)
            if latest_doc and latest_doc.version is not None:
                current_latest_ver = latest_doc.version

        doc = EthelDocument(
            id=new_doc_id,
            title=title or pp.filename,  # base filename as default title
            content_type=content_type,
            asset_id=asset.id,
            version=new_version,
        )
        session.add(doc)

        # ensure doc exists before pointing asset.latest_document_id at it
        await session.flush()

        if asset.latest_document_id is None or new_version >= current_latest_ver:
            asset.latest_document_id = new_doc_id

        await session.commit()
        return {
            "path": pp.raw,
            "asset_id": str(asset.id),
            "document_id": str(new_doc_id),
            "version": new_version,
            "content_type": content_type,
            "latest_document_id": str(asset.latest_document_id) if asset.latest_document_id else None,
        }
    except Exception as e:
        await session.rollback()
        try:
            await s3_manager.delete_file(str(new_doc_id))
        except Exception as asset_delete_error:
            logger.error(
                f"CRITICAL: Failed to delete orphaned S3 object {new_doc_id} after DB error: {asset_delete_error}"
            )
        raise HTTPException(status_code=500, detail=f"Failed to persist metadata: {e}")


@router.get("", summary="Download by logical path (latest unless versioned filename)")
async def download_asset(
    path: str = Query(..., description="e.g. /ethz/physics/mechanics/angular.pdf or angular.2.pdf"),
    session: AsyncSession = Depends(get_session),
):
    pp = _normalize_logical_path(path)
    asset = await _get_asset_by_path(session, pp)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    if pp.requested_version is None:
        if not asset.latest_document_id:
            raise HTTPException(status_code=404, detail="Asset has no versions")
        doc = await _get_doc(session, asset.latest_document_id)
    else:
        doc = await _get_doc_for_version(session, asset.id, pp.requested_version)

    if not doc:
        raise HTTPException(status_code=404, detail="Document/version not found")

    buf = BytesIO()
    try:
        await s3_manager.download_file(str(doc.id), buf)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download from S3: {e}")

    # IMPORTANT: for versioned requests, return the requested filename in headers
    out_name = pp.requested_filename if pp.requested_version is not None else pp.filename

    return Response(
        content=buf.getvalue(),
        media_type=getattr(doc, "content_type", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


@router.get("/by-id/{document_id}", summary="Download a document by UUID")
async def download_by_id(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    doc = await _get_doc(session, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    buf = BytesIO()
    try:
        await s3_manager.download_file(str(doc.id), buf)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download from S3: {e}")

    filename = doc.title or str(doc.id)
    return Response(
        content=buf.getvalue(),
        media_type=getattr(doc, "content_type", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ls", summary="List directory contents")
async def ls(
    path: str = Query("/", description="Directory path: /, /tenant, /tenant/collection, /tenant/collection/subdir/..."),
    include_markers: bool = Query(False, description="Include .keep entries"),
    session: AsyncSession = Depends(get_session),
):
    p = re.sub(r"/{2,}", "/", (path or "/").strip())
    if not p.startswith("/"):
        raise HTTPException(status_code=400, detail="path must start with '/'")
    if p != "/" and p.endswith("/"):
        p = p[:-1]

    parts = [seg for seg in p.split("/") if seg]

    if len(parts) == 0:
        res = await session.execute(select(Asset.tenant).distinct().order_by(Asset.tenant))
        return {"path": "/", "directories": [r[0] for r in res.all()], "files": []}

    tenant = parts[0]

    if len(parts) == 1:
        res = await session.execute(
            select(Asset.collection).where(Asset.tenant == tenant).distinct().order_by(Asset.collection)
        )
        return {"path": f"/{tenant}", "directories": [r[0] for r in res.all()], "files": []}

    collection = parts[1]
    subprefix = "/".join(parts[2:]) if len(parts) > 2 else ""

    res = await session.execute(
        select(Asset.subpath, Asset.filename, Asset.latest_document_id)
        .where(Asset.tenant == tenant)
        .where(Asset.collection == collection)
    )
    rows = res.all()

    directories: set[str] = set()
    files: List[Dict[str, Any]] = []

    for sp, fn, latest_doc_id in rows:
        sp = sp or ""
        if not include_markers and fn == DIR_MARKER_FILENAME:
            continue

        if subprefix == "":
            if sp == "":
                files.append({"name": fn, "latest_document_id": str(latest_doc_id) if latest_doc_id else None})
            else:
                directories.add(sp.split("/", 1)[0])
        else:
            if sp == subprefix:
                files.append({"name": fn, "latest_document_id": str(latest_doc_id) if latest_doc_id else None})
            elif sp.startswith(subprefix + "/"):
                remainder = sp[len(subprefix) + 1 :]
                directories.add(remainder.split("/", 1)[0])

    files.sort(key=lambda x: x["name"])
    return {"path": p, "directories": sorted(directories), "files": files}


@router.post("/mkdir", summary="Create tenant/collection/subdirs (directory markers)")
async def mkdir(
    path: str = Query(..., description="Directory path: /tenant/collection[/subdir/...], no filename"),
    session: AsyncSession = Depends(get_session),
):
    p = re.sub(r"/{2,}", "/", path.strip())
    if not p.startswith("/"):
        raise HTTPException(status_code=400, detail="path must start with '/'")
    if p.endswith("/"):
        p = p[:-1]

    parts = [seg for seg in p.split("/") if seg]
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="mkdir requires /tenant/collection[/subdirs...]")
    if any(seg in (".", "..") for seg in parts):
        raise HTTPException(status_code=400, detail="path must not contain '.' or '..' segments")

    tenant = parts[0]
    collection = parts[1]
    subpath = "/".join(parts[2:]) if len(parts) > 2 else ""

    try:
        await _ensure_dir_markers(session, tenant, collection, subpath)
        await session.commit()
        return {"created": True, "path": p}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"mkdir failed: {e}")


@router.post("/mv", summary="Move/rename an asset (metadata only)")
async def mv(
    src: str = Query(..., description="Source asset path"),
    dst: str = Query(..., description="Destination asset path"),
    session: AsyncSession = Depends(get_session),
):
    srcp = _normalize_logical_path(src)
    dstp = _normalize_logical_path(dst)

    asset = await _get_asset_by_path(session, srcp)
    if not asset:
        raise HTTPException(status_code=404, detail="Source not found")

    conflict = await _get_asset_by_path(session, dstp)
    if conflict:
        raise HTTPException(status_code=409, detail="Destination already exists")

    await _ensure_dir_markers(session, dstp.tenant, dstp.collection, dstp.subpath)

    try:
        asset.tenant = dstp.tenant
        asset.collection = dstp.collection
        asset.subpath = dstp.subpath
        asset.filename = dstp.filename  # BASE filename
        await session.commit()
        return {"moved": True, "from": srcp.raw, "to": dstp.raw, "asset_id": str(asset.id)}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"mv failed: {e}")


@router.delete("", summary="Remove an asset or a specific version")
async def rm(
    path: str = Query(..., description="e.g. /.../file.pdf deletes whole asset; /.../file.2.pdf deletes only version 2"),
    session: AsyncSession = Depends(get_session),
):
    pp = _normalize_logical_path(path)
    asset = await _get_asset_by_path(session, pp)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    if pp.requested_version is not None:
        doc = await _get_doc_for_version(session, asset.id, pp.requested_version)
        if not doc:
            raise HTTPException(status_code=404, detail="That version does not exist")

        doc_id = doc.id
        was_latest = asset.latest_document_id == doc_id

        try:
            await session.delete(doc)
            await session.flush()
            if was_latest:
                await _recompute_latest(session, asset)
                if asset.latest_document_id is None:
                    await session.delete(asset)

            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to delete version: {e}")

        try:
            await s3_manager.delete_file(str(doc_id))
        except Exception as e:
            logger.error(f"Failed to delete S3 object {doc_id}: {e}")

        return {"deleted": True, "path": pp.raw, "deleted_version": pp.requested_version}

    res = await session.execute(select(EthelDocument.id).where(EthelDocument.asset_id == asset.id))
    doc_ids = [r[0] for r in res.all()]

    try:
        await session.delete(asset)
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete asset metadata: {e}")

    s3_errors: List[str] = []
    for did in doc_ids:
        try:
            await s3_manager.delete_file(str(did))
        except Exception as e:
            s3_errors.append(f"{did}: {e}")

    return {"deleted": True, "path": pp.raw, "deleted_versions": len(doc_ids), "s3_delete_errors": s3_errors}

