"""Recovery links, evidence rendering and existing POST protections."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin import site as shared_admin_site
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from partitiondb.adapters.django.administration.managed import ManagedTenantAdmin as TenantAdmin
from partitiondb.adapters.django.database_access.admin import RoleProvisioningOperationAdmin, RoleRecoveryEventAdmin
from partitiondb.adapters.django.database_access.models import RoleProvisioningOperation, RoleRecoveryEvent
from partitiondb.adapters.django.tenancy.models import Tenant


@override_settings(
    ROOT_URLCONF="tests.recovery_admin_urls",
    STORAGES={
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class RecoveryAdminTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        self.tenant_admin = shared_admin_site._registry[Tenant]
        self.tenant = Tenant(pk=3, name="Demo", schema_name="org_demo3", database_alias="default")
        self.tenant._state.adding = False

    def test_admin_registration_and_recovery_links(self):
        for model, expected, obj in (
            (Tenant, TenantAdmin, self.tenant),
            (RoleProvisioningOperation, RoleProvisioningOperationAdmin, RoleProvisioningOperation(tenant_id="3")),
            (
                RoleRecoveryEvent,
                RoleRecoveryEventAdmin,
                RoleRecoveryEvent(tenant_id="3"),
            ),
        ):
            with self.subTest(model=model):
                model_admin = shared_admin_site._registry[model]
                self.assertIsInstance(model_admin, expected)
                self.assertEqual(model_admin.check(), [])
                if model is Tenant:
                    self.assertIn("tenant_overview", model_admin.get_list_display(self.request))
                else:
                    self.assertIn("tenant_recovery", model_admin.list_display)
                url = reverse(
                    f"{shared_admin_site.name}:partitiondb_tenancy_tenant_role_status",
                    args=[3],
                )
                self.assertIn(url, model_admin.tenant_recovery(obj))

    def test_evidence_remains_immutable_and_superuser_only(self):
        for model in (RoleProvisioningOperation, RoleRecoveryEvent):
            model_admin = shared_admin_site._registry[model]
            self.assertFalse(model_admin.has_add_permission(self.request))
            self.assertFalse(model_admin.has_change_permission(self.request))
            self.assertFalse(model_admin.has_delete_permission(self.request))
            self.assertTrue(model_admin.has_view_permission(self.request))
            self.request.user.is_superuser = False
            self.assertFalse(model_admin.has_view_permission(self.request))
            self.request.user.is_superuser = True

    def render_status(self, **evidence):
        status = {
            "stage": "access_provisioning",
            "can_reconcile": True,
            "restricted": True,
            "inspection": SimpleNamespace(role="pdb_org_demo3", state="disabled", reason="No login"),
            "evidence": SimpleNamespace(
                ownership_verified=evidence.get("ownership", False),
                credential_verified=evidence.get("credential", False),
                absence_verified=evidence.get("absent", False),
            ),
        }
        from partitiondb.adapters.django.administration.forms import RoleRecoveryForm, confirmation_form

        decisions = []
        if status["evidence"].ownership_verified and status["evidence"].credential_verified:
            decisions.append(("ready", "READY"))
        if status["evidence"].absence_verified:
            decisions.append(("retry", "RETRY"))
        with patch.object(shared_admin_site, "each_context", return_value={}):
            return render_to_string(
                "partitiondb/admin/role_status.html",
                {
                    "original": self.tenant,
                    "status": status,
                    "confirmation_form": confirmation_form(
                        self.tenant_admin, self.request, RoleRecoveryForm, decisions=decisions
                    ),
                    "reconcile_url": "/admin/partitiondb_tenancy/tenant/3/reconcile-role/",
                },
                request=self.request,
            )

    def test_ready_requires_ownership_and_fresh_authentication(self):
        self.assertNotIn('value="ready"', self.render_status(ownership=True))
        html = self.render_status(ownership=True, credential=True)
        self.assertIn('value="ready"', html)
        self.assertNotIn('value="retry"', html)
        self.assertIn('name="confirm_tenant_id"', html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_retry_requires_verified_absence(self):
        self.assertNotIn("<form", self.render_status())
        self.assertIn("Recovery needs investigation", self.render_status())
        html = self.render_status(absent=True)
        self.assertIn('value="retry"', html)
        self.assertNotIn('value="ready"', html)

    def test_endpoint_requires_post_superuser_and_exact_confirmation(self):
        self.assertEqual(self.tenant_admin.reconcile_role_view(self.request, "3").status_code, 405)
        request = RequestFactory().post("/admin/reconcile/", {"confirm_tenant_id": "wrong", "decision": "retry"})
        request.user = self.request.user
        with patch.object(self.tenant_admin, "get_object", return_value=self.tenant):
            with self.assertRaises(SuspiciousOperation):
                self.tenant_admin.reconcile_role_view(request, "3")
            request.user.is_superuser = False
            with self.assertRaises(PermissionDenied):
                self.tenant_admin.reconcile_role_view(request, "3")

    def cleanup_candidate(self):
        from partitiondb.adapters.django.database_access.cleanup import CleanupCandidate

        return CleanupCandidate("3", "org_demo3", "default", "operation-3", "pdb_org_demo3", 123)

    def cleanup_post(self, **changes):
        from dataclasses import asdict

        from django.core import signing

        from partitiondb.adapters.django.administration.recovery import CLEANUP_SALT

        self.request.user.pk = 1
        data = {
            "confirmation_token": signing.dumps(
                {
                    "candidate": asdict(self.cleanup_candidate()),
                    "actor_id": "1",
                },
                salt=CLEANUP_SALT,
            ),
            "confirm_tenant_id": "3",
            "confirm_role": "pdb_org_demo3",
            "confirm_ownership": "yes",
        }
        data.update(changes)
        request = RequestFactory().post("/admin/partitiondb_tenancy/tenant/3/cleanup-role/", data)
        request.user = self.request.user
        return request

    def test_cleanup_get_only_inspects_and_renders_confirmation(self):
        from unittest.mock import MagicMock

        self.request.user.pk = 1
        with (
            patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
            patch.object(shared_admin_site, "each_context", return_value={}),
            patch.object(self.tenant_admin, "get_workflow", return_value=MagicMock()),
            patch(
                "partitiondb.adapters.django.administration.recovery.inspect_cleanup",
                return_value=(self.cleanup_candidate(), None),
            ),
            patch("partitiondb.adapters.django.administration.recovery.cleanup_disabled_role") as cleanup,
        ):
            response = self.tenant_admin.cleanup_role_view(self.request, "3")
            response.render()
        self.assertContains(response, "Confirm cleanup and retry")
        self.assertContains(response, 'name="confirm_ownership"')
        self.assertContains(response, 'name="confirmation_token"')
        cleanup.assert_not_called()

    def test_cleanup_post_requires_fresh_signed_preview_and_exact_confirmation(self):
        for changes in (
            {"confirmation_token": "tampered"},
            {"confirm_tenant_id": "5"},
            {"confirm_role": "other"},
            {"confirm_ownership": ""},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
                patch.object(shared_admin_site, "each_context", return_value={}),
                patch("partitiondb.adapters.django.administration.recovery.cleanup_disabled_role") as cleanup,
            ):
                response = self.tenant_admin.cleanup_role_view(self.cleanup_post(**changes), "3")
                self.assertEqual(response.status_code, 409)
                cleanup.assert_not_called()

    def test_cleanup_post_rejects_expired_or_different_operator_preview(self):
        from django.core import signing

        request = self.cleanup_post()
        with (
            patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
            patch.object(shared_admin_site, "each_context", return_value={}),
            patch("partitiondb.adapters.django.administration.recovery.cleanup_disabled_role") as cleanup,
        ):
            with patch(
                "partitiondb.adapters.django.administration.recovery.signing.loads",
                side_effect=signing.SignatureExpired(),
            ):
                self.assertEqual(self.tenant_admin.cleanup_role_view(request, "3").status_code, 409)
            request.user.pk = 2
            self.assertEqual(self.tenant_admin.cleanup_role_view(request, "3").status_code, 409)
            cleanup.assert_not_called()

    def test_cleanup_post_calls_guarded_cleanup_then_audited_retry(self):
        from unittest.mock import MagicMock

        request = self.cleanup_post(decision="ready")
        events = []
        with (
            patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
            patch.object(shared_admin_site, "each_context", return_value={}),
            patch.object(self.tenant_admin, "get_workflow", return_value=MagicMock()),
            patch(
                "partitiondb.adapters.django.administration.recovery.cleanup_disabled_role",
                side_effect=lambda *args: events.append("cleanup"),
            ),
            patch.object(
                self.tenant_admin,
                "access_reconciliation_service",
                side_effect=lambda request, *args, **kwargs: events.append(request.POST["decision"]),
            ),
            patch.object(self.tenant_admin, "log_change"),
            patch.object(self.tenant_admin, "message_user"),
        ):
            response = self.tenant_admin.cleanup_role_view(request, "3")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(events, ["cleanup", "retry"])

    def test_cleanup_failure_never_reports_success_or_resumes(self):
        from unittest.mock import MagicMock

        from partitiondb.adapters.django.database_access.cleanup import CleanupBlocked

        with (
            patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
            patch.object(shared_admin_site, "each_context", return_value={}),
            patch.object(self.tenant_admin, "get_workflow", return_value=MagicMock()),
            patch(
                "partitiondb.adapters.django.administration.recovery.cleanup_disabled_role",
                side_effect=CleanupBlocked("Role changed"),
            ),
            patch.object(self.tenant_admin, "access_reconciliation_service") as reconcile,
            patch.object(self.tenant_admin, "message_user") as message,
        ):
            response = self.tenant_admin.cleanup_role_view(self.cleanup_post(), "3")
        self.assertEqual(response.status_code, 409)
        reconcile.assert_not_called()
        message.assert_not_called()

    def test_cleanup_url_rejects_post_without_csrf(self):
        from django.urls import resolve

        request = self.cleanup_post()
        callback = resolve("/admin/partitiondb_tenancy/tenant/3/cleanup-role/").func
        with patch("partitiondb.adapters.django.administration.recovery.cleanup_disabled_role") as cleanup:
            response = callback(request, object_id="3")
        self.assertEqual(response.status_code, 403)
        cleanup.assert_not_called()

    def test_cleanup_requires_superuser_before_touching_role(self):
        request = self.cleanup_post()
        request.user.is_superuser = False
        with (
            patch.object(self.tenant_admin, "get_object", return_value=self.tenant),
            patch("partitiondb.adapters.django.administration.recovery.cleanup_disabled_role") as cleanup,
            self.assertRaises(PermissionDenied),
        ):
            self.tenant_admin.cleanup_role_view(request, "3")
        cleanup.assert_not_called()
