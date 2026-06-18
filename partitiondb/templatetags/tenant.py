from django.template.library import Library
from django.template.defaulttags import URLNode
from django.template.defaulttags import url as default_url

from partitiondb.registry import registry
from partitiondb.utils import clean_tenant_url, get_app_label

register = Library()


class SchemaURLNode(URLNode):
    def __init__(self, url_node):
        super().__init__(url_node.view_name, url_node.args, url_node.kwargs, url_node.asvar)

    def render(self, context):
        tenant_url = super().render(context)
        return clean_tenant_url(tenant_url)


@register.tag
def url(parser, token):
    return SchemaURLNode(default_url(parser, token))


@register.simple_tag
def public_schema():
    return registry.get_public_schema_name()


@register.simple_tag(takes_context=True)
def is_tenant_app(context, app):
    if registry.get_has_multi_type_tenants():
        if hasattr(context.request, 'tenant') and context.request.tenant is not None:
            _apps = registry.get_tenant_types()[context.request.tenant.get_tenant_type()]['APPS']
        else:
            return True
    else:
        _apps = registry.get_tenant_apps()

    return app["app_label"] in [get_app_label(_app) for _app in _apps]


@register.simple_tag()
def is_shared_app(app):
    if registry.get_has_multi_type_tenants():
        _apps = registry.get_tenant_types()[registry.get_public_schema_name()]['APPS']
    else:
        _apps = registry.get_shared_apps()

    return app["app_label"] in [get_app_label(_app) for _app in _apps]


@register.simple_tag()
def colour_admin_apps():
    if registry.has(registry.enum.TENANT_COLOR_ADMIN_APPS):
        return registry.get_tenant_color_admin_apps()
    return True


@register.simple_tag(takes_context=True)
def is_public_schema(context, app):
    return not hasattr(context.request, 'tenant') or context.request.tenant.schema_name == registry.get_public_schema_name()


@register.simple_tag(takes_context=True)
def is_db_tenant(context, app):
    return context.request.tenant.db_name != registry.get_default_db_name()

@register.simple_tag(takes_context=True)
def current_tenant(context, app):
    if hasattr(context.request, 'tenant'):
        return context.request.tenant
    class FakeTenant:
        def __init__(self, name):
            self.name = name
    return FakeTenant(registry.get_public_schema_name())