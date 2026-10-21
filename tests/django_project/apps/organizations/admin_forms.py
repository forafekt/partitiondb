from django import forms

from partitiondb.core import validate_schema
from partitiondb.domains import normalize_hostname

from .models import Domain, Organization


class ProvisionOrganizationForm(forms.Form):
    name = forms.CharField(
        max_length=200,
        label="Organization name",
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
        label=("Create the schema, apply migrations, seed the default workspace and activate this organization."),
    )

    def clean_schema(self):
        schema = self.cleaned_data["schema"]

        try:
            validate_schema(schema)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        if Organization.objects.filter(
            database_alias="default",
            schema_name=schema,
        ).exists():
            raise forms.ValidationError(
                "This schema is already registered. Inspect the existing "
                "organization instead of submitting another creation request."
            )

        return schema

    def clean_hostname(self):
        try:
            hostname = normalize_hostname(self.cleaned_data["hostname"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        if Domain.objects.filter(hostname__iexact=hostname).exists():
            raise forms.ValidationError("This hostname is already registered.")

        return hostname
