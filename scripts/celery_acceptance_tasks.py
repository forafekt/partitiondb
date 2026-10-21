"""Tasks for the disposable RabbitMQ/prefork/PostgreSQL acceptance runner."""

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from celery import Celery
from kombu import Queue

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.celery import tenant_job
from partitiondb.postgres import transaction

app = Celery(
    "partitiondb-acceptance",
    broker=os.environ["PARTITIONDB_CELERY_BROKER"],
    backend=Path(os.environ["PARTITIONDB_CELERY_RESULT_DIR"]).as_uri(),
)
app.conf.update(
    task_default_queue=os.environ["PARTITIONDB_CELERY_QUEUE"],
    task_queues=[Queue(os.environ["PARTITIONDB_CELERY_QUEUE"], durable=True)],
    control_queue_exclusive=True,
    control_queue_durable=False,
    event_queue_exclusive=True,
    event_queue_durable=False,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    broker_connection_timeout=5,
    broker_connection_retry_on_startup=False,
    task_publish_retry=False,
    result_expires=120,
)


@contextmanager
def active_repository():
    # Connections open in the executing child, never in a pre-fork global.
    with psycopg.connect(os.environ["PARTITIONDB_CELERY_DSN"], autocommit=True) as connection:

        class Repository:
            def get(self, tenant_id):
                assert current_tenant(required=False) is None
                row = connection.execute(
                    "SELECT schema_name FROM public.pdb_celery_registry WHERE tenant_id = %s AND active",
                    (tenant_id,),
                ).fetchone()
                return Tenant(tenant_id, row[0]) if row else None

        yield Repository()


@app.task(bind=True, shared=False, name="partitiondb.acceptance.write", max_retries=1)
@tenant_job(active_repository)
def write(self, marker, fail=False, retry=False):
    tenant = current_tenant()
    with psycopg.connect(os.environ["PARTITIONDB_CELERY_DSN"], autocommit=True) as connection:
        runtime_user = connection.info.user
        with transaction(connection, tenant):
            assert connection.execute("SELECT tenant_id FROM identity").fetchall() == [(tenant.id,)]
            connection.execute("INSERT INTO work (marker, tenant_id) VALUES (%s, %s)", (marker, tenant.id))
            if fail:
                raise ValueError("Expected acceptance rollback")
            if retry and self.request.retries == 0:
                raise self.retry(countdown=0)
            # A bounded overlap makes both prefork children exercise the workload.
            connection.execute("SELECT pg_sleep(0.05)")
    return {
        "tenant": tenant.id,
        "pid": os.getpid(),
        "retries": self.request.retries,
        "role": runtime_user,
    }


@app.task(shared=False, name="partitiondb.acceptance.probe")
def probe():
    # Deliberately unwrapped: observe actual context left by the previous message.
    return {"clean": current_tenant(required=False) is None, "pid": os.getpid()}
