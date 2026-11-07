"""Django settings factory for the framework-independent development provider."""

from partitiondb.credentials.development import DevelopmentCredentials


def development_credentials():
    from django.core.exceptions import ImproperlyConfigured

    from partitiondb.adapters.django.configuration import get_config

    directory = get_config()["DEVELOPMENT_CREDENTIALS_DIR"]
    if directory is None:
        raise ImproperlyConfigured("Set PARTITIONDB.DEVELOPMENT_CREDENTIALS_DIR")
    return DevelopmentCredentials(directory)


def openbao_credentials():
    """Zero-argument factory for the existing CREDENTIAL_PROVIDER extension point."""
    from collections.abc import Mapping

    from django.core.exceptions import ImproperlyConfigured

    from partitiondb.adapters.django.configuration import get_config
    from partitiondb.credentials.openbao import OpenBaoKV2, TokenFile

    options = get_config()["CREDENTIAL_PROVIDER_OPTIONS"]
    required = {"address", "mount", "prefix", "mount_accessor", "token_file"}
    allowed = required | {"ca_file", "timeout"}
    if not isinstance(options, Mapping) or not required <= options.keys() or set(options) - allowed:
        raise ImproperlyConfigured("Configure the OpenBao address, mount, prefix, mount_accessor and token_file")
    values = dict(options)
    token = TokenFile(values.pop("token_file"))
    try:
        return OpenBaoKV2(token=token, **values)
    except Exception:
        raise ImproperlyConfigured("OpenBao credential provider configuration is invalid") from None
