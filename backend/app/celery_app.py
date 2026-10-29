"""Celery (target diagram: `worker` = Celery workers, `scheduler` = Celery beat, Redis as broker and result store).

The PostgreSQL `jobs` table stays the durable record and the outbox (ADR-0006/0013): beat runs the relay every
second, which turns due QUEUED jobs into `jobs.run` tasks on the queue of their resource; a task claims its job
with the same lease and fencing as before, so a lost or duplicated Celery message never loses or doubles work.
Queues: llm, llm_eval, cpu, runner, none (job resources), sandbox ([실행] from the API), relay, periodic.
"""
from celery import Celery

from app.core.config import get_settings

PERIODIC_SECONDS = {"job_reaper": 15, "attempt_sweeper": 30, "coding_autosubmit": 15, "posting_autoclose": 60,
                    "retention_purge": 3600, "file_gc": 3600, "session_gc": 3600, "sandbox_verify": 3600}

_url = get_settings().redis_url or "redis://127.0.0.1:6379/0"
celery = Celery("aihire", broker=_url, backend=_url, include=["app.jobs.celery_tasks"])
celery.conf.update(
    task_acks_late=True,               # a worker that dies mid-task leaves the message for another worker
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,      # one job at a time per process: the lease, not the broker, owns the job
    task_default_queue="none",
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
    worker_hijack_root_logger=False,
    beat_schedule={
        "relay": {"task": "relay.tick", "schedule": 1.0, "options": {"queue": "relay", "expires": 5}},
        **{f"periodic.{name}": {"task": "periodic.run", "schedule": float(sec), "args": [name],
                                "options": {"queue": "runner" if name == "sandbox_verify" else "periodic",
                                            "expires": sec}}
           for name, sec in PERIODIC_SECONDS.items()},
    },
)
