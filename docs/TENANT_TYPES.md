# Tenant types

Types select app subsets and optional URLconfs from one startup-loaded Django
registry; they do not change `INSTALLED_APPS` per request.

```python
PARTITIONDB["TENANT_TYPES"] = {
    "standard": {"APPS": ["projects"], "URLCONF": "config.standard_urls"},
    "enterprise": {"APPS": ["projects", "reporting"], "URLCONF": "config.enterprise_urls"},
}
```

Type names use canonical schema-style names. APPS contains unique labels from
`TENANT_APPS`; the union must cover all tenant apps. Empty subsets are allowed.
URLCONF is optional. Shared and tenant classifications remain disjoint.

`TypedTenantMixin` adds a required type field; configure the tenant model and its
shared migrations deliberately. Resolvers/repositories must return trusted
`Tenant(..., tenant_type="enterprise")` identities. Missing/unknown types reject
before database work. The stock `create_tenant` command has no `--tenant-type`
option. Extend its `creation_options()` to supply
`tenant_data={"tenant_type": "enterprise"}`, or pass that mapping to
`TenantWorkflow.create()` in trusted application code. Extend the provisioning
form for typed admin creation; configuring `TENANT_TYPES` alone adds no form field.
Type is part of identity and cache/storage namespaces;
untyped namespaces remain unchanged. No type is inferred from request/job text.

Requests select the configured URLconf, and model routing/migrations use the
eligible app set. Explicit SQL/`.using()` are trusted escape hatches, not a type
permissions system. Membership authorization is still required.

For inactive typed maintenance, pass the full identity:

```sh
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --tenant-type enterprise
```

Without an explicit ID, inventory lookup supplies the type. Reconciliation and
admission require matching `--confirm-type`. Upgrade checkpoint storage while
provisioning is quiescent; old records remain untyped. Mixed old/new provisioners
are unsupported once typed records exist.

Treat existing type/schema/alias and app eligibility as immutable. Django can
record router-skipped migrations; enabling an excluded app does not replay them.
Type conversion needs reviewed schema/data/metadata and asset migration. No
conversion command, per-type user model or dynamic plugin installation is supplied.
