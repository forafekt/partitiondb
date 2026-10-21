# Django role binding checkpoint

`DjangoRoleBindingRecorder` is an opt-in, explicit provisioning callback. It
persists the result of `PostgresRoleManager.provision()` to a concrete Django
registry model implementing `TenantAccessMixin`. Wire it as the
`on_provisioned` argument to `AccessProvisioningAdapter` and pass that adapter
as `Provisioner(provision_access=...)`. The provisioner must hold a durable,
exclusive cross-process tenant lock for the whole operation.

It validates the tenant identity, registered restricted policy, credential
reference, and role name, then performs a guarded update under row lock. It
never runs from normal `save()` or Django Admin edits. It does not store secrets.

**Not yet end-to-end:** automatic registration, access policy transition
operations, an actual secret backend, live PostgreSQL privilege tests, and
operator-confirmed reconciliation still need implementing and testing. If
role creation succeeds but binding persistence fails, provisioning remains
ambiguous and must not be blindly retried.

The future isolated-database policy is intentionally not enabled.
