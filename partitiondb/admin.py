class TenantAdminMixin:
    """
    Mixin for Tenant model:
    It disables save and delete buttons when not in current or public tenant (preventing Exceptions).
    """
    change_form_template = 'admin/partitiondb/tenant/change_form.html'


