# Django integration fixture

This project supplies the models and migrations used by the pytest suite. It is
not ActiveLane's production configuration. Use its existing migrations; do not
regenerate or delete migration history as a setup step.

From the repository root, run the isolated database validator:

```sh
docker compose run --rm validation
```

For manual fixture use, install `partitiondb[django]`, configure a disposable
PostgreSQL database through `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_HOST` and `POSTGRES_PORT`, then run:

```sh
python tests/django_project/manage.py check
python tests/django_project/manage.py migrate --noinput
```

The fixture defaults to localhost port 55432. Its settings include a test-only
secret key, debug mode and test credentials. Never deploy these settings.

See [Django setup](../../docs/DJANGO.md) and
[restricted access](../../docs/ACCESS.md) for application integration.

## Browser walkthrough

1. Create a superuser and sign in at `http://localhost:8000/admin/`.
2. Provision an organization with a domain such as `demo.localhost`.
3. Create an active staff account and an active membership in that organization.
   Choose viewer, editor or administrator according to the intended access.
4. Open `http://demo.localhost:8000/admin/` and log in again on that host.
   Its host-only session is independent of the platform session.

The platform admin manages shared users, organizations, domains and memberships.
The tenant admin manages workspaces in the selected organization's schema.
See [the settings manager](../../docs/SETTINGS.md) and
[login integration](../../docs/TENANT_LOGIN.md) to customize an external provider
in your application. PartitionDB has no built-in provider mode.

## Restricted PostgreSQL access

Run migrations after updating the checkout. Organization creation now offers
shared or dedicated database access; existing organizations remain shared.
The dedicated option populates the access journal and receipt, verifies the role,
and uses that role for tenant requests. Open the organization's access-status
link for audited recovery of interrupted provisioning. See
[restricted access](../../docs/ACCESS.md) for secret storage, cleanup and deployment requirements.
