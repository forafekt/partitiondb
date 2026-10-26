# Celery tasks

Install `partitiondb[celery]` for Celery 5.6. The core imports no Celery dependency.
For a runnable Django worker and result backend, see [local setup](LOCAL_INFRASTRUCTURE.md).

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

The registry/report functions above are application integration points. The
repository's `get(id)` must return a current active `Tenant` or `None`; the factory
owns cleanup. `DjangoRegistry` can supply the repository implementation. The task
decorator is outermost; bound synchronous tasks are supported. Coroutine and
generator tasks are rejected.

Only the stable ID travels in `partitiondb_tenant_id`; routing and credentials
are re-resolved for every execution/retry. The task body still owns its database
transaction: use `atomic_tenant(current_tenant())` for Django or a standalone
psycopg transaction. Resources and context are restored on failure/retry.

Within scope, `tenant_signature` captures its tenant ID and rejects a conflicting
explicit ID/header. Every canvas member, callback and error handler needing scope
must carry its own bound signature. Plain `.delay()` on a decorated task does not
add identity. Build signatures inside scope, publish after commit, and use an
outbox/idempotency design for required work. No exactly-once guarantee is supplied.

Publishers are trusted: an ID header grants no authorization by itself. Restrict
broker/result-store access and enforce job permissions. Do not send ORM instances,
connections, passwords or stale schemas in payloads. Missing/inactive identity
should not trigger an endless automatic retry policy. Eventlet/gevent and async
pools are outside the accepted scope.

## RabbitMQ compatibility

For Celery 5.6 and current RabbitMQ, use exclusive control/event queues and quorum
task queues with publisher confirmations. Quorum detection disables global QoS.
The local settings demonstrate these options; Redis replaces incompatible legacy
RPC result queues. The old classic queue is not automatically migrated.
A single-node quorum queue does not provide multi-node availability.

The isolated acceptance worker uses a temporary filesystem result backend and
checks restricted PostgreSQL work with two prefork children, retries, rollback,
identity denial and cleanup. It does not establish broker failover, worker-loss
redelivery, production TLS or general distributed result-store behavior.

```sh
docker compose -f compose.tests.yml --profile celery run --rm celery_acceptance
```

[Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
and [quorum queues](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)
describe the upstream contracts.
