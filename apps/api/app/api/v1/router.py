"""Aggregated API router for /api/v1."""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.health import router as health_router
from app.api.v1.me import router as me_router
from app.modules.admission import usage_router
from app.modules.community.router import community_router
from app.modules.credentials.router import router as credentials_router
from app.modules.llm_gateway.router import admin_llm_router, llm_router

api_v1_router = APIRouter()

api_v1_router.include_router(me_router)
api_v1_router.include_router(credentials_router)
api_v1_router.include_router(usage_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(health_router)
api_v1_router.include_router(llm_router)
api_v1_router.include_router(admin_llm_router)
api_v1_router.include_router(community_router)
