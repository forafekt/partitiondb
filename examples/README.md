# Runnable examples

Use a **disposable** PostgreSQL database. Both examples create schemas and tables;
these are local development demonstrations, without authentication or production
credential separation. Do not expose the Django development server publicly.

From the repository root, activate an environment with `.[django,dev]` installed:

```sh
docker compose up -d --wait postgres
export PARTITIONDB_EXAMPLE_DSN='postgresql://partitiondb:test-only@localhost:55432/partitiondb'
```

## Plain Python / psycopg

```sh
python examples/plain_python/demo.py provision
python examples/plain_python/demo.py list
python examples/plain_python/demo.py cleanup
```

Provision creates `example_alpha` and `example_beta`, writes one note per tenant,
and checks that each tenant sees only its own note. Listing resolves a tenant ID
through `job_scope` before opening an adapter transaction, as a worker would.
Cleanup drops **only those two demo schemas**, including their contents. Repeated
provisioning intentionally fails rather than adopting an existing schema. Partial
failures require inspecting and cleaning up the demo schemas.

## Django

```sh
python examples/django_demo/manage.py check
python examples/django_demo/manage.py migrate --noinput
python examples/django_demo/manage.py provision_demo
python examples/django_demo/manage.py check_demo
python examples/django_demo/manage.py migrate_tenant demo_alpha --plan
python examples/django_demo/manage.py migrate_tenant demo_alpha --check
python examples/django_demo/manage.py runserver 127.0.0.1:8000
```

Visit `http://alpha.localhost:8000/notes/` and
`http://beta.localhost:8000/notes/`. Alternatively use curl with
`-H 'Host: alpha.localhost'` against `http://127.0.0.1:8000/notes/`.

The shared `registry.Organization` stores schema, database alias and active state;
`registry.Domain` stores canonical hostnames and primary-domain selection. Both use
the optional PartitionDB abstract mixins. The
resolver returns only active organizations. Tenant middleware opens a transaction,
reads tenant-local `projects.Note` records, and closes the connection afterward.
The example omits auth/session apps deliberately; centralized authentication is
not yet integrated with this adapter. A hostname selects routing, not permission.

`provision_demo` writes inactive registry metadata, creates the schema, runs tenant
migrations, seeds notes and then activates the organization. It retains failed
state for inspection rather than deleting data. It is not an automatic recovery
orchestration service. Repeated runs fail when metadata already exists.

`check_demo` sends real Django test-client requests through middleware for both
hosts and verifies separate data and context cleanup. CI runs both examples
against its disposable PostgreSQL service. See [release validation](../docs/RELEASE_VALIDATION.md) for executed results.

To reset the Django example, recreate the entire **disposable database**. Avoid
running migrations or deleting schemas belonging to another application.

## Durable provisioning example

```sh
python examples/plain_python/recoverable.py install
python examples/plain_python/recoverable.py register
python examples/plain_python/recoverable.py run
python examples/plain_python/recoverable.py status
python examples/plain_python/recoverable.py run
```

The second run returns the ACTIVE checkpoint without repeating callbacks. This
example leaves its metadata table/row and `recoverable_example` schema in the
disposable database for inspection. Registration deliberately fails on duplicates.
Use a fresh disposable database to restart from scratch. Read
[provisioning](../docs/PROVISIONING.md) before attempting recovery from a failed run.
