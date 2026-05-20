"""专家 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from claw.expert.service import ExpertService
from web.backend.deps import get_expert_service, get_expert_marketplace
from web.backend.schemas.expert import ExpertSchema

router = APIRouter(prefix="/experts", tags=["experts"])


@router.get("", response_model=list[ExpertSchema])
async def list_experts(
    q: str = Query("", description="搜索关键词"),
    tag: str = Query("", description="标签过滤"),
    service: ExpertService = Depends(get_expert_service),
) -> list[ExpertSchema]:
    experts = service.list_experts(q=q, tag=tag)
    return [ExpertSchema.from_expert(e) for e in experts]


@router.get("/{name}", response_model=ExpertSchema)
async def get_expert(
    name: str,
    service: ExpertService = Depends(get_expert_service),
) -> ExpertSchema:
    expert = service.get(name)
    if expert is None:
        raise HTTPException(status_code=404, detail=f"Expert not found: {name}")
    return ExpertSchema.from_expert(expert)


@router.post("/{name}/install", response_model=ExpertSchema)
async def install_bundled_expert(
    name: str,
    service: ExpertService = Depends(get_expert_service),
) -> ExpertSchema:
    try:
        expert = service.install_bundled(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ExpertSchema.from_expert(expert)


@router.delete("/{name}", status_code=204)
async def uninstall_expert(
    name: str,
    service: ExpertService = Depends(get_expert_service),
) -> None:
    try:
        service.uninstall(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
