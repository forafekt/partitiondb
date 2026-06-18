import re

from django.core.exceptions import ValidationError


# Valid PostgreSQL schema name regex
# Criteria:
#  1. Cannot start with 'pg_'
#  2. Can be any valid character, if quoted
#  3. Must be between 1 and 63 characters long
#
# Reference:
# https://www.postgresql.org/docs/13/sql-createschema.html

PGSQL_VALID_SCHEMA_NAME = re.compile(r'^(?!pg_).{1,63}$', re.IGNORECASE)
MYSQL_VALID_SCHEMA_NAME = re.compile(r'^[a-z0-9_]{1,64}$', re.IGNORECASE)
AZURE_VALID_SCHEMA_NAME = re.compile(r'^[a-z0-9_]{1,63}$', re.IGNORECASE)
MONGO_VALID_SCHEMA_NAME = re.compile(r'^[a-z0-9_]{1,63}$', re.IGNORECASE)
ORACLE_VALID_SCHEMA_NAME = re.compile(r'^[a-z0-9_]{1,30}$', re.IGNORECASE)
REDIS_VALID_SCHEMA_NAME = re.compile(r'^[a-z0-9_]{1,63}$', re.IGNORECASE)

def is_valid_schema_name(name, db_type="postgresql") -> bool:
    if db_type == "postgresql":
        return PGSQL_VALID_SCHEMA_NAME.match(name) is not None
    elif db_type == "mysql":
        return MYSQL_VALID_SCHEMA_NAME.match(name) is not None
    elif db_type == "azure":
        return AZURE_VALID_SCHEMA_NAME.match(name) is not None
    elif db_type == "mongo":
        return MONGO_VALID_SCHEMA_NAME.match(name) is not None
    elif db_type == "oracle":
        return ORACLE_VALID_SCHEMA_NAME.match(name) is not None
    elif db_type == "redis":
        return REDIS_VALID_SCHEMA_NAME.match(name) is not None
    else:
        return False

def check_schema_name(name, db_type="postgresql"):
    if not is_valid_schema_name(name, db_type):
        raise ValidationError(f"[{db_type}]: Invalid string used for the schema name.")
