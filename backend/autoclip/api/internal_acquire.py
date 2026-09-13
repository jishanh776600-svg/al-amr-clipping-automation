"""Internal authenticated source acquisition API for AL AMR cloud workers.

Exposes server-side media acquisition endpoints guarded by internal server tokens.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import paths
from ..db.models import new_id
from ..pipeline.source_acquisition.base import (
    JobContext,
    SourceAcquisitionError,
    SourceErrorCode,
)
from ..pipeline.source_acquisition.security import safe_target_path, validate_remote_url
from ..pipeline.source_acquisition.server_downloader.engine import ServerDownloaderEngine
from .auth import is_valid_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["internal-acquisition"])


class InternalAcquireIn(BaseModel):
    url: str
    job_id: str = Field(default_factory=new_id)


def require_internal_token(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> None:
    """Validate internal server secret token."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not is_valid_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized. A valid internal server secret is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/internal/acquire")
@router.post("/api/internal/acquire")
async def internal_acquire(
    payload: InternalAcquireIn,
    request: Request,
    _: None = Depends(require_internal_token),
) -> JSONResponse:
    """Execute server-side media acquisition for cloud workers."""
    source_url = payload.url.strip()
    job_id = payload.job_id.strip() or new_id()

    # SSRF guard
    try:
        validate_remote_url(source_url)
    except SourceAcquisitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation: {exc.message}",
        ) from exc

    # Target directory isolation
    acquisitions_base = paths.work_dir() / "acquisitions"
    acquisitions_base.mkdir(parents=True, exist_ok=True)
    target_dir = safe_target_path(acquisitions_base, job_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    engine = ServerDownloaderEngine()
    try:
        result, telemetry = engine.download(
            source_url,
            target_dir,
            job_context=JobContext(job_id=job_id),
        )
    except SourceAcquisitionError as exc:
        log.warning("Internal acquisition failed for job %s: [%s] %s", job_id, exc.code, exc.message)
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        if exc.code == SourceErrorCode.SOURCE_PROVIDER_TIMEOUT:
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        elif exc.code == SourceErrorCode.SOURCE_AUTH_REQUIRED:
            status_code = status.HTTP_403_FORBIDDEN
        elif exc.code == SourceErrorCode.SOURCE_ACCESS_BLOCKED:
            status_code = status.HTTP_403_FORBIDDEN

        return JSONResponse(
            status_code=status_code,
            content={
                "error": exc.message,
                "code": exc.code,
                "hint": exc.hint,
                "provider": "server-downloader",
            },
        )
    except Exception as exc:
        log.exception("Unexpected error in internal acquisition for job %s: %s", job_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": f"Internal downloader failure: {exc}",
                "code": SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
                "provider": "server-downloader",
            },
        )

    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/internal/acquire/{job_id}/stream"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "job_id": job_id,
            "provider": "server-downloader",
            "source_url": source_url,
            "file_size": result.file_size,
            "duration": result.duration,
            "sha256": result.sha256,
            "stream_url": stream_url,
            "media_url": stream_url,
            "created_at": time.time(),
        },
    )


@router.get("/internal/acquire/{job_id}/stream")
@router.get("/api/internal/acquire/{job_id}/stream")
async def internal_acquire_stream(
    job_id: str,
    _: None = Depends(require_internal_token),
) -> FileResponse:
    """Stream acquired media file to internal caller."""
    acquisitions_base = paths.work_dir() / "acquisitions"
    target_dir = safe_target_path(acquisitions_base, job_id)
    media_file = target_dir / "source.mp4"

    if not media_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Acquired media for job '{job_id}' not found.",
        )

    return FileResponse(
        path=media_file,
        media_type="video/mp4",
        filename="source.mp4",
    )
