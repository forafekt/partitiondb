"""Repair interrupted first initialization of the LOCAL control database.

Only the configured control database is created. Existing databases are never
modified or dropped. Tenant databases remain owned by the library workflow.
"""

import os

import psycopg
from psycopg import sql

name = os.environ["POSTGRES_DB"]
with psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=os.environ["POSTGRES_PORT"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    dbname="postgres",
    sslmode="verify-full",
    sslrootcert="/ca/ca.pem",
    connect_timeout=5,
    autocommit=True,
) as connection:
    # Session lock serializes concurrent local initialization invocations.
    connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("partitiondb:local-control:" + name,))
    try:
        existing = connection.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if existing is None:
            connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            print("Created the missing local control database")
        elif existing[0] != os.environ["POSTGRES_USER"]:
            raise RuntimeError("Existing local control database has an unexpected owner")
        else:
            print("Existing local control database retained")
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", ("partitiondb:local-control:" + name,))
