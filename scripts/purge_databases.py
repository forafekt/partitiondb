"""Explicit destructive development reset. Preview by default; stop app/workers first.

DSNs are read from PDB_RESET_DSNS (or PDB_RESET_DSN), never from arguments.
Use one DSN per line, or comma-separated PostgreSQL URIs. The same explicit
database/schema selections apply to every connection. All targets are validated
before deletion starts. --purge-roles also removes custom roles, preserving
postgres, partitiondb, connected users and --keep-role names. Role ownership
and grants are cleaned only in supplied databases. Secrets/uploads remain.
"""

import argparse
import os
import re
from contextlib import ExitStack

DEFAULT_DSNS = ",".join(
    [
        "postgresql://partitiondb:local-database-only@localhost:55433/partitiondb",
        "postgresql://partitiondb:local-database-only@localhost:55434/postgres",
    ]
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", default=[], help="Exact database to drop (repeatable)")
    parser.add_argument("--schema", action="append", default=[], help="Exact schema to drop CASCADE (repeatable)")
    parser.add_argument(
        "--purge-roles",
        action="store_true",
        help="Remove custom roles after reassigning ownership and removing grants in connected databases",
    )
    parser.add_argument("--keep-role", action="append", default=[], help="Additional role to preserve (repeatable)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", help="Required for execution: DELETE DEVELOPMENT DATA")
    args = parser.parse_args()
    databases = sorted(set(args.database))
    schemas = sorted(set(args.schema))
    if not databases and not schemas and not args.purge_roles:
        parser.error("Specify --database, --schema or --purge-roles")
    if any(not name or name in {"postgres", "template0", "template1"} for name in databases):
        parser.error("System/maintenance database names are protected")
    if any(not name or name.startswith("pg_") or name == "information_schema" for name in schemas):
        parser.error("System schemas are protected")
    if args.apply and args.confirm != "DELETE DEVELOPMENT DATA":
        parser.error('--apply requires --confirm "DELETE DEVELOPMENT DATA"')
    import psycopg
    from psycopg import sql

    raw = os.environ.get("PDB_RESET_DSNS") or os.environ.get("PDB_RESET_DSN") or DEFAULT_DSNS
    # Split URI boundaries rather than every comma: libpq supports multihost DSNs.
    dsns = list(
        dict.fromkeys(part.strip() for part in re.split(r"\r?\n|,\s*(?=postgres(?:ql)?://)", raw) if part.strip())
    )
    if not dsns:
        parser.error("Set PDB_RESET_DSNS (or PDB_RESET_DSN) to explicit development connections")
    with ExitStack() as stack:
        connections = []
        protected_roles = {"postgres", "partitiondb", *args.keep_role}
        for index, dsn in enumerate(dsns, 1):
            try:
                connection = stack.enter_context(psycopg.connect(dsn, autocommit=True))
            except psycopg.Error:
                # Connection exceptions can contain credentials; do not print them.
                parser.error(f"Connection {index} failed; check its DSN and server availability. Nothing was changed")
            current = connection.execute(
                "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
            ).fetchone()
            print(f"Connection {index}: database={current[0]!r}, server={current[1]!r}, port={current[2]!r}")
            if current[0] in databases:
                parser.error(f"Connection {index}: cannot drop the connected database; use a maintenance database")
            existing_databases = {row[0] for row in connection.execute("SELECT datname FROM pg_database")}
            existing_schemas = {row[0] for row in connection.execute("SELECT nspname FROM pg_namespace")}
            missing_databases = sorted(set(databases) - existing_databases)
            missing_schemas = sorted(set(schemas) - existing_schemas)
            if missing_databases or missing_schemas:
                if missing_databases:
                    print(f"  Missing databases: {missing_databases!r}")
                    print(f"  Available databases: {sorted(existing_databases)!r}")
                if missing_schemas:
                    print(f"  Missing schemas in {current[0]!r}: {missing_schemas!r}")
                    available = sorted(
                        name for name in existing_schemas if not name.startswith("pg_") and name != "information_schema"
                    )
                    print(f"  Available user schemas: {available!r}")
                parser.error(f"Connection {index}: requested targets are missing; nothing was changed")
            for name in schemas:
                print(f"  DROP SCHEMA {name!r} CASCADE in {current[0]!r}")
            for name in databases:
                print(f"  DROP DATABASE {name!r} (fails if sessions remain)")
            identities = connection.execute("SELECT current_user, session_user").fetchone()
            protected_roles.update(identities)
            connections.append(connection)
        role_plans = []
        for index, connection in enumerate(connections, 1):
            roles = []
            if args.purge_roles:
                roles = [
                    (name, oid)
                    for name, oid in connection.execute("SELECT rolname, oid FROM pg_roles ORDER BY rolname")
                    if not name.startswith("pg_") and name not in protected_roles
                ]
                print(f"Connection {index}: preserve roles {sorted(protected_roles)!r}")
                for name, _ in roles:
                    print(f"  Reassign ownership to current_user, remove grants here, then DROP ROLE {name!r}")
            role_plans.append(roles)
        if not args.apply:
            print('Preview only. Use --apply --confirm "DELETE DEVELOPMENT DATA" to execute.')
            return
        # Each server commits independently; a later failure cannot undo earlier drops.
        for index, connection in enumerate(connections, 1):
            for name in schemas:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
                # if public schema, recreate it
                if name == "public":
                    connection.execute(sql.SQL("CREATE SCHEMA public"))
            for name in databases:
                connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            print(f"Connection {index}: selected targets removed.")
        # Clean every supplied database before attempting cluster-wide role deletion.
        for connection, roles in zip(connections, role_plans, strict=True):
            with connection.transaction():
                for name, oid in roles:
                    actual = connection.execute("SELECT oid FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
                    if actual != (oid,):
                        raise RuntimeError(f"Role {name!r} changed since preview; stopping")
                    connection.execute(sql.SQL("REASSIGN OWNED BY {} TO CURRENT_USER").format(sql.Identifier(name)))
                    connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
        for index, (connection, roles) in enumerate(zip(connections, role_plans, strict=True), 1):
            for name, oid in roles:
                actual = connection.execute("SELECT oid FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
                if actual is None:  # Another supplied connection to this cluster already removed it.
                    continue
                if actual != (oid,):
                    raise RuntimeError(f"Role {name!r} changed since preview; stopping")
                try:
                    connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))
                except psycopg.errors.DependentObjectsStillExist:
                    parser.error(
                        f"Connection {index}: role {name!r} still has dependencies in another database. "
                        "Include that database in PDB_RESET_DSNS for role-only cleanup and retry. "
                        "Earlier changes have already committed."
                    )
                print(f"Connection {index}: removed role {name!r}")
        print("Reset complete. Requested public schemas were recreated; secrets and uploads were not removed.")


if __name__ == "__main__":
    main()
