"""Celery application configuration."""

from celery import Celery

from sre_agent.config import get_settings

settings = get_settings()

# Create Celery application
celery_app = Celery(
    "sre_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "sre_agent.tasks.dispatch",
        "sre_agent.tasks.context_tasks",
        "sre_agent.tasks.fix_pipeline_tasks",
        "sre_agent.tasks.notification_tasks",
    ],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Reliability settings
    task_acks_late=True,  # Acknowledge after task completes
    task_reject_on_worker_lost=True,  # Re-queue if worker dies
    worker_prefetch_multiplier=1,  # One task at a time per worker
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    # Retry settings
    task_default_retry_delay=60,  # 1 minute default retry delay
    task_max_retries=3,
    # Dead letter queue for failed tasks
    task_routes={
        "sre_agent.tasks.dispatch.*": {"queue": "default"},
    },
    # Task time limits
    task_soft_time_limit=300,  # 5 minutes soft limit
    task_time_limit=600,  # 10 minutes hard limit
)

# Optional: Configure beat schedule for periodic tasks (future use)
celery_app.conf.beat_schedule = {
    # Example: Cleanup old events every hour
    # "cleanup-old-events": {
    #     "task": "sre_agent.tasks.maintenance.cleanup_old_events",
    #     "schedule": 3600.0,
    # },
}
