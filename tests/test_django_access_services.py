"""Contract tests that do not require a configured Django registry database."""
from unittest.mock import Mock
import pytest
from django.core.exceptions import ValidationError
from partitiondb.core import Tenant
from partitiondb.postgres_role_manager import RoleBinding
from partitiondb.adapters.django.access_services import DjangoRoleBindingRecorder
from partitiondb.adapters.django.access_models import TenantAccessMixin


def test_recorder_requires_concrete_access_model():
    with pytest.raises(TypeError, match="concrete"):
        DjangoRoleBindingRecorder(TenantAccessMixin)


def test_binding_identity_checked_before_database_access():
    recorder = object.__new__(DjangoRoleBindingRecorder)
    recorder.model = Mock()
    recorder.using = "default"
    with pytest.raises(ValidationError, match="does not match"):
        recorder(Tenant("1", "org_demo"), RoleBinding("pdb_other", "secret/1"))
    recorder.model.assert_not_called()
