# Local application stack

Run from the repository root:

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail=80 initialize web worker bao-renew
```

`compose.yml` includes `compose.local.yml` (Compose v2.20+). Selecting
`-f compose.local.yml` explicitly starts the same `partitiondb-local` project.
The image installs a wheel from the checkout. Initialization prepares TLS,
OpenBao, the control database, migrations, checkpoints, static files, server
profiles and an administrator. Existing database data and account passwords remain.

| Service | Address | Local login |
| --- | --- | --- |
| Platform admin | http://localhost:8000/admin/ | `admin` / `local-admin-only` |
| Mailpit | http://localhost:58025 | No login |
| RabbitMQ management | http://localhost:15673 | `partitiondb` / `local-broker-only` |
| OpenBao | https://localhost:58201/ui/ | Generated root token below |
| PostgreSQL control / tenant server | `127.0.0.1:55433` / `127.0.0.1:55434` | `partitiondb` / `local-database-only` |

Accept/trust the generated certificate for local OpenBao UI access. Its login
token is generated, not `partitiondb-test-only`; retrieve it locally:

```sh
docker compose run --rm --no-deps bao-bootstrap \
  python -c 'import json; print(json.load(open("/bootstrap/initialized.json"))["root_token"])'
```

Select **Token** in the UI. Credentials are beneath `secret/partitiondb` with
hashed names. The application uses a scoped periodic token, not this root token.
Keep the root token out of screenshots, logs and application settings.

## Create and exercise tenants

1. In platform admin, open **Organizations → Add**.
2. Choose **New database + tenant schema**, server `tenant_server`, name `demo`,
   schema `demo`, hostname `demo.localhost`. Leave the database name blank.
3. Tick the provisioning confirmation and submit. The default workspace is seeded.
4. Create an active staff user and an active organization membership.
5. Open http://demo.localhost:8000/admin/ and log in on that host.

The platform manages shared accounts/memberships; the tenant admin manages its
workspaces. A platform login does not establish a tenant-host session. Compare a
second tenant with a different workspace count to exercise isolation. Server
`control` creates tenant databases on the control cluster; `tenant_server` uses
the second cluster. Docker supplies servers; PartitionDB supplies their tenant
resources. Tenant creation is synchronous.

After creating `demo`, verify the worker in an application shell:

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

The result should match demo's workspace count. The task reloads the active tenant
and enters `atomic_tenant` at execution. RabbitMQ uses quorum tasks and exclusive
control/event queues; Redis stores results with one-day expiry. Rebuild both web
and worker after configuration changes. The old classic `celery` queue is retained;
messages already there are not moved. No periodic scheduler is started.

To exercise email in the same shell:

```python
from django.core.mail import send_mail

send_mail("Local stack", "Mail capture works", "sender@localhost", ["recipient@localhost"])
```

## Persistence and recovery

```sh
docker compose down
docker compose up --build -d
docker compose --profile operations run --rm backup
```

`down` preserves named volumes. `down -v` destroys the local data. The backup
service snapshots both PostgreSQL clusters and roles; complete recovery also
requires OpenBao data/recovery material and private uploads. Store backups securely
and rehearse restores; new cluster identities need explicit evidence reconciliation.
Uploads have no unauthenticated media route. Static assets are public.

After restarting OpenBao alone, rerun `docker compose run --rm bao-bootstrap` to
unseal it. That local operator retains root/recovery material in a separate volume
unavailable to web/worker. `bao-renew` renews the scoped one-hour token every ten
minutes; bootstrap replaces an expired/revoked token. Do not delete only the vault
or recovery volume while retaining tenant data.

On startup failure, inspect `docker compose logs --tail=80 initialize
bao-bootstrap postgres-control postgres-tenants`. For an older TLS permission
failure, rerun `docker compose run --rm certificates`, then start again. The setup
fixes the PostgreSQL key to 0600 without replacing it. Missing control database
creation after interrupted initialization is handled before migrations. Delayed
exchange warnings during first worker startup can occur while Celery builds its
chain; persistent queue errors require investigation.

## Deployment limits

This stack uses local fixture passwords, a superuser database administrator and
local bootstrap recovery material. PostgreSQL/OpenBao verify TLS, published ports
bind to loopback, and the application runs non-root with a read-only root
filesystem. Web-edge HTTP, single-node services and local workload bootstrap are
for integration testing. Production needs HTTPS, operator-managed seal/recovery,
workload authentication/renewal, least-privilege identities, network controls,
locked images/dependencies, monitoring and coordinated restores. No HA/failover
or production capacity claim follows from this example.

Use [testing](TESTING.md) for the separate disposable test stack.
