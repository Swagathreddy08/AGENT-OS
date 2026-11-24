from datetime import timedelta

from celery import Celery

from app.core.config import settings


celery_app = Celery("agentos", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=840,
    timezone="UTC",
    beat_schedule={
        "requeue-stuck-executions": {
            "task": "app.workers.tasks.workflow_tasks.requeue_stuck_executions",
            "schedule": timedelta(seconds=60),
        },
        "run-scheduled-workflows": {
            "task": "app.workers.tasks.workflow_tasks.run_scheduled_workflows",
            "schedule": timedelta(seconds=60),
        },
    },
)
celery_app.conf.imports = ("app.workers.tasks.workflow_tasks",)
