from celery import Celery  # type: ignore[import-untyped]
from kombu import Queue, Exchange  # type: ignore[import-untyped]

from app.config import get_settings
from celery_redis_cluster_backend import install_redis_cluster_backend  # type: ignore[import-not-found]


settings = get_settings()

broker_url = settings.redis_url
if "rediss://" in broker_url and "+cluster" not in broker_url:
    broker_url = broker_url.replace("rediss://", "rediss+cluster://")
elif "redis://" in broker_url and "+cluster" not in broker_url:
    broker_url = broker_url.replace("redis://", "redis+cluster://")

# Install the custom redis cluster backend to handle clustering redirection
install_redis_cluster_backend()

celery = Celery(
    "hostfast",
    broker=broker_url,
    backend=broker_url,
)

if broker_url.startswith("rediss"):
    celery.conf.update(
        broker_use_ssl={"ssl_cert_reqs": "none"},
        redis_backend_use_ssl={"ssl_cert_reqs": "none"},
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
