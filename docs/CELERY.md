# Celery tasks

> **In this guide:** [Install](#install) · [Define a tenant-aware task](#define-a-tenant-aware-task) · [How identity travels](#how-identity-travels) · [Rules for publishers](#rules-for-publishers) · [RabbitMQ compatibility](#rabbitmq-compatibility)

Run background work inside the correct tenant scope. Only a stable tenant ID travels with the job. Routing and credentials are re-resolved on **every** execution and retry.

## Install

```sh
pip install "partitiondb[celery]"   # Celery 5.6
```

The core imports no Celery dependency. For a runnable Django worker and result backend, see the [local setup](LOCAL_INFRASTRUCTURE.md#verify-the-celery-worker).

## Define a tenant-aware task

```python
from contextlib import contextmanager
from celery import Celery
from partitiondb import current_tenant
from partitiondb.adapters.celery import tenant_job, tenant_signature

app = Celery("myapp")


@contextmanager
def active_repository():
    with open_application_registry() as repository:
        yield repository


@app.task
@tenant_job(active_repository)
def rebuild_report(report_id):
    return build_report(current_tenant(), report_id)


tenant_signature(rebuild_report, args=(42,), tenant_id="customer-42").apply_async()
```

`open_application_registry` and `build_report` are your own integration points.

**Requirements**

- The repository's `get(id)` must return a current, **active** `Tenant`, or `None`. The factory owns cleanup.
- `partitiondb.adapters.django.registry.DjangoRegistry` can supply the repository implementation. Wrap its construction in a context-manager factory like the one above.
- The task decorator (`@app.task`) must be **outermost**, with `@tenant_job` directly beneath it. Bound synchronous tasks are supported.
- Coroutine and generator tasks are rejected.

## How identity travels

- Only the stable ID is sent, in the `partitiondb_tenant_id` header.
- Resources and context are restored on failure and retry.
- The task body still owns its database transaction. Use `atomic_tenant(current_tenant())` for Django, or a standalone psycopg transaction.

### Signatures and canvases

- Inside a tenant scope, `tenant_signature` captures its tenant ID and rejects a conflicting explicit ID or header.
- Every canvas member, callback and error handler that needs a scope must carry **its own** bound signature.
- A plain `.delay()` on a decorated task does **not** add identity.

## Rules for publishers

> [!IMPORTANT]
> Publishers are trusted. An ID header grants no authorization by itself.

- Restrict broker and result-store access, and enforce job permissions.
- Build signatures inside scope and publish **after commit**.
- Use an outbox and idempotency design for required work. No exactly-once guarantee is supplied.
- Never send ORM instances, connections, passwords or stale schemas in payloads.
- A missing or inactive identity should not trigger an endless automatic retry policy.
- Eventlet, gevent and async pools are outside the accepted scope.

## RabbitMQ compatibility

For Celery 5.6 and current RabbitMQ, use:

- exclusive control and event queues,
- quorum task queues with publisher confirmations (quorum detection disables global QoS), and
- Redis for results, replacing incompatible legacy RPC result queues.

The [local settings](LOCAL_INFRASTRUCTURE.md) demonstrate these options. The old classic queue is not migrated automatically, and a single-node quorum queue provides no multi-node availability.

### Acceptance test

```sh
docker compose -f compose.tests.yml --profile celery run --rm celery_acceptance
```

The isolated worker uses a temporary filesystem result backend and checks restricted PostgreSQL work with two prefork children, retries, rollback, identity denial and cleanup.

> [!NOTE]
> It does **not** establish broker failover, worker-loss redelivery, production TLS or general distributed result-store behavior.

### Upstream references

- [Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
- [Quorum queues and RabbitMQ](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)
