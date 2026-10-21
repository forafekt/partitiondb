# Django restricted access workflow checkpoint

The optional `partitiondb.adapters.django.access_workflow` module composes
`DjangoAccessResolver`, `PostgresRoleManager` and `DjangoRoleBindingRecorder`
into a callback for `Provisioner(provision_access=...)`.

The host application must supply a durable `ProvisioningStore` with a cross-process
lock, an administrative PostgreSQL connection, a durable credential provider,
and a concrete tenant registry model using `TenantAccessMixin`.

The callback must only be invoked through the trusted provisioning lifecycle.
A failure in `ACCESS_PROVISIONING` is ambiguous and requires manual inspection
and `reconcile_access()`; never blindly retry.

This is NOT yet end-to-end provisioning: no Django admin action, policy
transition service, schema migration or live PostgreSQL security test is
included. Do not use in production until those are completed and verified.

Future dedicated-database isolation remains reserved, not implemented.
