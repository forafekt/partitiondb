from django.conf import settings


class DynamicDatabaseMixin:
    """
    A Django admin mixin that allows dynamically changing the database connection
    for admin views based on parameters or conditions.
    """
    default_db = 'default'  # Default database connection

    def get_db_for_request(self, request):
        """
        Determine which database to use based on the request.
        Override this method to implement custom logic.

        Example implementations:
        - Based on URL parameters: return request.GET.get('db', self.default_db)
        - Based on user: return user_to_db_mapping.get(request.user.username, self.default_db)
        - Based on session: return request.session.get('selected_db', self.default_db)
        """
        # By default, check for a 'db' parameter in GET
        return request.GET.get('db', self.default_db)

    def get_queryset(self, request):
        """
        Override get_queryset to use the dynamic database connection
        """
        db = self.get_db_for_request(request)

        # Validate that the requested DB exists in settings
        if db not in settings.DATABASES:
            db = self.default_db

        queryset = super().get_queryset(request)
        return queryset.using(db)

    def save_model(self, request, obj, form, change):
        """
        Override save_model to use the dynamic database connection
        """
        db = self.get_db_for_request(request)
        if db not in settings.DATABASES:
            db = self.default_db

        obj.save(using=db)

    def delete_model(self, request, obj):
        """
        Override delete_model to use the dynamic database connection
        """
        db = self.get_db_for_request(request)
        if db not in settings.DATABASES:
            db = self.default_db

        obj.delete(using=db)

    def save_formset(self, request, form, formset, change):
        """
        Override save_formset to use the dynamic database connection
        """
        db = self.get_db_for_request(request)
        if db not in settings.DATABASES:
            db = self.default_db

        instances = formset.save(commit=False)
        for instance in instances:
            instance.save(using=db)
        formset.save_m2m()

        # Handle deletes
        for obj in formset.deleted_objects:
            obj.delete(using=db)

    # Add a database selector to the admin change list page
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}

        # Add available databases to the context
        extra_context['available_databases'] = settings.DATABASES.keys()
        extra_context['current_database'] = self.get_db_for_request(request)

        return super().changelist_view(request, extra_context)