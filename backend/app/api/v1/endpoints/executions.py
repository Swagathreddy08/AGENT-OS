from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_user_organization_id
from app.models.entities import ExecutionStatus, NodeExecution, User, Workflow, WorkflowExecution
from app.api.v1.endpoints.workflows import _execution_response


router = APIRouter(prefix="/executions", tags=["executions"])


async def _get_execution(
    execution_id: UUID,
    organization_id: UUID,
    db: AsyncSession,
) -> WorkflowExecution:
    execution = await db.scalar(
        select(WorkflowExecution)
        .join(Workflow, Workflow.id == WorkflowExecution.workflow_id)
        .where(
            WorkflowExecution.id == execution_id,
            Workflow.organization_id == organization_id,
        )
    )
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    return execution


@router.get("")
async def list_executions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    organization_id = await get_user_organization_id(user, db)
    result = await db.scalars(
        select(WorkflowExecution)
        .join(Workflow, Workflow.id == WorkflowExecution.workflow_id)
        .where(Workflow.organization_id == organization_id)
        .order_by(WorkflowExecution.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_execution_response(execution) for execution in result.all()]


@router.get("/{execution_id}")
async def get_execution(
    execution_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    return _execution_response(await _get_execution(execution_id, organization_id, db))


@router.get("/{execution_id}/logs")
async def execution_logs(
    execution_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    organization_id = await get_user_organization_id(user, db)
    execution = await _get_execution(execution_id, organization_id, db)
    result = await db.scalars(
        select(NodeExecution)
        .where(NodeExecution.execution_id == execution.id)
        .order_by(NodeExecution.started_at)
    )
    rows = result.all()
    if not rows:
        return [
            {"node_id": node_id, "status": "completed", "output": output}
            for node_id, output in execution.state.get("nodes", {}).items()
        ]
    return [
        {
            "node_id": item.node_id,
            "status": item.status,
            "input": item.input,
            "output": item.output,
            "error": item.error,
            "attempt": item.attempt,
            "started_at": item.started_at.isoformat() if item.started_at else None,
            "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        }
        for item in rows
    ]


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    execution = await _get_execution(execution_id, organization_id, db)
    result = await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution.id,
            WorkflowExecution.status.in_({
                ExecutionStatus.QUEUED,
                ExecutionStatus.RUNNING,
                ExecutionStatus.WAITING_APPROVAL,
            }),
        )
        .values(status=ExecutionStatus.CANCELLED)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This execution can no longer be cancelled.",
        )
    await db.commit()
    await db.refresh(execution)
    return _execution_response(execution)
