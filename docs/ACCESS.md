# Database access and credentials

Restricted tenants use a dedicated PostgreSQL login. This is independent of Django
staff status, memberships and viewer/editor/admin permissions. Shared-role tenants
need no access-operation rows. Dedicated databases always use restricted access.

## Provisioning evidence

The library writes an access operation before external work, creates a NOLOGIN
role, stores its password, grants tenant objects, enables login, records the role
OID receipt and binds it to the tenant. Activation verifies the completed journal,
receipt, actual role attributes/grants and a fresh password connection.

| Shared model | Purpose |
| --- | --- |
| AccessOperation | Durable intent and provisioning transitions |
| RoleOwnershipReceipt | Operation, role OID and credential-reference ownership |
| AccessRecoveryAudit | Explicit operator recovery decisions |
| DatabaseOperation / DatabaseRecoveryAudit | Database creation intent, receipt and recovery |
| TenantDatabaseTarget | Persisted tenant database routing |
| DatabaseServerProfile | Verified operator-managed server availability |

Evidence is view-only in admin; server profiles have explicit management controls.
Use the tenant's access-status page for inspection/recovery. Passwords are never
displayed there. A completed operation records prior success, not continuous
health: a broken secret can leave it completed while authentication fails.
Structural role inspection alone does not establish ownership or password validity.

`atomic_tenant` obtains credentials in shared context and opens a fresh scoped
connection, changing only its user/password. Trusted migration/seed code may use
`privileged=True`; request handlers must not. Tenant roles must not access the
registry, journals or secret store. Review external PUBLIC/role/function grants:
inspection is not a complete PostgreSQL security audit.

## Provider contract

`CREDENTIAL_PROVIDER` is a zero-argument factory returning the store. Keep
construction lazy. `put` must not overwrite an existing reference; `get` must
report failures; recovery uses `absent(reference)` only when it returns explicit,
authoritative `True`. Denied access, timeouts and service failures are not absence.
Changing providers does not migrate existing passwords or references.

Local POSIX development can use:

```python
PARTITIONDB.update(
    CREDENTIAL_PROVIDER="partitiondb.adapters.django.databases.credentials.development_credentials",
    DEVELOPMENT_CREDENTIALS_DIR=BASE_DIR / ".secrets",
)
```

This provider uses private files and hashed names. It is not an encrypted vault;
keep its trusted parent directory and preserve secrets for existing tenants.

## Self-hosted OpenBao KV v2

Run OpenBao as an external service within the customer's network. Configure:

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

Use workload authentication and agent-managed token renewal. The token file must
be a private regular non-symlink file owned by the application UID (0600 on POSIX).
TLS verification is required. The client disables environment proxies/redirects,
hashes references and uses CAS=0 writes. It pins the KV v2 mount accessor; the
internal mount-preflight API must be accepted against the deployed server version.

The normal provisioning policy is:

```hcl
path "secret/data/partitiondb/*" {
  capabilities = ["create", "update", "read"]
}
path "secret/metadata/partitiondb/*" {
  capabilities = ["read"]
}
```

Enable mount-level `cas_required`. Separate runtime read-only identity where the
application composition permits. Metadata absence is checked conservatively:
soft-deleted/destroyed versions still block absence-based recovery. Explicit
provider `delete` permanently removes **all versions and metadata** and requires
metadata delete permission; ordinary provisioning does not invoke it on failure.

The library does not operate production OpenBao, unsealing, HA, workload identity,
backup/restore, leased roles or coordinated password rotation. Use the
[local stack](LOCAL_INFRASTRUCTURE.md) for a runnable integration and
[operations](OPERATIONS.md) for recovery and grant refresh.
