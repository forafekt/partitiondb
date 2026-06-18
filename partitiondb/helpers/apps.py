# partitiondb/helpers/apps.py

def get_model(app_label=None, model_name=None, require_ready: bool = True):
    from django.apps import apps
    return apps.get_model(app_label, model_name, require_ready)
