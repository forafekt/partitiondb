from django.db import models

from partitiondb.adapters.django.models import TenantAdminAuditMixin


class AdminAudit(TenantAdminAuditMixin):
    pass

