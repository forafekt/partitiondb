# Access provisioning checkpoint (development)

This update adds an optional `provision_access` callback to `Provisioner`. Existing callers remain unchanged. When configured, the callback runs after migration and before verification/activation, under the durable tenant lock. It should invoke a deployment-supplied role manager and credential provider using a separate privileged connection.

Stages: `MIGRATED -> ACCESS_PROVISIONING -> ACCESS_READY -> ACTIVE`.

`ACCESS_PROVISIONING` is intentionally ambiguous: if a callback or checkpoint write fails, retrying blindly could collide with an already-created role or overwrite a secret. The provisioner stops and requires operator inspection followed by `reconcile_access(..., confirm=tenant, access_ready=True|False)`. Only use `False` after verifying that all role and secret side effects have been cleaned up; only use `True` after verifying role ownership, privileges, secret storage and authentication. No automatic reconciliation or deletion is performed.

**Not implemented:** Django admin access-policy configuration, durable access-policy registry, automatic callback wiring, role ownership tracking, credential delivery to Go, grants on objects created by future migrations, existing session termination, rotation and database-level security integration tests. Do not enable restricted roles in production based on this checkpoint alone.

Test with `docker compose run --build --rm tests` or target `tests/test_access_provisioning.py` in the test container. Python syntax compilation passed locally; pytest could not run locally because `psycopg` is unavailable.
