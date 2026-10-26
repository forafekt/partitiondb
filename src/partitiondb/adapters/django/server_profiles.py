"""Explicit verification of operator-controlled PostgreSQL server registrations."""

import psycopg
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from partitiondb.postgres_database import cluster_id

from .access_boundary import require_control_plane
from .configuration import get_config
from .database_routes import database_profiles


def verify_server_profile(profile):
    """Verify connectivity and pin cluster identity; never creates infrastructure."""
    require_control_plane()
    from .access_control.models import DatabaseServerProfile

    with transaction.atomic(using="default"):
        row = DatabaseServerProfile._base_manager.using("default").select_for_update().get(pk=profile.pk)
        source = database_profiles(include_registered=False).get(row.source_profile)
        if source is None:
            raise ValidationError("Select an operator-configured source profile")
        params = dict(get_config().callback("PROVISIONING_CONNECTION_PARAMS", required=True)(source.source_alias))
        params.update(dbname=source.maintenance_database, connect_timeout=5)
        try:
            with psycopg.connect(**params, autocommit=True) as connection:
                identity = cluster_id(connection)
                allowed = connection.execute(
                    "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"
                ).fetchone()[0]
                if not allowed:
                    raise ValidationError("Provisioning credentials require database creation privileges")
        except psycopg.Error:
            raise ValidationError(
                "Server verification failed; check connectivity, TLS and provisioning credentials"
            ) from None
        if row.cluster_id and row.cluster_id != identity:
            raise ValidationError("PostgreSQL cluster identity changed; register a separate server")
        row.cluster_id = identity
        row.source_fingerprint = source.fingerprint
        row.verified_at = timezone.now()
        row.save(using="default", update_fields=["cluster_id", "source_fingerprint", "verified_at"])
    return row
