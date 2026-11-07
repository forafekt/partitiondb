"""Separate storage modes preserve validation and use the configured admin UI."""

from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.contrib.admin import site as shared_admin_site
from django.contrib.admin.widgets import AdminTextInputWidget
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from partitiondb.adapters.django.administration.forms import style_provisioning_form
from partitiondb.adapters.django.databases.routes import NEW_DATABASE
from partitiondb.adapters.django.registry.forms import DatabaseTenantProvisionForm, SchemaTenantProvisionForm
from partitiondb.adapters.django.registry_app.models import Tenant


@override_settings(ROOT_URLCONF="tests.recovery_admin_urls")
class TenantCreationTests(SimpleTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        module = "partitiondb.adapters.django.registry.forms"
        for name, value in (
            ("database_profiles", {"control": Mock()}),
            ("tenant_database_aliases", ["default", "dedicated"]),
            ("database_provisioning_targets", {}),
        ):
            self.stack.enter_context(patch(module + "." + name, return_value=value))
        model = Mock()
        model._base_manager.using.return_value.filter.return_value.exists.return_value = False
        self.stack.enter_context(patch(module + ".get_tenant_model", return_value=model))
        self.stack.enter_context(patch(module + ".get_domain_model", return_value=model))
        self.data = {
            "name": "Example",
            "schema": "org_example",
            "hostname": "example.localhost",
            "confirm": "on",
            "database_alias": "default",
            "access_policy": "shared",
            "database_profile": "control",
        }
        self.admin = shared_admin_site._registry[Tenant]
        self.request = RequestFactory().get("/admin/partitiondb_registry/tenant/add/")
        self.request.user = get_user_model()(pk=1, is_superuser=True, is_staff=True, is_active=True)

    def test_schema_form_excludes_new_database_inputs_and_rejects_forged_mode(self):
        form = SchemaTenantProvisionForm(data=self.data)
        self.assertNotIn("database_profile", form.fields)
        self.assertNotIn("database_name", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        forged = SchemaTenantProvisionForm(data={**self.data, "database_alias": NEW_DATABASE})
        self.assertFalse(forged.is_valid())

    def test_database_form_enforces_new_target_and_restricted_access(self):
        form = DatabaseTenantProvisionForm(data=self.data)
        self.assertTrue(form.fields["database_alias"].widget.is_hidden)
        self.assertTrue(form.fields["access_policy"].widget.is_hidden)
        self.assertTrue(form.is_valid(), form.errors)
        options = form.creation_options(form.cleaned_data)
        self.assertTrue(options["new_database"])
        self.assertEqual(options["access_policy"], "restricted")
        self.assertEqual(options["database_profile"], "control")
        self.assertNotIn("database_profile", options["tenant_data"])

    def test_database_server_and_name_are_validated(self):
        for data in (
            {**self.data, "database_profile": "untrusted"},
            {**self.data, "database_name": "INVALID DB"},
        ):
            form = DatabaseTenantProvisionForm(data=data)
            self.assertFalse(form.is_valid())

    def test_admin_widgets_and_urls_are_specific_to_each_flow(self):
        for kind, expected in (
            ("schema", SchemaTenantProvisionForm),
            ("database", DatabaseTenantProvisionForm),
        ):
            self.request.partitiondb_creation_kind = kind
            form = self.admin.get_provisioning_form_class(self.request)()
            style_provisioning_form(self.admin, self.request, form)
            self.assertIsInstance(form, expected)
            self.assertIsInstance(form.fields["name"].widget, AdminTextInputWidget)
            url = reverse(f"{shared_admin_site.name}:partitiondb_registry_tenant_add_{kind}")
            self.assertEqual(resolve(url).url_name, f"partitiondb_registry_tenant_add_{kind}")

    def test_add_page_is_a_choice_not_an_ambiguous_combined_form(self):
        with patch.object(shared_admin_site, "each_context", return_value={}):
            response = self.admin.add_view(self.request)
        self.assertEqual(response.template_name, "partitiondb/admin/provision_choose.html")
        self.assertIn("schema_url", response.context_data)
        self.assertIn("database_url", response.context_data)
