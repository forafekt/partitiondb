import logging

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.db import connection
from django.db.models import Q
from django.http import Http404
from django.urls import set_urlconf
from django.utils.deprecation import MiddlewareMixin

from partitiondb.middleware.context_thread import TenantContextThreadMiddleware, set_current_tenant_ctx, \
    unset_current_tenant_ctx
from partitiondb.utils import  get_host_from_request
from partitiondb.registry import registry

logger = logging.getLogger(__name__)


class TenantMainMiddleware(TenantContextThreadMiddleware, MiddlewareMixin):
    TENANT_NOT_FOUND_EXCEPTION = Http404
    """
    This middleware should be placed at the very top of the middleware stack.
    Selects the proper database schema using the request host. Can fail in
    various ways which is better than corrupting or revealing data.
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

    def __call__(self, request):
        logger.info("Processing request for host: %s", request.get_host())

        connection.set_schema_to_public()
        logger.info("Set connection to public schema")

        self.validate_request_host(request)
        logger.info("Validated request host")

        tenant = self.get_tenant(request)
        logger.info("Got tenant: %s", tenant)

        set_current_tenant_ctx(tenant)
        logger.info("Set current tenant context")


        print("tenant", tenant)
        # Register DB alias to settings.DATABASES
        registry.register_db_alias(tenant.db_alias, tenant.db_name)
        logger.info("Registered %s to settings.DATABASES", tenant.db_alias)

        tenant.domain_url = get_host_from_request(request)[1]
        logger.info("Set tenant domain_url: %s", tenant.domain_url)

        request.tenant = tenant
        logger.info("Set request tenant: %s", request.tenant)

        connection.set_tenant(request.tenant)
        logger.info("Set connection tenant: %s", request.tenant)

        # order is important here
        # setup url routing must be called after the tenant is set
        # and before the response is returned
        response = self.get_response(request)

        self.setup_url_routing(request)
        logger.info("Setup url routing")

        # Cleanup
        unset_current_tenant_ctx()
        logger.warning("Unset current tenant context")
        registry.unregister_all_db_aliases()
        logger.warning("Unregistered all DB aliases")

        return response

    def validate_request_host(self, request):
        try:
            host, hostname = get_host_from_request(request)
            logger.debug("Parsed hostname: %s", hostname)

        except DisallowedHost:
            logger.error("Disallowed host: %s", request.get_host())
            from django.http import HttpResponseNotFound
            return HttpResponseNotFound()



    def get_tenant(self, request):
        domain_model = registry.get_tenant_domain_model()
        host, hostname = get_host_from_request(request)
        try:
            domain = domain_model.objects.select_related().get(Q(domain=host) | Q(tenant__slug=hostname))
            return domain.tenant
        except domain_model.DoesNotExist:
            logger.error("No tenant found for hostname: %s", hostname)
            return None

    def no_tenant_found(self, request, hostname):
        """ What should happen if no tenant is found.
        This makes it easier if you want to override the default behavior """
        if hasattr(settings, 'SHOW_PUBLIC_IF_NO_TENANT_FOUND') and settings.SHOW_PUBLIC_IF_NO_TENANT_FOUND:
            self.setup_url_routing(request=request, force_public=True)
            logger.debug("No tenant found for hostname: %s", hostname)
        else:
            logger.error("No tenant found for hostname: %s", hostname)
            raise self.TENANT_NOT_FOUND_EXCEPTION('No tenant for hostname "%s"' % hostname)

    @staticmethod
    def setup_url_routing(request, force_public=False):
        """
        Sets the correct url conf based on the tenant
        :param request:
        :param force_public
        """
        public_schema_name = registry.get_public_schema_name()
        if registry.get_has_multi_type_tenants():
            tenant_types = registry.get_tenant_types()
            if (not hasattr(request, 'tenant') or
                    ((force_public or request.tenant.schema_name == registry.get_public_schema_name()) and
                     'URLCONF' in tenant_types[public_schema_name])):
                request.urlconf = registry.get_public_schema_urlconf()
            else:
                tenant_type = request.tenant.get_tenant_type()
                request.urlconf = tenant_types[tenant_type]['URLCONF']
            set_urlconf(request.urlconf)

        else:
            # Do we have a public-specific urlconf?
            if (hasattr(settings, 'PUBLIC_SCHEMA_URLCONF') and
                    (force_public or request.tenant.schema_name == registry.get_public_schema_name())):
                request.urlconf = settings.PUBLIC_SCHEMA_URLCONF
