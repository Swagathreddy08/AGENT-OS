import asyncio
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.workers.celery_app import celery_app


async def _execute(execution_id: str, graph_data: dict, input_data: dict, retry_attempt: int = 0) -> dict:
    from app.core.database import SessionLocal, engine
    from app.models.entities import ExecutionStatus, WorkflowExecution
    from app.schemas.workflow import WorkflowGraph
    from app.workflow_engine.executor import WorkflowExecutor
    from app.workflow_engine.validator import validate_graph

    try:
        async with SessionLocal() as db:
            parsed_execution_id = UUID(execution_id)
            execution = await db.get(WorkflowExecution, parsed_execution_id)
            if execution is None:
                raise ValueError(f"Execution {execution_id} was not found.")
            if execution.status == ExecutionStatus.CANCELLED:
                return {"execution_id": execution_id, "status": "cancelled"}

            previous_status = ExecutionStatus.FAILED if retry_attempt else ExecutionStatus.QUEUED
            result = await db.execute(
                update(WorkflowExecution)
                .where(
                    WorkflowExecution.id == parsed_execution_id,
                    WorkflowExecution.status == previous_status,
                )
                .values(status=ExecutionStatus.RUNNING, started_at=datetime.utcnow(), error=None)
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
                state = await WorkflowExecutor(graph).execute({"input": input_data, "nodes": {}})
            except Exception as exc:
                execution.status = ExecutionStatus.FAILED
                execution.error = str(exc)
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise

            execution.state = state
            execution.status = ExecutionStatus.COMPLETED
            execution.completed_at = datetime.utcnow()
            await db.commit()
            return {"execution_id": execution_id, "status": "completed", "state": state}
    finally:
        await engine.dispose()


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def execute_workflow_task(self, execution_id: str, graph: dict, input_data: dict) -> dict:
    return asyncio.run(_execute(execution_id, graph, input_data, self.request.retries))
