import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.utcnow()


async def _execute(
    execution_id: str,
    graph_data: dict,
    input_data: dict,
    retry_attempt: int = 0,
) -> dict:
    from app.core.database import SessionLocal
    from app.models.entities import ExecutionStatus, WorkflowExecution
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
        try:
            graph = WorkflowGraph.model_validate(graph_data)
            errors = validate_graph(graph)
            if errors:
                raise ValueError("; ".join(errors))

            async def should_continue() -> bool:
                await db.refresh(execution)
                return execution.status != ExecutionStatus.CANCELLED

            async def classifier_handler(state: dict, config: dict) -> dict:
                source_text = str(state.get("input", {}).get("text", "")).lower()
                category = "bug" if any(word in source_text for word in ("bug", "error", "crash")) else "general"
                return {"category": category, "condition": category == "bug"}

            handlers = {
                "text_classifier": classifier_handler,
            }

            state = await WorkflowExecutor(
                graph,
                handlers=handlers,
                should_continue=should_continue,
            ).execute({"input": input_data, "nodes": {}})
        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error = str(exc)
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


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def execute_workflow_task(self, execution_id: str, graph: dict, input_data: dict) -> dict:
    return asyncio.run(_execute(execution_id, graph, input_data, self.request.retries))


async def _requeue_stuck_executions() -> int:
    from app.core.database import SessionLocal
    from app.models.entities import ExecutionStatus, WorkflowExecution, WorkflowVersion

    cutoff = utcnow() - timedelta(seconds=60)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(WorkflowExecution, WorkflowVersion)
                .join(
                    WorkflowVersion,
                    (WorkflowVersion.workflow_id == WorkflowExecution.workflow_id)
                    & (WorkflowVersion.version == WorkflowExecution.workflow_version),
                )
                .where(
                    WorkflowExecution.status == ExecutionStatus.QUEUED,
                    WorkflowExecution.created_at <= cutoff,
                )
                .order_by(WorkflowExecution.created_at)
                .limit(100)
            )
        ).all()

    for execution, version in rows:
        try:
            execute_workflow_task.delay(
                str(execution.id),
                version.graph,
                execution.input,
            )
        except Exception:
            logger.exception(
                "Unable to requeue workflow execution %s",
                execution.id,
            )
            raise
    return len(rows)


@celery_app.task(name="app.workers.tasks.workflow_tasks.requeue_stuck_executions")
def requeue_stuck_executions() -> int:
    return asyncio.run(_requeue_stuck_executions())
