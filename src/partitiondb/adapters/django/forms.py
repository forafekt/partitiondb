from django import forms
from django.db import connections

from partitiondb.core import validate_schema
from partitiondb.domains import normalize_hostname

from .model_registry import get_domain_model, get_tenant_model


class TenantProvisionForm(forms.Form):
    database_alias = forms.ChoiceField(
        required=False,
        initial="default",
        label="Database",
        help_text="Dedicated targets must be preconfigured and can belong to only one tenant.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["database_alias"].choices = [
            (alias, "Shared database" if alias == "default" else f"Dedicated database ({alias})")
            for alias in connections
        ]

    def clean_database_alias(self):
        alias = self.cleaned_data["database_alias"] or "default"
        if (
            alias != "default"
            and get_tenant_model()._base_manager.using("default").filter(database_alias=alias).exists()
        ):
            raise forms.ValidationError("This dedicated database is already reserved.")
        return alias

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("database_alias", "default") != "default" and cleaned.get("access_policy") != "restricted":
            self.add_error("access_policy", "Choose a dedicated PostgreSQL role for a dedicated database.")
        return cleaned

    access_policy = forms.ChoiceField(
        choices=[("shared", "Shared runtime role"), ("restricted", "Dedicated PostgreSQL role")],
        initial="shared",
        help_text="Dedicated roles are provisioned, verified and used for this tenant's requests.",
    )
    name = forms.CharField(
        max_length=200,
        label="Tenant name",
    )
    schema = forms.CharField(
        max_length=63,
        label="Schema name",
        help_text="For example: org_acme. Cannot be changed afterward.",
    )
    hostname = forms.CharField(
        max_length=253,
        help_text="For example: acme.localhost. Do not include a scheme or port.",
    )
    confirm = forms.BooleanField(
        label=("Create the schema, apply migrations, run tenant setup and activate this tenant."),
    )

    def clean_schema(self):
        schema = self.cleaned_data["schema"]

        try:
            validate_schema(schema)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        if (
            get_tenant_model()
            ._base_manager.using("default")
            .filter(
                database_alias=self.cleaned_data.get("database_alias", "default"),
                schema_name=schema,
            )
            .exists()
        ):
            raise forms.ValidationError(
                "This schema is already registered. Inspect the existing "
                "tenant instead of submitting another creation request."
            )

        return schema

    def clean_hostname(self):
        try:
            hostname = normalize_hostname(self.cleaned_data["hostname"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        if get_domain_model()._base_manager.using("default").filter(hostname__iexact=hostname).exists():
            raise forms.ValidationError("This hostname is already registered.")

        return hostname

    @classmethod
    def creation_options(cls, cleaned_data):
        """Subclass fields are application model values; base fields remain explicit."""
        return {
            "name": cleaned_data["name"],
            "schema": cleaned_data["schema"],
            "hostname": cleaned_data["hostname"],
            "database": cleaned_data["database_alias"],
            "access_policy": cleaned_data["access_policy"],
            "tenant_data": {
                key: value for key, value in cleaned_data.items() if key not in TenantProvisionForm.base_fields
            },
        }
