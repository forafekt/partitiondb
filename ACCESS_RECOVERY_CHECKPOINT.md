# Controlled access recovery checkpoint

`AccessRecoveryService` inspects PostgreSQL and checks independent ownership,
credential and absence evidence under the same provisioning lock used for the
checkpoint transition. It never creates or adopts roles.

For `ready`, role state must be `configured`, and both ownership and credential
authentication must be independently verified. For `retry`, the role must be
absent and the evidence provider must independently verify that no secret or
role side effects remain. Role name and grants alone are insufficient.

`DjangoAccessReconciliationService` is an opt-in callable for the existing
Django Admin reconciliation endpoint. POST must contain `decision=ready` or
`decision=retry`, plus `confirm_tenant_id`. It repeats superuser checks.

**Not yet production-complete:** durable role-ownership proof, real credential
authentication verifier, durable idempotent audit sink, and an Admin inspection
preview. Do not configure permissive placeholder evidence callbacks in production.
For production, configure a durable audit sink; operation IDs and atomic
checkpoint/audit persistence are recommended. `isolated` tenant databases remain
future work.
