"""Isolated Django acceptance: optional app models, migration and constraints."""

import subprocess
import sys
import textwrap


def test_access_control_app_migrates_without_activelane_models(tmp_path):
    script = textwrap.dedent("""
        from django.conf import settings
        settings.configure(
            SECRET_KEY='test',
            INSTALLED_APPS=['partitiondb.adapters.django.access_control'],
            DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
            DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        )
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection, IntegrityError, transaction
        from partitiondb.adapters.django.access_control.models import (
            AccessOperation, RoleOwnershipReceipt, AccessRecoveryAudit,
        )
        call_command('check', verbosity=0)
        call_command('makemigrations', 'partitiondb_access_control', check=True, dry_run=True, verbosity=0)
        call_command('migrate', verbosity=0)
        AccessOperation.objects.create(
            operation_id='abc', tenant_id='t', database_alias='default',
            schema_name='org_t', credential_ref='ref', role_name='pdb_org_t',
            state='pending',
        )
        try:
            with transaction.atomic():
                AccessOperation.objects.create(
                    operation_id='def', tenant_id='t', database_alias='default',
                    schema_name='org_t', credential_ref='ref', role_name='pdb_org_t',
                    state='pending',
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError('Missing unique tenant target constraint')
        assert AccessOperation.objects.count() == 1
        assert 'pdb_role_ownership_receipt' in connection.introspection.table_names()
        assert 'pdb_access_recovery_audit' in connection.introspection.table_names()
        from partitiondb.core import Tenant
        from partitiondb.access_operations import AccessOperation as Intent, OperationState
        from partitiondb.access_evidence import RoleOwnershipReceipt as Receipt
        from partitiondb.access_recovery import RecoveryDecision
        from partitiondb.postgres_role_inspection import RoleInspection, RoleState
        from partitiondb.adapters.django.access_operation_store import DjangoAccessOperationJournal
        from partitiondb.adapters.django.access_evidence_store import (
            DjangoOwnershipReceiptStore, DjangoRecoveryAuditSink,
        )
        tenant = Tenant('new', 'org_new')
        journal = DjangoAccessOperationJournal(AccessOperation)
        journal.begin(Intent('intent-new', tenant, 'secret/new', 'pdb_org_new'))
        assert journal.get(tenant).state == OperationState.PENDING
        # Nested transactions must fail before writing independently durable evidence.
        for action in [
            lambda: journal.advance('intent-new', OperationState.PENDING, OperationState.ROLE_CREATED),
        ]:
            try:
                with transaction.atomic():
                    action()
            except RuntimeError:
                pass
            else:
                raise AssertionError('Nested control-plane transaction accepted')
        assert journal.get(tenant).state == OperationState.PENDING

        journal.advance('intent-new', OperationState.PENDING, OperationState.ROLE_CREATED)
        assert journal.get(tenant).state == OperationState.ROLE_CREATED
        try:
            journal.advance('intent-new', OperationState.PENDING, OperationState.ROLE_CREATED)
        except ValueError:
            pass
        else:
            raise AssertionError('CAS must reject stale transition')
        receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt)
        try:
            with transaction.atomic():
                receipts.record(Receipt('new', 'default', 'org_new', 'pdb_org_new', 456, 'secret/new', 'intent-new'))
        except RuntimeError:
            pass
        else:
            raise AssertionError('Nested receipt transaction accepted')
        assert receipts.get(tenant) is None
        receipts.record(Receipt('new', 'default', 'org_new', 'pdb_org_new', 456, 'secret/new', 'intent-new'))
        assert receipts.get(tenant).role_oid == 456
        from partitiondb.adapters.django.access_control.services import JournalBoundRecoveryEvidence
        class Authentication:
            def verify(self, tenant, role, ref):
                return True
            def absent(self, ref):
                return False
        evidence = JournalBoundRecoveryEvidence(journal, receipts, Authentication())
        verified = evidence(tenant, RoleInspection('pdb_org_new', RoleState.CONFIGURED, role_oid=456))
        assert verified.ownership_verified and verified.credential_verified
        replacement = evidence(tenant, RoleInspection('pdb_org_new', RoleState.CONFIGURED, role_oid=457))
        assert not replacement.ownership_verified
        audit = DjangoRecoveryAuditSink(AccessRecoveryAudit)
        inspection = RoleInspection('pdb_org_new', RoleState.CONFIGURED, role_oid=456)
        try:
            with transaction.atomic():
                audit.record(operation_id='nested', tenant=tenant, actor_id='42',
                             decision=RecoveryDecision.READY, inspection=inspection)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Nested audit transaction accepted')
        assert not AccessRecoveryAudit.objects.exists()
        assert audit.record(operation_id='intent-new:ready', tenant=tenant, actor_id='42',
                            decision=RecoveryDecision.READY, inspection=inspection)
        assert not audit.record(operation_id='intent-new:ready', tenant=tenant, actor_id='42',
                                decision=RecoveryDecision.READY, inspection=inspection)
        try:
            audit.record(operation_id='intent-new:ready', tenant=tenant, actor_id='99',
                         decision=RecoveryDecision.READY, inspection=inspection)
        except ValueError:
            pass
        else:
            raise AssertionError('Audit identity mismatch must fail')
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
