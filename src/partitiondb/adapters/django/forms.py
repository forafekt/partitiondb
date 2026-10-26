from django import forms

from partitiondb.core import validate_schema
from partitiondb.domains import normalize_hostname

from .database_routes import NEW_DATABASE, database_profiles, tenant_database_aliases, validate_new_database_name
from .database_settings import database_provisioning_targets
from .model_registry import get_domain_model, get_tenant_model


class TenantProvisionForm(forms.Form):
    database_alias = forms.ChoiceField(
        required=False,
        initial="default",
        label="Database",
        help_text="Choose the shared database or a configured dedicated target for this tenant.",
    )

    database_profile = forms.ChoiceField(
        required=False,
        label="Database server",
        help_text="For a new database: choose a trusted server profile. The default uses this application's server.",
    )
    database_name = forms.CharField(
        required=False,
        max_length=63,
        label="New database name",
        help_text=(
            "For a new database only. Leave blank to generate a unique name; "
            "otherwise use lowercase letters, numbers and underscores."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        automatic = database_provisioning_targets()
        profiles = database_profiles()
        self.fields["database_alias"].choices = [
            (alias, self.database_label(alias, automatic=alias in automatic)) for alias in tenant_database_aliases()
        ]
        if profiles:
            self.fields["database_alias"].choices = [
                *self.fields["database_alias"].choices,
                (NEW_DATABASE, "New database + tenant schema"),
            ]
        self.fields["database_profile"].choices = [(name, name) for name in profiles]
        self.fields["database_profile"].initial = "default" if "default" in profiles else next(iter(profiles), None)
        self.fields["database_alias"].help_text = (
            "Choose New database + tenant schema to create a database on submission. "
            "You can also use the shared database or a target already configured by the administrator."
            if profiles
            else "Use the shared database or a configured target. \
                No server profiles are enabled for creating new databases."
        )
        if not profiles:
            self.fields["database_profile"].widget = forms.HiddenInput()
            self.fields["database_name"].widget = forms.HiddenInput()
        self.fields["access_policy"].help_text = "Dedicated databases always use a dedicated PostgreSQL role. \
            This choice applies only to schemas in the shared database."

    @staticmethod
    def database_label(alias, *, automatic):
        if alias == "default":
            return "Shared database (tenant schema)"
        return f"Create database ({alias})" if automatic else f"Existing database ({alias})"

    def clean_database_alias(self):
        alias = self.cleaned_data["database_alias"] or "default"
        if alias != NEW_DATABASE and alias not in tenant_database_aliases():
            raise forms.ValidationError("Select a tenant database target")
        if alias == NEW_DATABASE:
            return alias
        if (
            alias != "default"
            and get_tenant_model()._base_manager.using("default").filter(database_alias=alias).exists()
        ):
            raise forms.ValidationError("This dedicated database is already reserved.")
        return alias

    def clean(self):
        cleaned = super().clean()
        alias = cleaned.get("database_alias", "default")
        if alias == NEW_DATABASE:
            cleaned["access_policy"] = "restricted"
            profiles = database_profiles()
            chosen = cleaned.get("database_profile") or self.fields["database_profile"].initial
            if chosen not in profiles:
                self.add_error("database_profile", "Choose an available database server.")
            else:
                cleaned["database_profile"] = chosen
        else:
            if cleaned.get("database_name"):
                self.add_error("database_name", "A new database name requires New database + tenant schema.")
            if alias != "default":
                cleaned["access_policy"] = "restricted"
        return cleaned

    def clean_database_name(self):
        value = self.cleaned_data["database_name"]
        if value:
            try:
                validate_new_database_name(value)
            except ValueError as error:
                raise forms.ValidationError(str(error)) from error
        return value

    access_policy = forms.ChoiceField(
        choices=[("shared", "Shared runtime role"), ("restricted", "Dedicated PostgreSQL role")],
        initial="shared",
        label="Shared-database access policy",
        help_text="Dedicated databases always use a dedicated PostgreSQL role.",
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
        label="Provision tenant infrastructure, apply migrations, run tenant setup and activate this tenant.",
    )

    def clean_schema(self):
        schema = self.cleaned_data["schema"]

        try:
            validate_schema(schema)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        if self.cleaned_data.get("database_alias") != NEW_DATABASE and (
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
        options = {
            "name": cleaned_data["name"],
            "schema": cleaned_data["schema"],
            "hostname": cleaned_data["hostname"],
            "database": cleaned_data["database_alias"],
            "access_policy": cleaned_data["access_policy"],
            "tenant_data": {
                key: value for key, value in cleaned_data.items() if key not in TenantProvisionForm.base_fields
            },
        }

        if cleaned_data["database_alias"] == NEW_DATABASE:
            options.update(
                database="default",
                new_database=True,
                database_name=cleaned_data.get("database_name") or None,
                database_profile=cleaned_data.get("database_profile") or None,
                access_policy="restricted",
            )
        return options
