from celery import Celery  # type: ignore[import-untyped]
from kombu import Queue, Exchange  # type: ignore[import-untyped]

from app.config import get_settings

settings = get_settings()
celery = Celery(
    "hostfast",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery.conf.task_queues = (
    Queue("scale", Exchange("scale"), routing_key="scale", queue_arguments={'x-max-priority': 10}),
    Queue("pro", Exchange("pro"), routing_key="pro", queue_arguments={'x-max-priority': 5}),
    Queue("starter", Exchange("starter"), routing_key="starter", queue_arguments={'x-max-priority': 1}),
)
celery.conf.task_default_queue = "starter"
celery.conf.task_default_exchange = "starter"
celery.conf.task_default_routing_key = "starter"

celery.conf.beat_schedule = {
    "poll-all-sites": {
        "task": "app.tasks.polling_tasks.poll_all_sites",
        "schedule": 60.0,
    }
}
celery.conf.timezone = "UTC"

# Import task modules so decorator-registered tasks are discovered by the worker.
from app.tasks import crawl_tasks, polling_tasks  # noqa: F401
