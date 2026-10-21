# Restricted role manager checkpoint

This is an **experimental, opt-in** administrative API. Existing provisioning behavior is unchanged.

New modules:
- `src/partitiondb/credentials.py`: credential provider protocol.
- `src/partitiondb/postgres_role_manager.py`: explicit role creation and disable operations.
- `tests/test_role_manager.py`: initial unit checks.

Not implemented: integration with recoverable provisioning checkpoints; safe resume after partial role creation; future-object grants; role-name collision handling across aliases; credential rotation and secret deletion; terminating active sessions; privilege hardening of PUBLIC, role inheritance, and database CONNECT; automatic Go secret resolution. Do not enable this for production tenants.

Run: `docker compose run --build --rm tests`.
