# Access policy checkpoint

This is an **incomplete foundation**, not automatic role/credential provisioning.

New files:
- `src/partitiondb/access.py`: framework-independent policy and credential reference types.
- `src/partitiondb/postgres_roles.py`: opt-in grant/revoke operations for pre-existing PostgreSQL roles. Requires a privileged administrative connection; **not** wired to the provisioner.
- `tests/test_access_policy.py`: initial policy tests.

Existing source remains unchanged. Version remains `0.2.0a34` until the feature is integrated and tested.

## Not implemented

- Role creation, login/password issuance, encrypted secret storage and credential rotation
- Django admin form and tenant registry schema changes
- Provisioning checkpoint lifecycle integration and recovery
- `ALTER DEFAULT PRIVILEGES` for migration owners
- Database role membership/CONNECT/CREATE/PUBLIC privilege auditing
- Tenant-specific Go credential discovery
- PostgreSQL isolation acceptance tests

**Do not use `provision_restricted_role` as a security boundary until role membership, inherited privileges, object ownership, default grants and tenant revocation have been audited and tested.**

## Validation

Python compilation and access-policy smoke test passed. Pytest collection could not run in the current environment because `psycopg` is missing.
