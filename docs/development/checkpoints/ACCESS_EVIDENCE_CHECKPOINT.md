# Restricted-role recovery evidence checkpoint

This checkpoint adds `RoleOwnershipReceipt`, `PostgresRecoveryEvidenceProvider`,
and `PsycopgCredentialAuthentication`. PostgreSQL inspection now exposes
`role_oid` so role drop/recreation cannot silently satisfy ownership evidence.

## Integration requirements

- Store receipts durably in a trusted administrative store, inaccessible to
  tenant application roles. Record the actual role OID and a unique operation
  identifier after provisioning. The receipt is independent of mutable
  `TenantAccessMixin` role-binding metadata.
- Do not generate ownership receipts retroactively from role names. A crash
  before the receipt is committed remains ambiguous and requires manual review.
- Configure a trusted database-target resolver for fresh password authentication.
  Do not derive the DSN from untrusted tenant metadata.
- The generic secret provider does not prove absence. `absent()` fails closed;
  implement an explicit trusted cleanup/evidence process for RETRY decisions.
- This checkpoint does **not** yet provide the concrete durable Django receipt
  store, atomic/idempotent audit sink, or an Admin inspection preview. It does
  not automatically enable recovery in production.
- PostgreSQL role OID is stable for the role's lifetime but is not a
  cryptographic attestation; store permissions and operation provenance remain
  part of the trust boundary.
- Dedicated tenant databases remain a future backend policy.
