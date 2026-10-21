# Durable restricted-access operation checkpoint

This opt-in coordinator writes a unique operation intent to a trusted shared
control-plane journal **before** PostgreSQL role creation or secret storage.
It records role OID evidence after provisioning, then writes the Django role
binding and completes the journal. The existing `Provisioner` owns the tenant
lock and `ACCESS_PROVISIONING` ambiguity checkpoint.

Wire `RestrictedAccessCoordinator` as `Provisioner(provision_access=...)`.
Use a concrete `AccessOperationMixin` subclass with migrations and
`DjangoAccessOperationJournal`, a trusted PostgreSQL role OID lookup,
`DjangoOwnershipReceiptStore`, and `DjangoRoleBindingRecorder`.

**Not yet production-complete:** a crash after role creation but before
persisting its OID cannot establish ownership from the intent alone. Do not
adopt the role by name. Recovery needs stronger pre-DDL ownership evidence,
credential validation, and manual intervention for genuinely ambiguous cases.
The coordinator does not automatically retry, rotate, disable, or delete roles.
The journal's unique `(tenant_id, database_alias)` constraint intentionally
blocks repeat operations until a controlled lifecycle transition is added.

Keep the operation journal and ownership receipts in a protected shared
control-plane schema. The dedicated database isolation policy remains future.
