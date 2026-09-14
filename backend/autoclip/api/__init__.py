"""REST API routers."""

from __future__ import annotations

from fastapi import APIRouter

from . import bgm, campaigns, clips, internal_acquire, jobs, publishing, settings, sources

__all__ = ["api_router"]

api_router = APIRouter()
api_router.include_router(sources.router)
api_router.include_router(jobs.router)
api_router.include_router(clips.router)
api_router.include_router(campaigns.router)
api_router.include_router(publishing.router)
api_router.include_router(settings.router)
api_router.include_router(internal_acquire.router)
api_router.include_router(bgm.router)


