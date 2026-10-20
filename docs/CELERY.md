# Optional Celery task adapter

Install `partitiondb[celery]` for Celery 5.6. Django and PostgreSQL remain separate
extras. Importing the core never imports Celery. The development extra includes
Celery so release validation executes its tests rather than skipping them.

## Task definition and publishing

```python
from contextlib import contextmanager
from celery import Celery
from partitiondb import current_tenant
from partitiondb.adapters.celery import tenant_job, tenant_signature

app = Celery("myapp")


@contextmanager
def active_repository():
    # Application-owned factory. Open a control-plane repository; get(id) must
    # return only active tenants and current trusted routing, or None.
    with open_application_registry() as repository:
        yield repository


@app.task
@tenant_job(active_repository)
def rebuild_report(report_id):
    tenant = current_tenant()
    # Establish the appropriate transaction boundary here before database work.
    return build_application_report(tenant, report_id)


# Within tenant scope, capture the stable ID now, even if publication is delayed.
signature = tenant_signature(rebuild_report, args=(42,), immutable=True)
signature.apply_async()

# A trusted control-plane publisher supplies the ID explicitly.
tenant_signature(rebuild_report, args=(42,), tenant_id="org-42").apply_async()
```

Application registry/report functions above are integration points, not supplied
library functions. The task decorator must be outermost. tenant_job wraps the task
body, not Celery's request stack or worker implementation. It supports bound
tasks too: use `@app.task(bind=True)` and accept self before ordinary arguments.
Task functions must be synchronous and non-generator functions.

tenant_signature builds a Celery signature carrying the custom
partitiondb_tenant_id header, with no schema name, alias or credentials added by
the adapter. It copies custom headers. It rejects missing identity, a conflicting
reserved header or an explicit tenant ID different from the publishing scope.
The caller is still responsible for all ordinary arguments and other headers.
Do not put secrets or stale routing into them.

Every delivery and retry resolves the ID again in shared context. Missing or
inactive tenants and mismatched repository results reject before body execution.
The factory is a context manager so its resources close on success, failure and
retry. Tenant scope resets before factory exit; the previous caller scope is
restored afterward. The adapter does not open a tenant database transaction.
For Django, wrap ORM work in atomic_tenant(current_tenant()); for psycopg, use
transaction(connection, current_tenant()) on an exclusively leased connection.

## Retries, canvas and commit timing

Celery's self.retry retains custom request headers; the adapter leaves retry
configuration, acknowledgement and idempotency policy with the application.
Retries still re-resolve the registry, so a tenant disabled or moved after the
first attempt is checked again. Avoid automatic retry policies that endlessly
retry MissingTenant or authorization failures. Required work needs a durable
outbox/idempotency design; this adapter does not provide exactly-once execution.

Every chain/group/chord member, callback and error handler that needs tenant scope
must have its own tenant-bound signature. No global publish signal or implicit
canvas propagation is installed. Use immutable=True when a canvas member should
not consume the previous member's result. Plain task.delay(), app.send_task() and
unbound signatures do not acquire a tenant header automatically and will fail
closed at execution unless a trusted publisher supplies the header itself.
Direct task.run() calls outside a Celery request fail; use a normal service
function with an explicit scope for synchronous application work.

If publication depends on a database commit, build the signature within scope
and publish after commit using application infrastructure. Never assume that a
message and a database write commit atomically.

## Trust and validation limits

The ID header is not authorization or a signature. Anyone able to publish trusted
task messages can name a tenant; authenticate publishers, restrict broker access
and enforce job-level permissions in application services. Protect result backend
access and task arguments as well. Use appropriate serialization and broker TLS.

Local tests use real Celery eager requests/retries plus a solo worker with memory
transport for successful and failed messages, different tenants and post-task
context cleanup. The separate acceptance below covers RabbitMQ and prefork. Redis delivery,
worker-loss redelivery, rate limits and a complete production deployment are outside
that acceptance.
Eventlet/gevent pools and async coroutine task execution are not supported by
this adapter's current acceptance scope. Test the selected external broker and
worker topology before adopting this optional integration in production.

Upstream API references: [task request headers and bound tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[Celery task API](https://docs.celeryq.dev/en/stable/reference/celery.app.task.html).

## External broker / prefork acceptance

Run the independent disposable acceptance command:

```sh
docker compose run --rm celery_acceptance
```

This launches the isolated RabbitMQ and PostgreSQL services, installs only the
Celery/PostgreSQL extras, creates a random database, runtime login and task queue,
and starts a separate Celery worker with two prefork children. It publishes 20
tenant jobs and checks that both children execute work using the restricted login.
It also checks retry rollback, explicit task failure rollback, inactive-tenant and
missing-header denial, unwrapped context probes, row contents and warm shutdown.
The worker receives the runtime DSN through its environment; the parent removes
its administrative test/example DSNs from that environment. Database routing
credentials do not travel in task arguments or custom identity headers.

Only the parent uses CREATEDB/CREATEROLE credentials for setup and cleanup. The
role is granted registry SELECT plus tenant schema USAGE and table SELECT/INSERT.
No schema/table creation privileges are granted. No connection is opened in the
worker's pre-fork module globals. On exit the runner stops its owned worker group,
deletes its queue, drops only its created database and then removes its role.
A partial setup failure cleans only resources successfully created by that run.
The passing marker is printed after cleanup completes. Worker diagnostics on
failure redact the configured broker URL and generated database credentials.

These services have test-only credentials and no RabbitMQ host port. Do not use
production endpoints or credentials. They are acceptance fixtures, not a production
Celery deployment. Broker services remain running for reuse after the command;
`docker compose down` stops this disposable project when finished.

This check passed in the release validation run. It does not cover broker failover, worker-loss redelivery,
TLS, long-duration soak, gevent/eventlet, or a Django ORM task body.

### RabbitMQ queue policies

The acceptance runner configures exclusive control/event queues and durable task
queues. It uses a temporary filesystem result directory shared by controller and
workers
in the same container, rather than Celery 5.6's legacy RPC result reply queues.
That directory is owned by the runner and removed after worker cleanup. This tests
RabbitMQ task delivery, not an AMQP result backend or a distributed filesystem.
No deprecated broker feature is enabled and the tenant adapter itself is unchanged.

A local subprocess regression inspects Celery/Kombu's real control/request/reply
and task queue entities to ensure every queue is durable or exclusive; it also
checks a filesystem result roundtrip. See RELEASE_VALIDATION.md for executed acceptance.
References: [Celery queue settings](https://docs.celeryq.dev/en/v5.6.3/userguide/configuration.html),
[RabbitMQ queue types](https://www.rabbitmq.com/docs/queues).
