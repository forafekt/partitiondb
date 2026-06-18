

class FakeTenant:
    """
    We can't import any db model in a backend (apparently?), so this class is used
    for wrapping schema names in a tenant-like structure.
    """
    def __init__(self, schema_name, tenant_type=None, db_name=None):
        from partitiondb.registry import registry
        self.db_name = db_name
        self.schema_name = schema_name
        self.tenant_type = tenant_type
        self.db_alias = registry.get_db_alias(self.db_name)

    def get_tenant_type(self):
        return self.tenant_type
