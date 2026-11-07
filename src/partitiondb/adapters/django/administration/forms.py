from django import forms
from django.db import models


def style_provisioning_form(model_admin, request, form):
    """Use the configured ModelAdmin widgets without depending on a UI package."""
    for name, field in form.fields.items():
        if type(field.widget) not in {forms.TextInput, forms.Textarea, forms.Select, forms.CheckboxInput}:
            continue
        if isinstance(field, forms.MultipleChoiceField):
            continue
        if isinstance(field, forms.ChoiceField):
            model_field = models.CharField(choices=list(field.choices), blank=not field.required)
        elif isinstance(field, forms.BooleanField):
            model_field = models.BooleanField(blank=not field.required)
        elif isinstance(field, forms.CharField):
            model_type = models.TextField if isinstance(field.widget, forms.Textarea) else models.CharField
            model_field = model_type(max_length=field.max_length or 255, blank=not field.required)
        else:
            continue
        model_field.set_attributes_from_name(name)
        styled = model_admin.formfield_for_dbfield(model_field, request)
        if styled is not None:
            original_attrs = field.widget.attrs
            field.widget = styled.widget
            for attribute, value in original_attrs.items():
                if attribute == "class":
                    field.widget.attrs[attribute] = " ".join(filter(None, (field.widget.attrs.get(attribute), value)))
                else:
                    field.widget.attrs[attribute] = value
            if isinstance(field, forms.ChoiceField):
                field.widget.choices = field.choices
    return form


class TenantConfirmationForm(forms.Form):
    confirm_tenant_id = forms.CharField(
        label="Confirm tenant ID", max_length=255, widget=forms.TextInput(attrs={"autocomplete": "off"})
    )


class RoleRecoveryForm(TenantConfirmationForm):
    decision = forms.ChoiceField(choices=())


class RoleCleanupForm(TenantConfirmationForm):
    confirmation_token = forms.CharField(widget=forms.HiddenInput)
    confirm_role = forms.CharField(
        label="Confirm PostgreSQL role", max_length=63, widget=forms.TextInput(attrs={"autocomplete": "off"})
    )
    confirm_ownership = forms.BooleanField(
        label="I verified ownership of this failed operation and authorize cleanup",
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )


class DatabaseRecoveryForm(TenantConfirmationForm):
    confirmation_token = forms.CharField(widget=forms.HiddenInput)
    decision = forms.ChoiceField(choices=())
    confirm_database_name = forms.CharField(
        label="Confirm database name", max_length=63, widget=forms.TextInput(attrs={"autocomplete": "off"})
    )
    confirm_ownership = forms.BooleanField(
        label="I verified ownership and authorize the displayed recovery action",
        widget=forms.CheckboxInput(attrs={"value": "yes"}),
    )


class LifecycleConfirmationForm(TenantConfirmationForm):
    confirm_schema = forms.CharField(label="Confirm schema", max_length=63)
    confirm_database = forms.CharField(label="Confirm database alias", max_length=64)
    confirm_type = forms.CharField(label="Confirm tenant type (leave empty for untyped)", max_length=63, required=False)


def confirmation_form(model_admin, request, form_class, *, token=None, decisions=None):
    """Use the host ModelAdmin's widgets; services still validate exact identity under lock."""
    form = form_class(initial={"confirmation_token": token} if token is not None else None)
    if decisions is not None:
        form.fields["decision"].choices = decisions
    return style_provisioning_form(model_admin, request, form)
