# Tenant types

> **In this guide:** [Configure types](#configure-types) · [Creating typed tenants](#creating-typed-tenants) · [Request and routing behavior](#request-and-routing-behavior) · [Maintenance](#maintenance-of-typed-tenants) · [Limits](#limits)

Tenant types let different tenants use different **subsets of your tenant apps** and, optionally, different **URLconfs**. Types come from a single Django registry loaded at startup. They do not change `INSTALLED_APPS` per request.

## Configure types

```python
PARTITIONDB["TENANT_TYPES"] = {
    "standard": {"APPS": ["projects"], "URLCONF": "config.standard_urls"},
    "enterprise": {"APPS": ["projects", "reporting"], "URLCONF": "config.enterprise_urls"},
}
```

Rules:

- Type names use canonical schema-style names.
- `APPS` lists unique labels from `TENANT_APPS`, and the union across all types must cover **every** tenant app. Empty subsets are allowed.
- `URLCONF` is optional.
- Shared and tenant app classifications remain disjoint.

## Creating typed tenants

`TypedTenantMixin` adds a required type field. Configure your tenant model and its shared migrations deliberately.

- Resolvers and repositories must return trusted identities, such as `Tenant(..., tenant_type="enterprise")`.
- Missing or unknown types are rejected before any database work.
- No type is ever inferred from request or job text.

Configuring `TENANT_TYPES` alone adds **no** form field and no command option. Choose one of these ways to supply the type:

| Where | How |
| --- | --- |
| `create_tenant` command | The stock command has no `--tenant-type` option. Extend its `creation_options()` to supply `tenant_data={"tenant_type": "enterprise"}` |
| Trusted application code | Pass that same mapping to `TenantWorkflow.create()` |
| Admin | Extend the provisioning form to add a type field |

Type is part of tenant identity and of the cache and storage namespaces. Untyped namespaces remain unchanged.

## Request and routing behavior

- Requests select the configured URLconf for the tenant's type.
- Model routing and migrations use the eligible app set for that type.
- Membership authorization is still required.

> [!NOTE]
> Explicit SQL and `.using()` are trusted escape hatches. Tenant types are not a permissions system.

## Maintenance of typed tenants

For inactive typed maintenance, pass the full identity:

```sh
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --tenant-type enterprise
```

- Without an explicit ID, inventory lookup supplies the type.
- Reconciliation and admission require a matching `--confirm-type`.
- Upgrade checkpoint storage while provisioning is quiescent. Old records remain untyped, and mixed old/new provisioners are unsupported once typed records exist.

## Limits

Treat a tenant's existing type, schema, alias and app eligibility as **immutable**.

- Django can record router-skipped migrations, and enabling an excluded app does not replay them.
- Type conversion needs a reviewed schema, data, metadata and asset migration.
- Not provided: a conversion command, a per-type user model, or dynamic plugin installation.
