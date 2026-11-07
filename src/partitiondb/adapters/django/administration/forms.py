from django import forms
from django.db import models


def style_provisioning_form(model_admin, request, form):
    """Use the configured ModelAdmin widgets without depending on a UI package."""
    for name, field in form.fields.items():
        if field.widget.is_hidden:
            continue
        if isinstance(field, forms.ChoiceField):
            model_field = models.CharField(choices=list(field.choices), blank=not field.required)
        elif isinstance(field, forms.BooleanField):
            model_field = models.BooleanField(blank=not field.required)
        elif isinstance(field, forms.CharField):
            model_field = models.CharField(max_length=field.max_length or 255, blank=not field.required)
        else:
            continue
        model_field.set_attributes_from_name(name)
        styled = model_admin.formfield_for_dbfield(model_field, request)
        if styled is not None:
            field.widget = styled.widget
            if isinstance(field, forms.ChoiceField):
                field.widget.choices = field.choices
    return form
