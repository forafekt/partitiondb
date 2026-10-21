# Django durable evidence checkpoint

This checkpoint adds optional abstract Django control-plane models and persistence
adapters for role ownership receipts and recovery audit events. Applications
must define concrete subclasses, generate migrations, and restrict write access
at the PostgreSQL level. Keep records outside tenant schemas.

`DjangoOwnershipReceiptStore.record` is deliberately insert-only. It does not
manufacture ownership evidence from an existing PostgreSQL role. A trusted
provisioning operation must record the actual role OID and its operation ID.

`DjangoRecoveryAuditSink.record` is idempotent for a caller-supplied operation
ID and rejects conflicting retries. It is not automatically attached to the
recovery service: that service's current audit signature lacks an operation ID
and actor identity. Do not use random operation IDs on each retry.

**Not production-complete:** A durable pre-DDL operation intent, automatic
receipt creation, credential verification wiring, atomic audit/checkpoint
coordination, and Admin status/preview still require implementation. A crash
between role creation and receipt insertion remains ambiguous and must not be
silently recovered. These models are not tamper-proof without database ACLs.

Future dedicated tenant databases should use database target identity rather
than schema alone; `database_alias` is retained in these records.
