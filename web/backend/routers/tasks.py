"""定时任务管理 REST API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from claw.scheduler.config import validate_cron
from claw.scheduler.types import (
    CronTrigger,
    IntervalTrigger,
)
from web.backend.schemas.task import (
    CreateTaskRequest,
    TaskDetailSchema,
    TaskRunRecordSchema,
    TaskSchema,
    ToggleRequest,
    TriggerResultSchema,
    UpdateTaskRequest,
)
from web.backend.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _service(request: Request) -> TaskService:
    """从 app.state 获取 TaskService 实例。"""
    return request.app.state.task_service


@router.get("", response_model=list[TaskSchema])
async def list_tasks(request: Request) -> list[TaskSchema]:
    """列出所有已注册任务。"""
    svc = _service(request)
    tasks = svc.list_tasks()
    return [TaskSchema(**t) for t in tasks]


@router.get("/{name}", response_model=TaskDetailSchema)
async def get_task(name: str, request: Request) -> TaskDetailSchema:
    """获取任务详情 + 执行历史。"""
    svc = _service(request)
    detail = svc.get_task(name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {name}")
    return TaskDetailSchema(**detail)


@router.post("", response_model=TaskSchema, status_code=201)
async def create_task(
    body: CreateTaskRequest, request: Request
) -> TaskSchema:
    """创建 LLM 调度任务：选择 agent → 自动创建推送 session。"""
    svc = _service(request)

    trigger = _build_trigger(body.trigger)

    try:
        view = await svc.create_task(
            name=body.name,
            trigger=trigger,
            agent_id=body.agent_id,
            prompt=body.prompt,
            description=body.description,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskSchema(**view)


@router.put("/{name}", response_model=TaskSchema)
async def update_task(
    name: str, body: UpdateTaskRequest, request: Request
) -> TaskSchema:
    """更新 LLM 调度任务。"""
    svc = _service(request)

    # 构建 updates dict，只包含非 None 的字段
    updates: dict = {}
    if body.description is not None:
        updates["description"] = body.description
    if body.trigger is not None:
        updates["trigger"] = body.trigger.model_dump()
    if body.prompt is not None:
        updates["prompt"] = body.prompt
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    try:
        view = await svc.update_task(name, updates)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "System task" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return TaskSchema(**view)


@router.patch("/{name}/toggle", response_model=TaskSchema)
async def toggle_task(
    name: str, body: ToggleRequest, request: Request
) -> TaskSchema:
    """切换任务启用/禁用。"""
    svc = _service(request)
    try:
        view = await svc.toggle_task(name, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TaskSchema(**view)


@router.post("/{name}/trigger", response_model=TriggerResultSchema)
async def trigger_task(name: str, request: Request) -> TriggerResultSchema:
    """手动触发任务。"""
    svc = _service(request)
    try:
        result = await svc.trigger_task(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TriggerResultSchema(**result)


@router.get("/{name}/history", response_model=list[TaskRunRecordSchema])
async def get_task_history(
    name: str, request: Request, limit: int = 20
) -> list[TaskRunRecordSchema]:
    """获取任务执行历史。"""
    svc = _service(request)
    records = svc.get_history(name, limit=limit)
    return [TaskRunRecordSchema(**r) for r in records]


@router.delete("/{name}", status_code=204)
async def delete_task(name: str, request: Request) -> None:
    """删除 LLM 调度任务。"""
    svc = _service(request)
    try:
        await svc.delete_task(name)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "System task" in msg:
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


# ---- 内部辅助 ----


def _build_trigger(trigger_schema) -> CronTrigger | IntervalTrigger:
    """从 TriggerSchema 构建触发器实例。"""
    if trigger_schema.type == "cron":
        # 校验 cron 表达式
        try:
            validate_cron(trigger_schema.expression)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return CronTrigger(expression=trigger_schema.expression)
    if trigger_schema.type == "interval":
        return IntervalTrigger(seconds=trigger_schema.seconds)
    raise HTTPException(status_code=422, detail=f"Unsupported trigger type: {trigger_schema.type}")
