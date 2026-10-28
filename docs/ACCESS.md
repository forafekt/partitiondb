# Database access and credentials

> **In this guide:** [Provisioning evidence](#provisioning-evidence) · [Credential provider contract](#credential-provider-contract) · [Development provider](#development-provider) · [OpenBao](#self-hosted-openbao-kv-v2)

Restricted tenants use a **dedicated PostgreSQL login**. This is independent of Django staff status, memberships and viewer/editor/admin permissions.

| Tenant kind | Access model |
| --- | --- |
| Shared-role tenant | No access-operation rows needed |
| Restricted tenant | Dedicated login, tracked by the evidence below |
| Dedicated database | Always restricted |

## Provisioning evidence

The library follows a journaled sequence, so an interruption never leaves ambiguity about what was created:

1. Write an access operation **before** any external work.
2. Create a `NOLOGIN` role and store its password.
3. Grant tenant objects.
4. Enable login.
5. Record the role OID receipt and bind it to the tenant.

Activation then verifies the completed journal, the receipt, the actual role attributes and grants, and a fresh password connection.

| Shared model | Purpose |
| --- | --- |
| `AccessOperation` | Durable intent and provisioning transitions |
| `RoleOwnershipReceipt` | Operation, role OID and credential-reference ownership |
| `AccessRecoveryAudit` | Explicit operator recovery decisions |
| `DatabaseOperation` / `DatabaseRecoveryAudit` | Database creation intent, receipt and recovery |
| `TenantDatabaseTarget` | Persisted tenant database routing |
| `DatabaseServerProfile` | Verified, operator-managed server availability |

Evidence is view-only in admin; server profiles have explicit management controls. Use the tenant's **access-status page** for inspection and recovery. Passwords are never displayed there.

> [!NOTE]
> A completed operation records prior success, not continuous health. A broken secret can leave an operation "completed" while authentication fails. Structural role inspection alone does not establish ownership or password validity.

### Runtime behavior

- `atomic_tenant` obtains credentials in shared context and opens a fresh scoped connection, changing only its user and password.
- Trusted migration and seed code may use `privileged=True`. **Request handlers must not.**
- Tenant roles must not be able to access the registry, journals or secret store.
- Review external `PUBLIC`, role and function grants yourself: the library's inspection is not a complete PostgreSQL security audit.

## Credential provider contract

`CREDENTIAL_PROVIDER` is a zero-argument factory that returns the store. Keep construction lazy.

| Method | Requirement |
| --- | --- |
| `put` | Must not overwrite an existing reference |
| `get` | Must report failures |
| `absent(reference)` | Used by recovery. Return an explicit, authoritative `True` only when the secret is truly absent |

Denied access, timeouts and service failures are **not** absence. Changing providers does not migrate existing passwords or references.

## Development provider

For local POSIX development:

```python
PARTITIONDB.update(
    CREDENTIAL_PROVIDER="partitiondb.adapters.django.databases.credentials.development_credentials",
    DEVELOPMENT_CREDENTIALS_DIR=BASE_DIR / ".secrets",
)
```

> [!WARNING]
> This provider stores secrets in private files with hashed names. It is **not an encrypted vault**. Protect its trusted parent directory and preserve the secrets for any existing tenants. Do not use it in production.

## Self-hosted OpenBao KV v2

Run OpenBao as an external service inside your network, then configure:

```python
PARTITIONDB.update(
    CREDENTIAL_PROVIDER="partitiondb.adapters.django.databases.credentials.openbao_credentials",
    CREDENTIAL_PROVIDER_OPTIONS={
        "address": "https://secrets.internal:8200",
        "mount": "secret",
        "prefix": "partitiondb",
        "mount_accessor": "kv_REPLACE_WITH_ACTUAL_ACCESSOR",
        "token_file": "/run/partitiondb/bao-token",
        "ca_file": "/etc/partitiondb/customer-ca.pem",
        "timeout": 5,
    },
)
```

### Authentication and transport

- Use workload authentication with agent-managed token renewal.
- The token file must be a private, regular, non-symlink file owned by the application UID (mode `0600` on POSIX).
- TLS verification is required.
- The client disables environment proxies and redirects, hashes references, and uses `CAS=0` writes.
- It pins the KV v2 mount accessor. The internal mount-preflight API must be accepted by your deployed server version.

### Policy

A normal provisioning policy:

```hcl
path "secret/data/partitiondb/*" {
  capabilities = ["create", "update", "read"]
}
path "secret/metadata/partitiondb/*" {
  capabilities = ["read"]
}
```

Recommendations:

- Enable mount-level `cas_required`.
- Use a separate, read-only runtime identity where your application composition allows it.

### Deletion and absence

- Metadata absence is checked conservatively: soft-deleted or destroyed versions still block absence-based recovery.
- Explicit provider `delete` permanently removes **all versions and metadata** and requires metadata-delete permission. Ordinary provisioning never calls it on failure.

### Out of scope

PartitionDB does not operate production OpenBao. That includes unsealing, HA, workload identity, backup/restore, leased roles and coordinated password rotation.

## Related guides

- [Local stack](LOCAL_INFRASTRUCTURE.md): a runnable OpenBao integration.
- [Operations](OPERATIONS.md): recovery and grant refresh.
