import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_user_organization_id
from app.models.entities import ExecutionStatus, User, Workflow, WorkflowExecution, WorkflowVersion
from app.schemas.workflow import ExecuteRequest, WorkflowCreate, WorkflowGraph, WorkflowUpdate
from app.workflow_engine.validator import validate_graph


router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)


def _workflow_response(workflow: Workflow) -> dict:
    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "graph": workflow.graph,
        "version": workflow.current_version,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


def _execution_response(execution: WorkflowExecution) -> dict:
    return {
        "id": str(execution.id),
        "workflow_id": str(execution.workflow_id),
        "workflow_version": execution.workflow_version,
        "status": execution.status.value,
        "idempotency_key": execution.idempotency_key,
        "input": execution.input,
        "state": execution.state,
        "error": execution.error,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
    }


async def _get_workflow(workflow_id: UUID, organization_id: UUID, db: AsyncSession) -> Workflow:
    workflow = await db.scalar(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.organization_id == organization_id,
        )
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    graph = payload.graph.model_dump(mode="json") if payload.graph else {"nodes": [], "edges": []}
    workflow = Workflow(
        organization_id=organization_id,
        name=payload.name,
        description=payload.description,
        graph=graph,
        current_version=1,
    )
    db.add(workflow)
    await db.flush()
    db.add(WorkflowVersion(workflow_id=workflow.id, version=1, graph=graph))
    await db.commit()
    await db.refresh(workflow)
    return _workflow_response(workflow)


@router.get("")
async def list_workflows(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    organization_id = await get_user_organization_id(user, db)
    result = await db.scalars(
        select(Workflow)
        .where(Workflow.organization_id == organization_id)
        .order_by(Workflow.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_workflow_response(workflow) for workflow in result.all()]


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    return _workflow_response(await _get_workflow(workflow_id, organization_id, db))


@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    payload: WorkflowUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    workflow = await _get_workflow(workflow_id, organization_id, db)
    if payload.name is not None:
        workflow.name = payload.name
    if payload.description is not None:
        workflow.description = payload.description
    if payload.graph is not None:
        new_graph = payload.graph.model_dump(mode="json")
        if new_graph != workflow.graph:
            workflow.graph = new_graph
            workflow.current_version += 1
            db.add(
                WorkflowVersion(
                    workflow_id=workflow.id,
                    version=workflow.current_version,
                    graph=workflow.graph,
                )
            )
    await db.commit()
    await db.refresh(workflow)
    return _workflow_response(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    organization_id = await get_user_organization_id(user, db)
    workflow = await _get_workflow(workflow_id, organization_id, db)
    await db.delete(workflow)
    await db.commit()


@router.post("/{workflow_id}/validate")
async def validate_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    workflow = await _get_workflow(workflow_id, organization_id, db)
    errors = validate_graph(WorkflowGraph.model_validate(workflow.graph))
    return {"valid": not errors, "errors": errors}


@router.post("/{workflow_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_workflow(
    workflow_id: UUID,
    payload: ExecuteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    workflow = await _get_workflow(workflow_id, organization_id, db)
    graph = WorkflowGraph.model_validate(workflow.graph)
    errors = validate_graph(graph)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"errors": errors})

    idempotency_key = None
    if payload.idempotency_key:
        scoped_key = f"{organization_id}:{workflow_id}:{payload.idempotency_key}"
        idempotency_key = hashlib.sha256(scoped_key.encode()).hexdigest()
        existing = await db.scalar(
            select(WorkflowExecution).where(WorkflowExecution.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return _execution_response(existing)

    execution = WorkflowExecution(
        workflow_id=workflow.id,
        workflow_version=workflow.current_version,
        status=ExecutionStatus.QUEUED,
        idempotency_key=idempotency_key,
        input=payload.input,
        state={"nodes": {}},
    )
    db.add(execution)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key is None:
            raise
        existing = await db.scalar(
            select(WorkflowExecution).where(WorkflowExecution.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        return _execution_response(existing)
    await db.refresh(execution)

    try:
        from app.workers.tasks.workflow_tasks import execute_workflow_task
        execute_workflow_task.delay(str(execution.id), graph.model_dump(mode="json"), payload.input)
    except Exception as exc:
        logger.exception("Unable to enqueue workflow execution %s", execution.id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Workflow queue is unavailable") from exc
    return _execution_response(execution)
