from django.db import connections


def provisioning_connection_params(alias="default"):
    """Build psycopg parameters from Django's PostgreSQL configuration."""
    params = connections[alias].get_connection_params().copy()

    # Django supplies its own cursor factory; standalone psycopg uses its default.
    params.pop("cursor_factory", None)

    return params