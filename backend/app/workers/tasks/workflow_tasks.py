import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from app.workers.celery_app import celery_app
from app.workflow_engine.errors import ApprovalRequired, NonRetryableWorkflowError
from app.workflow_engine.node_handlers import create_node_handlers


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.utcnow()


async def _execute(
    execution_id: str,
    retry_attempt: int = 0,
) -> dict:
    from app.core.database import SessionLocal
    from app.models.entities import (
        ApprovalRequest,
        ExecutionStatus,
        NodeExecution,
        Workflow,
        WorkflowExecution,
        WorkflowVersion,
    )
    from app.schemas.workflow import WorkflowGraph
    from app.workflow_engine.executor import WorkflowExecutor
    from app.workflow_engine.validator import validate_graph

    async with SessionLocal() as db:
        parsed_execution_id = UUID(execution_id)
        execution = await db.get(WorkflowExecution, parsed_execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} was not found.")
        if execution.status == ExecutionStatus.CANCELLED:
            return {"execution_id": execution_id, "status": "cancelled"}

        previous_status = (
            ExecutionStatus.FAILED
            if retry_attempt
            else ExecutionStatus.QUEUED
        )
        result = await db.execute(
            update(WorkflowExecution)
            .where(
                WorkflowExecution.id == parsed_execution_id,
                WorkflowExecution.status == previous_status,
            )
            .values(status=ExecutionStatus.RUNNING, started_at=utcnow(), error=None)
        )
        if result.rowcount != 1:
            await db.rollback()
            current = await db.scalar(
                select(WorkflowExecution).where(WorkflowExecution.id == parsed_execution_id)
            )
            if current is None:
                raise ValueError(f"Execution {execution_id} was not found.")
            return {"execution_id": execution_id, "status": current.status.value}
        await db.commit()
        execution = await db.get(WorkflowExecution, parsed_execution_id)
        workflow = await db.get(Workflow, execution.workflow_id)
        try:
            version = await db.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == execution.workflow_id,
                    WorkflowVersion.version == execution.workflow_version,
                )
            )
            if version is None:
                raise NonRetryableWorkflowError(
                    f"Workflow version {execution.workflow_version} was not found."
                )
            graph = WorkflowGraph.model_validate(version.graph)
            errors = validate_graph(graph)
            if errors:
                raise NonRetryableWorkflowError("; ".join(errors))

            state = dict(execution.state or {})
            state["input"] = execution.input or {}
            state["nodes"] = dict(state.get("nodes", {}))
            state["approvals"] = dict(state.get("approvals", {}))
            incoming_nodes: dict[str, list[str]] = {}
            for edge in graph.edges:
                incoming_nodes.setdefault(edge.target, []).append(edge.source)

            async def should_continue() -> bool:
                await db.refresh(execution)
                return execution.status != ExecutionStatus.CANCELLED

            async def persist_node(node_id: str, output: dict, node_state: dict) -> None:
                now = utcnow()
                db.add(
                    NodeExecution(
                        execution_id=execution.id,
                        node_id=node_id,
                        status="completed",
                        input={"input": node_state.get("input", {})},
                        output=output,
                        attempt=retry_attempt + 1,
                        started_at=now,
                        completed_at=now,
                    )
                )
                execution.state = {
                    **node_state,
                    "nodes": dict(node_state.get("nodes", {})),
                }
                await db.commit()

            async def persist_node_error(
                node_id: str,
                error: Exception,
                node_state: dict,
            ) -> None:
                now = utcnow()
                db.add(
                    NodeExecution(
                        execution_id=execution.id,
                        node_id=node_id,
                        status="failed",
                        input={"input": node_state.get("input", {})},
                        error=str(error)[:2000],
                        attempt=retry_attempt + 1,
                        started_at=now,
                        completed_at=now,
                    )
                )
                execution.state = {
                    **node_state,
                    "nodes": dict(node_state.get("nodes", {})),
                }
                await db.commit()

            handlers = create_node_handlers(
                db,
                execution.id,
                workflow.organization_id,
                incoming_nodes,
            )

            state = await WorkflowExecutor(
                graph,
                handlers=handlers,
                should_continue=should_continue,
                on_node_complete=persist_node,
                on_node_error=persist_node_error,
            ).execute(state)
        except ApprovalRequired as request:
            node_id = request.node_id
            approval = await db.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.execution_id == execution.id,
                    ApprovalRequest.node_id == node_id,
                )
            )
            if approval is None:
                db.add(
                    ApprovalRequest(
                        execution_id=execution.id,
                        node_id=node_id,
                        status="pending",
                        payload=request.payload,
                    )
                )
            execution.state = {
                **state,
                "nodes": dict(state.get("nodes", {})),
            }
            execution.status = ExecutionStatus.WAITING_APPROVAL
            execution.error = None
            execution.started_at = None
            await db.commit()
            return {"execution_id": execution_id, "status": "waiting_approval"}
        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error = str(exc)[:2000]
            execution.completed_at = utcnow()
            await db.commit()
            raise

        execution.state = state
        if state.pop("cancelled", False):
            execution.status = ExecutionStatus.CANCELLED
        else:
            execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = utcnow()
        await db.commit()
        return {"execution_id": execution_id, "status": "completed", "state": state}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(NonRetryableWorkflowError,),
    retry_backoff=True,
    max_retries=3,
)
def execute_workflow_task(self, execution_id: str) -> dict:
    return asyncio.run(_execute(execution_id, self.request.retries))


async def _requeue_stuck_executions() -> int:
    from app.core.database import SessionLocal
    from app.models.entities import ExecutionStatus, WorkflowExecution

    queued_cutoff = utcnow() - timedelta(seconds=60)
    running_cutoff = utcnow() - timedelta(seconds=960)
    async with SessionLocal() as db:
        queued = list(
            (
                await db.scalars(
                    select(WorkflowExecution)
                    .where(
                        WorkflowExecution.status == ExecutionStatus.QUEUED,
                        WorkflowExecution.created_at <= queued_cutoff,
                    )
                    .order_by(WorkflowExecution.created_at)
                    .limit(100)
                )
            ).all()
        )
        stale_running = list(
            (
                await db.scalars(
                    select(WorkflowExecution)
                    .where(
                        WorkflowExecution.status == ExecutionStatus.RUNNING,
                        WorkflowExecution.started_at <= running_cutoff,
                    )
                    .order_by(WorkflowExecution.started_at)
                    .limit(100)
                )
            ).all()
        )
        for execution in stale_running:
            result = await db.execute(
                update(WorkflowExecution)
                .where(
                    WorkflowExecution.id == execution.id,
                    WorkflowExecution.status == ExecutionStatus.RUNNING,
                    WorkflowExecution.started_at <= running_cutoff,
                )
                .values(status=ExecutionStatus.QUEUED, started_at=None)
            )
            if result.rowcount == 1:
                queued.append(execution)
        await db.commit()

    for execution in queued:
        try:
            execute_workflow_task.delay(str(execution.id))
        except Exception:
            logger.exception(
                "Unable to requeue workflow execution %s",
                execution.id,
            )
            raise
    return len(queued)


@celery_app.task(name="app.workers.tasks.workflow_tasks.requeue_stuck_executions")
def requeue_stuck_executions() -> int:
    return asyncio.run(_requeue_stuck_executions())


async def _run_scheduled_workflows() -> int:
    import hashlib

    from sqlalchemy.exc import IntegrityError

    from app.core.database import SessionLocal
    from app.models.entities import ExecutionStatus, Workflow, WorkflowExecution

    now = utcnow()
    async with SessionLocal() as db:
        workflows = (
            await db.scalars(
                select(Workflow)
                .where(Workflow.graph["nodes"].contains([{"type": "scheduled_trigger"}]))
                .order_by(Workflow.created_at)
                .limit(1000)
            )
        ).all()
        scheduled = []
        for workflow in workflows:
            trigger = next(
                (
                    node for node in workflow.graph.get("nodes", [])
                    if node.get("type") == "scheduled_trigger"
                ),
                None,
            )
            config = trigger.get("data", {}).get("config", {}) if trigger else {}
            interval = config.get("interval_seconds")
            if not isinstance(interval, int) or not 60 <= interval <= 86400:
                logger.warning(
                    "Skipping scheduled workflow %s: interval_seconds must be between 60 and 86400",
                    workflow.id,
                )
                continue
            slot = int(now.timestamp()) // interval
            raw_key = f"{workflow.organization_id}:{workflow.id}:schedule:{slot}"
            idempotency_key = hashlib.sha256(raw_key.encode()).hexdigest()
            execution = WorkflowExecution(
                workflow_id=workflow.id,
                workflow_version=workflow.current_version,
                status=ExecutionStatus.QUEUED,
                idempotency_key=idempotency_key,
                input={"scheduled_at": now.isoformat()},
                state={"nodes": {}},
            )
            db.add(execution)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                continue
            await db.refresh(execution)
            scheduled.append(execution.id)

    for execution_id in scheduled:
        try:
            execute_workflow_task.delay(str(execution_id))
        except Exception:
            logger.exception(
                "Unable to enqueue scheduled workflow execution %s",
                execution_id,
            )
    return len(scheduled)


@celery_app.task(name="app.workers.tasks.workflow_tasks.run_scheduled_workflows")
def run_scheduled_workflows() -> int:
    return asyncio.run(_run_scheduled_workflows())
