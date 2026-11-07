"""Isolated SQLite control-plane acceptance; deliberately never inspects target databases."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings

settings.configure(
    SECRET_KEY="overview-test-only",
    ROOT_URLCONF="tests.recovery_admin_urls",
    ALLOWED_HOSTS=["testserver"],
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django.contrib.admin",
        "partitiondb.adapters.django.apps.PartitionDBConfig",
        "partitiondb.adapters.django.tenancy",
        "partitiondb.adapters.django.infrastructure",
        "partitiondb.adapters.django.database_access",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ]
            },
        }
    ],
    STATIC_URL="/static/",
    PARTITIONDB={
        "SHARED_APPS": [
            "auth",
            "contenttypes",
            "sessions",
            "messages",
            "staticfiles",
            "admin",
            "partitiondb_tenancy",
            "partitiondb_infrastructure",
            "partitiondb_database_access",
        ],
        "TENANT_APPS": [],
    },
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
)
import django  # noqa: E402

django.setup()
from django.contrib import admin  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Permission  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import DatabaseError, IntegrityError, connection, transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from partitiondb import Tenant as Identity  # noqa: E402
from partitiondb import tenant_scope  # noqa: E402
from partitiondb.adapters.django.admin import SharedAdminSite  # noqa: E402
from partitiondb.adapters.django.administration.overview import TenantOverviewService  # noqa: E402
from partitiondb.adapters.django.database_access.models import RoleProvisioningOperation  # noqa: E402
from partitiondb.adapters.django.infrastructure.models import (  # noqa: E402
    DatabaseRecoveryEvent,
    DatabaseReservation,
)
from partitiondb.adapters.django.tenancy.lifecycle import set_primary_domain  # noqa: E402
from partitiondb.adapters.django.tenancy.models import Domain, Tenant  # noqa: E402
from partitiondb.adapters.django.tenancy.operator import run_tenant_action  # noqa: E402


class TenantOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        call_command("migrate", verbosity=0, skip_checks=True)
        cls.operator = get_user_model().objects.create_superuser("operator", password="test-only")
        cls.reader = get_user_model().objects.create_user("reader", is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(codename="view_tenant"))
        cls.tenants = Tenant.objects.bulk_create(
            [
                Tenant(
                    name=f"Tenant {i}",
                    schema_name=f"overview_{i}",
                    credential_ref="operator-only-ref",
                    access_policy="restricted",
                )
                for i in range(10)
            ]
        )
        Domain.objects.bulk_create(
            [Domain(tenant=row, hostname=f"tenant-{row.pk}.example", is_primary=True) for row in cls.tenants]
        )
        cls.model_admin = admin.site._registry[Tenant]

    def request(self, method="get", data=None, user=None):
        request = getattr(RequestFactory(), method)("/admin/overview/", data or {})
        request.user = user or self.operator
        return request

    def test_overview_has_distinct_states_without_opening_privileged_connections(self):
        with patch(
            "partitiondb.adapters.django.tenancy.workflow.TenantWorkflow.open_connection",
            side_effect=AssertionError("GET opened a privileged connection"),
        ):
            response = self.model_admin.overview_view(self.request(), str(self.tenants[0].pk))
            response.render()
        html = response.content.decode()
        self.assertIn("Admission enabled", html)
        self.assertIn("Lifecycle checkpoint", html)
        self.assertIn("Unavailable", html)  # SQLite has no PostgreSQL checkpoint store.
        self.assertIn("Role provisioning", html)
        self.assertIn("operator-only-ref", html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertNotIn("Healthy", html)

    def test_metadata_reader_cannot_see_operator_sections_or_mutate(self):
        request = self.request(user=self.reader)
        response = self.model_admin.overview_view(request, str(self.tenants[0].pk))
        response.render()
        html = response.content.decode()
        for private in ("operator-only-ref", "PostgreSQL role", "Operator actions", "Memberships"):
            self.assertNotIn(private, html)
        self.assertNotIn("credential_ref", self.model_admin.get_fields(request, self.tenants[0]))
        self.assertNotIn("access_role", self.model_admin.get_fields(request, self.tenants[0]))
        with self.assertRaises(PermissionDenied):
            self.model_admin.lifecycle_view(self.request("post", user=self.reader), str(self.tenants[0].pk))

    def test_application_fieldsets_cannot_expose_private_fields_to_metadata_viewers(self):
        request = self.request(user=self.reader)
        fieldsets = (("Identity", {"fields": (("name", "credential_ref"), ("schema_name", "access_role"))}),)
        with patch.object(self.model_admin, "fieldsets", fieldsets):
            actual = self.model_admin.get_fieldsets(request, self.tenants[0])
        self.assertEqual(actual[0][1]["fields"], [("name",), ("schema_name",)])

    def test_direct_lifecycle_url_requires_post_and_exact_identity(self):
        row = self.tenants[0]
        with patch("partitiondb.adapters.django.administration.dashboard.run_tenant_action") as action:
            self.assertEqual(self.model_admin.lifecycle_view(self.request(), str(row.pk)).status_code, 405)
            with self.assertRaises(PermissionDenied):
                self.model_admin.lifecycle_view(self.request("post", {"action": "disable"}), str(row.pk))
            action.assert_not_called()

    def test_disable_action_never_acquires_target_or_credentials(self):
        workflow = MagicMock()
        identity = Identity(str(self.tenants[0].pk), self.tenants[0].schema_name)
        run_tenant_action(self.request("post"), workflow, identity, "disable")
        workflow.access_context.assert_not_called()
        workflow.registry_lifecycle.return_value.__enter__.return_value.set_active.assert_called_once_with(
            identity, active=False
        )
        with self.assertRaises(PermissionDenied):
            run_tenant_action(self.request(user=self.reader), workflow, identity, "disable")

    def test_expected_admission_blocker_is_displayed_without_logging_success(self):
        from partitiondb.adapters.django.tenancy.lifecycle import AdmissionBlocked

        row = self.tenants[0]
        request = self.request(
            "post",
            {
                "action": "enable",
                "confirm_tenant_id": str(row.pk),
                "confirm_schema": row.schema_name,
                "confirm_database": row.database_alias,
                "confirm_type": "",
            },
        )
        reason = "Role provisioning is incomplete. Inspect role access and recovery before enabling admission."
        with (
            patch(
                "partitiondb.adapters.django.administration.dashboard.run_tenant_action",
                side_effect=AdmissionBlocked(reason),
            ),
            patch.object(self.model_admin, "message_user") as message,
            patch.object(self.model_admin, "log_change") as audit,
        ):
            response = self.model_admin.lifecycle_view(request, str(row.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(message.call_args.args[1], reason)
        audit.assert_not_called()

    def test_batch_query_count_does_not_grow_with_tenant_count(self):
        service = TenantOverviewService()
        with CaptureQueriesContext(connection) as one:
            service.summaries(self.tenants[:1])
        with CaptureQueriesContext(connection) as many:
            values = service.summaries(self.tenants)
        self.assertEqual(len(one), len(many))
        self.assertLessEqual(len(many), 3)
        self.assertEqual(len(values), 10)
        self.assertTrue(all(value.hostname.endswith(".example") for value in values.values()))

    def test_stale_identity_and_missing_evidence_are_different(self):
        row = self.tenants[1]
        operation = RoleProvisioningOperation.objects.create(
            tenant_id=str(row.pk),
            operation_id="stale-overview",
            database_alias="default",
            schema_name="another_schema",
            role_name="pdb_another",
            credential_ref="ref",
            state="pending",
        )
        try:
            summaries = TenantOverviewService().summaries(self.tenants[:2])
            self.assertEqual(summaries[str(row.pk)].role_state, "Mismatch")
            self.assertEqual(summaries[str(self.tenants[0].pk)].role_state, "Missing")
        finally:
            operation.delete()

    def test_unavailable_evidence_does_not_claim_absence(self):
        service = TenantOverviewService()
        original = service._rows

        def rows(model, **filters):
            if model is RoleProvisioningOperation:
                raise DatabaseError("unavailable")
            return original(model, **filters)

        with patch.object(service, "_rows", side_effect=rows):
            summary = service.summaries(self.tenants[:1])[str(self.tenants[0].pk)]
        self.assertEqual(summary.role_state, "Unavailable")

    def test_tenant_scope_cannot_read_operator_summaries(self):
        with tenant_scope(Identity("other", "other_schema")), self.assertRaises(ValueError):
            TenantOverviewService().summaries(self.tenants[:1])

    def test_primary_domain_switch_preserves_single_primary(self):
        row = self.tenants[2]
        alternate = Domain.objects.create(tenant=row, hostname="alternate.example")
        result = set_primary_domain(alternate.pk)
        self.assertTrue(result.is_primary)
        self.assertEqual(Domain.objects.get(tenant=row, is_primary=True).pk, alternate.pk)

    def test_native_groups_and_evidence_state_constraints(self):
        site = SharedAdminSite(name="overview_order")
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label.startswith("partitiondb_"):
                site.register(model, type(model_admin))
        groups = site.get_app_list(self.request())
        self.assertEqual(
            [group["app_label"] for group in groups],
            [
                "partitiondb_tenancy",
                "partitiondb_infrastructure",
                "partitiondb_database_access",
            ],
        )
        self.assertEqual(groups[1]["models"][0]["object_name"], "DatabaseServer")
        DatabaseRecoveryEvent.objects.create(
            operation_id="cleanup-event", tenant_id="1", actor_id="operator", decision="cleanup"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DatabaseRecoveryEvent.objects.create(
                operation_id="invalid-event", tenant_id="1", actor_id="operator", decision="invented"
            )

    def test_managed_route_reports_missing_and_wrong_owner_separately(self):
        row = SimpleNamespace(
            pk="route-owner", database_alias="pdb_db_overview", schema_name="route_schema", get_tenant_type=lambda: None
        )
        service = TenantOverviewService()
        summary = service.summaries([row], include_operations=False, include_domains=False)[row.pk]
        self.assertEqual(summary.placement, "Missing")
        reservation = DatabaseReservation.objects.create(
            alias=row.database_alias,
            profile="default",
            database_name="overview_database",
            profile_fingerprint="wrong-fingerprint",
            tenant_id="another-owner",
        )
        try:
            summary = service.summaries([row], include_operations=False, include_domains=False)[row.pk]
            self.assertEqual(summary.placement, "Mismatch")
        finally:
            reservation.delete()

    def test_ambiguous_resume_is_rejected_before_provisioning(self):
        from partitiondb.provisioning import Stage

        identity = Identity("resume-id", "resume_schema")
        workflow = MagicMock()
        context = workflow.access_context.return_value.__enter__.return_value
        context.tenant = identity
        context.provisioner.store.get.return_value = SimpleNamespace(tenant=identity, stage=Stage.CREATING)
        with self.assertRaises(ValueError):
            run_tenant_action(self.request("post"), workflow, identity, "resume")
        context.provisioner.run.assert_not_called()
        workflow.registry_lifecycle.assert_not_called()

    def test_canonical_string_ids_support_custom_registry_models(self):
        row = SimpleNamespace(
            pk="external:id", database_alias="default", schema_name="external_schema", get_tenant_type=lambda: None
        )
        service = TenantOverviewService()
        result = service.summaries([row], include_operations=False, include_domains=False)
        self.assertEqual(list(result), ["external:id"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(TenantOverviewTests)
    )
    raise SystemExit(not result.wasSuccessful())
