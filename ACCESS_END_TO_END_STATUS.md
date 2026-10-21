# Restricted-access integration status

This checkpoint adds failure injection for every coordinator side-effect
boundary and read-only lookup of durable Django operation intents.

It does NOT prove crash-safe automatic replay. PostgreSQL DDL, secret-provider
writes, and Django journal writes do not share a transaction. In particular,
a crash between role creation and ownership-receipt persistence is ambiguous
and must not result in automatic role adoption.

Before production release:
- Bind operation IDs to a trusted, durable, independent ownership proof before
  any external side effects; verify PostgreSQL role OID and credentials.
- Wire the concrete Django operation, receipt, and audit models into a trusted
  shared database with application migrations.
- Implement explicit operator review for ambiguous states, with audited,
  idempotent recovery and lock coverage.
- Run real PostgreSQL integration tests including process interruption,
  privilege denial, role replacement, and credential rotation.
- Verify Django Admin permission checks and the application integration.
- Preserve the reserved isolated-database policy without enabling it.

The default shared-role path remains unchanged.
