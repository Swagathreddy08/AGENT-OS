from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_user_organization_id
from app.models.entities import ApprovalRequest, User, Workflow, WorkflowExecution

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    decision: dict = Field(default_factory=dict)


async def _get_approval(approval_id: UUID, organization_id: UUID, db: AsyncSession) -> ApprovalRequest:
    approval = await db.scalar(
        select(ApprovalRequest)
        .join(WorkflowExecution, WorkflowExecution.id == ApprovalRequest.execution_id)
        .join(Workflow, Workflow.id == WorkflowExecution.workflow_id)
        .where(
            ApprovalRequest.id == approval_id,
            Workflow.organization_id == organization_id,
        )
        .with_for_update()
    )
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    return approval


@router.get("")
async def list_approvals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    organization_id = await get_user_organization_id(user, db)
    result = await db.execute(
        select(ApprovalRequest, WorkflowExecution.workflow_id)
        .join(WorkflowExecution, WorkflowExecution.id == ApprovalRequest.execution_id)
        .join(Workflow, Workflow.id == WorkflowExecution.workflow_id)
        .where(Workflow.organization_id == organization_id)
        .order_by(ApprovalRequest.created_at.desc())
    )
    return [
        {
            "id": str(approval.id),
            "execution_id": str(approval.execution_id),
            "workflow_id": str(workflow_id),
            "node_id": approval.node_id,
            "status": approval.status,
            "payload": approval.payload,
            "decision": approval.decision,
        }
        for approval, workflow_id in result.all()
    ]


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: UUID,
    payload: ApprovalDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    approval = await _get_approval(approval_id, organization_id, db)
    if approval.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval has already been decided")
    approval.status = "approved"
    approval.decision = payload.decision
    await db.commit()
    return {"id": str(approval.id), "status": approval.status, "decision": approval.decision}


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: UUID,
    payload: ApprovalDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    organization_id = await get_user_organization_id(user, db)
    approval = await _get_approval(approval_id, organization_id, db)
    if approval.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Approval has already been decided")
    approval.status = "rejected"
    approval.decision = payload.decision
    await db.commit()
    return {"id": str(approval.id), "status": approval.status, "decision": approval.decision}
