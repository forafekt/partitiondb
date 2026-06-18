# middleware/thread_local.py
import threading

from partitiondb.utils import get_host_from_request

tenant_ctx = threading.local()

def get_current_request_ctx():
    return getattr(tenant_ctx, 'request', None)

def set_current_request_ctx(request):
    tenant_ctx.request = request

def get_current_hostname_ctx():
    request = get_current_request_ctx()
    if request:
        return get_host_from_request(request)[1]
    return None

def set_current_hostname_ctx(hostname):
    tenant_ctx.hostname = hostname

def get_current_host_ctx():
    request = get_current_request_ctx()
    if request:
        return request.get_host()
    return None

def set_current_host_ctx(host):
    tenant_ctx.host = host

def get_current_tenant_ctx():
    return getattr(tenant_ctx, 'tenant', None)

def set_current_tenant_ctx(tenant):
    tenant_ctx.tenant = tenant

def unset_current_tenant_ctx():
    tenant_ctx.tenant = None
    del tenant_ctx.tenant


class TenantContextThreadMiddleware(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request_ctx(request)

        host, host_name = get_host_from_request(request)
        set_current_host_ctx(host)
        set_current_hostname_ctx(host_name)


        if hasattr(request, 'tenant'):
            set_current_tenant_ctx(request.tenant)

        response = self.get_response(request)
        return response
