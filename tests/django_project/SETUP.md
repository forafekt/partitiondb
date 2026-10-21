# PartitionDB Django test project

This fixture is derived from the supplied project, not from ActiveLane's production settings.

Fixes:
- Register the custom user as AUTH_USER_MODEL = "accounts.User".
- Preserve the actual PARTITIONDB configuration (remove duplicate overwrite).
- Include partitiondb_access_control among SHARED_APPS, so its models
  are recognized as shared and routed to the shared schema.
- Use explicit PartitionDBConfig and access-control AppConfig paths.
- Default PostgreSQL connection to 127.0.0.1:55432 for host Poetry use.
  In Compose, set POSTGRES_HOST=postgres and POSTGRES_PORT=5432.

From tests/django_project:
  poetry run python manage.py check
  poetry run python manage.py makemigrations accounts organizations workspaces audit partitiondb_access_control
  poetry run python manage.py migrate

Important:
- The source archive's previously deleted migration files are not
  automatically reconstructed. Generate and review them, then commit.
- A development database with old migration history may need a fresh
  disposable database. Do not reset a database with valuable data.
- The library's own Django migration belongs under src/partitiondb/.
- Production credentials and settings must not be copied from this fixture.
