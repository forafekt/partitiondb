# Restricted role inspection checkpoint

`PostgresRoleInspector` provides read-only structural evidence about an expected
PostgreSQL role. `CONFIGURED` **does not mean trusted or ready for automatic
reconciliation**. It does not establish role ownership, verify secret persistence,
authenticate as the role, inspect grants on other schemas or databases, or check
future object grants. It never changes a provisioning checkpoint.

Use inspection results as an operator diagnostic only. Continue to require
independent ownership evidence, tenant lifecycle locking, credential validation,
and explicit operator confirmation before invoking `Provisioner.reconcile_access`.
Do not wire `CONFIGURED` to automatic `access_ready=True`.

For Django, expose results read-only through an authorized admin service after
proper permission checks; never invoke inspection from normal model saves.
