"""技能 REST API：列表、详情、安装（文件/ZIP）、卸载、导出。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from claw.skills.marketplace import MarketplaceOps
from claw.skills.registry import SkillsRegistry
from claw.skills.types import SkillLoadError
from web.backend.deps import get_marketplace_ops, get_skill_registry
from web.backend.schemas.skill import (
    ExportRequestSchema,
    SkillListItemSchema,
    SkillSchema,
)

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillListItemSchema])
async def list_skills(
    q: str = Query("", description="搜索关键词"),
    registry: SkillsRegistry = Depends(get_skill_registry),
) -> list[SkillListItemSchema]:
    """列出所有技能，支持按名称/描述/标签搜索。"""
    if q:
        skills = registry.search(q)
    else:
        skills = registry.list()
    return [SkillListItemSchema.from_skill(s) for s in skills]


@router.get("/{name}", response_model=SkillSchema)
async def get_skill(
    name: str,
    registry: SkillsRegistry = Depends(get_skill_registry),
) -> SkillSchema:
    """获取技能完整详情（含 instructions）。"""
    skill = registry.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return SkillSchema.from_skill(skill)


@router.post("/install/file", response_model=SkillSchema)
async def install_from_file(
    file: UploadFile = File(..., description="SKILL.md 文件"),
    marketplace: MarketplaceOps = Depends(get_marketplace_ops),
) -> SkillSchema:
    """从上传的 SKILL.md 文件安装技能。"""
    # 写入临时目录下的 SKILL.md（loader 要求文件名必须是 SKILL.md）
    tmp_dir = tempfile.mkdtemp(prefix="skill_upload_")
    tmp_path = Path(tmp_dir) / "SKILL.md"
    try:
        content = await file.read()
        tmp_path.write_bytes(content)
        skill = marketplace.install_from_file(str(tmp_path))
    except (FileNotFoundError, ValueError, SkillLoadError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return SkillSchema.from_skill(skill)


@router.post("/install/zip", response_model=list[SkillSchema])
async def install_from_zip(
    file: UploadFile = File(..., description="包含 SKILL.md 的 ZIP 压缩包"),
    marketplace: MarketplaceOps = Depends(get_marketplace_ops),
) -> list[SkillSchema]:
    """从上传的 ZIP 压缩包批量安装技能。"""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".zip", prefix="skill_upload_", delete=False,
    )
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        skills = marketplace.install_from_zip(tmp.name)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if not skills:
        raise HTTPException(status_code=400, detail="ZIP 中没有有效的 SKILL.md 文件")
    return [SkillSchema.from_skill(s) for s in skills]


@router.delete("/{name}", status_code=204)
async def uninstall_skill(
    name: str,
    marketplace: MarketplaceOps = Depends(get_marketplace_ops),
) -> None:
    """卸载技能（仅限 local 来源，bundled 受保护）。"""
    skill = marketplace._registry.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    if skill.source == "bundled":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot uninstall bundled skill: {name}",
        )
    ok = marketplace.remove(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")


@router.get("/{name}/export")
async def export_skill(
    name: str,
    marketplace: MarketplaceOps = Depends(get_marketplace_ops),
) -> Response:
    """导出单个技能为 SKILL.md 文件下载。"""
    skill = marketplace._registry.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

    from claw.skills.store import SkillStore
    content = SkillStore._skill_to_skill_md_static(skill)
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}-SKILL.md"'},
    )


@router.post("/export")
async def export_skills(
    body: ExportRequestSchema,
    background_tasks: BackgroundTasks,
    marketplace: MarketplaceOps = Depends(get_marketplace_ops),
) -> FileResponse:
    """批量导出多个技能为 ZIP 压缩包。"""
    tmp_dir = tempfile.mkdtemp(prefix="skill_export_")
    try:
        zip_path = marketplace.export_skills(body.names, tmp_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 响应发送后清理临时目录
    background_tasks.add_task(_cleanup_dir, tmp_dir)
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename="skills_export.zip",
    )


def _cleanup_dir(dir_path: str) -> None:
    """BackgroundTasks 回调：清理临时目录。"""
    import shutil
    shutil.rmtree(dir_path, ignore_errors=True)
