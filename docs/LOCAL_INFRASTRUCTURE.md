# Local application stack

> **In this guide:** [Start the stack](#start-the-stack) · [Services](#services) · [Create and exercise tenants](#create-and-exercise-tenants) · [Verify the worker](#verify-the-celery-worker) · [Persistence and recovery](#persistence-and-recovery) · [Troubleshooting](#troubleshooting) · [Deployment limits](#deployment-limits)

A single Docker Compose command brings up a complete, runnable PartitionDB example: Django web app, PostgreSQL (two clusters), OpenBao, RabbitMQ, Redis, Mailpit and a Celery worker.

> [!WARNING]
> This stack is for **local integration testing only**. It uses fixture passwords and local bootstrap recovery material. See [deployment limits](#deployment-limits).

## Start the stack

Run from the repository root (requires Compose v2.20+):

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail=80 initialize web worker bao-renew
```

`compose.yml` includes `compose.local.yml`. Selecting `-f compose.local.yml` explicitly starts the same `partitiondb-local` project.

The image installs a wheel built from the checkout. The `initialize` service prepares TLS, OpenBao, the control database, migrations, checkpoints, static files, server profiles and an administrator. Existing database data and account passwords are kept across restarts.

## Services

| Service | Address | Local login |
| --- | --- | --- |
| Platform admin | http://localhost:8000/admin/ | `admin` / `local-admin-only` |
| Mailpit | http://localhost:58025 | No login |
| RabbitMQ management | http://localhost:15673 | `partitiondb` / `local-broker-only` |
| OpenBao | https://localhost:58201/ui/ | Generated root token (below) |
| PostgreSQL control / tenant server | `127.0.0.1:55433` / `127.0.0.1:55434` | `partitiondb` / `local-database-only` |

### Signing in to the OpenBao UI

Accept or trust the generated certificate for local OpenBao UI access. The login token is generated (it is **not** `partitiondb-test-only`). Retrieve it locally:

```sh
docker compose run --rm --no-deps bao-bootstrap \
  python -c 'import json; print(json.load(open("/bootstrap/initialized.json"))["root_token"])'
```

Select **Token** in the UI. Credentials live beneath `secret/partitiondb` with hashed names.

> [!CAUTION]
> The application uses a scoped periodic token, not the root token. Keep the root token out of screenshots, logs and application settings.

## Create and exercise tenants

1. In platform admin, open **PartitionDB tenants → Tenants → Add**.
2. Choose **New database + tenant schema**, with server `tenant_server`, name `demo`, schema `demo` and hostname `demo.localhost`. Leave the database name blank.
3. Tick the provisioning confirmation and submit. The default workspace is seeded.
4. Create an active staff user and an active tenant membership with an explicit viewer/editor/admin role.
5. Open http://demo.localhost:8000/admin/ and log in **on that host**.

Things to know:

- The platform admin manages shared accounts and memberships. The tenant admin manages that tenant's workspaces.
- A platform login does not create a tenant-host session.
- To exercise isolation, create a second tenant with a different workspace count and compare.
- Server `control` creates tenant databases on the control cluster; `tenant_server` uses the second cluster. Docker supplies the servers, and PartitionDB supplies their tenant resources.
- Tenant creation is synchronous.

## Verify the Celery worker

After creating `demo`, open an application shell:

```sh
docker compose exec web python tests/django_project/manage.py shell
```

```python
from partitiondb.adapters.django import get_tenant_model
from config.celery_local import workspace_count

tenant = get_tenant_model().objects.get(name="demo", active=True)
result = workspace_count.delay(str(tenant.pk))
result.get(timeout=20)
```

The result should match demo's workspace count. The task reloads the active tenant and enters `atomic_tenant` at execution time.

Messaging details:

- RabbitMQ uses quorum task queues and exclusive control/event queues.
- Redis stores results with a one-day expiry.
- Rebuild **both** `web` and `worker` after configuration changes.
- The old classic `celery` queue is retained, and messages already there are not moved.
- No periodic scheduler is started.

See [Celery tasks](CELERY.md) for the task API.

### Test email capture

In the same shell:

```python
from django.core.mail import send_mail

send_mail("Local stack", "Mail capture works", "sender@localhost", ["recipient@localhost"])
```

Then open Mailpit at http://localhost:58025.

## Persistence and recovery

```sh
docker compose down                              # stop; keep named volumes
docker compose up --build -d                     # start again
docker compose --profile operations run --rm backup
```

- `down` preserves named volumes. **`down -v` destroys local data.**
- The `backup` service snapshots both PostgreSQL clusters and roles. Complete recovery also requires OpenBao data/recovery material and private uploads.
- Store backups securely and rehearse restores. New cluster identities need explicit evidence reconciliation.
- Uploads have no unauthenticated media route. Static assets are public.

### Restarting OpenBao

After restarting OpenBao alone, rerun `docker compose run --rm bao-bootstrap` to unseal it.

- The bootstrap operator keeps root/recovery material in a separate volume that is unavailable to `web` and `worker`.
- `bao-renew` renews the scoped one-hour token every ten minutes, and bootstrap replaces an expired or revoked token.
- Do not delete only the vault or recovery volume while retaining tenant data.

## Troubleshooting

On startup failure, inspect:

```sh
docker compose logs --tail=80 initialize bao-bootstrap postgres-control postgres-tenants
```

| Symptom | What to do |
| --- | --- |
| Older TLS permission failure | Run `docker compose run --rm certificates`, then start again. Setup fixes the PostgreSQL key to `0600` without replacing it |
| Control database missing after an interrupted initialization | Handled automatically before migrations |
| Delayed-exchange warnings on first worker startup | Can occur while Celery builds its chain. Persistent queue errors need investigation |

## Deployment limits

This example uses local fixture passwords, a superuser database administrator and local bootstrap recovery material.

**What it does provide:** PostgreSQL and OpenBao verify TLS, published ports bind to loopback, and the application runs non-root with a read-only root filesystem.

**What it does not provide:** web-edge HTTP, single-node services and local workload bootstrap are for integration testing only. Production needs:

- HTTPS
- operator-managed seal and recovery
- workload authentication and renewal
- least-privilege identities
- network controls
- locked images and dependencies
- monitoring
- coordinated restores

No HA, failover or production-capacity claim follows from this example.

For the separate disposable test stack, see [testing](TESTING.md).
