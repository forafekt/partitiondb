from partitiondb.access_evidence import PostgresRecoveryEvidenceProvider, RoleOwnershipReceipt
from partitiondb.core import Tenant
from partitiondb.postgres_role_inspection import RoleInspection, RoleState

TENANT = Tenant("one", "org_one")
RECEIPT = RoleOwnershipReceipt("one", "default", "org_one", "pdb_org_one", 123, "secret/one", "op-1")


class Receipts:
    def __init__(self, receipt):
        self.receipt = receipt

    def get(self, tenant):
        return self.receipt


class Auth:
    def __init__(self, valid=True, absent=False):
        self.valid, self.missing = valid, absent

    def verify(self, tenant, role, ref):
        return self.valid

    def absent(self, ref):
        return self.missing


def check(receipt=RECEIPT, oid=123, state=RoleState.CONFIGURED, auth=None):
    provider = PostgresRecoveryEvidenceProvider(Receipts(receipt), auth or Auth())
    return provider(TENANT, RoleInspection("pdb_org_one", state, role_oid=oid))


def test_valid_receipt_and_auth():
    result = check()
    assert result.ownership_verified and result.credential_verified


def test_missing_receipt_fails_closed():
    assert not check(receipt=None).ownership_verified


def test_recreated_role_oid_cannot_be_adopted():
    assert not check(oid=999).ownership_verified


def test_no_oid_fails_closed():
    assert not check(oid=None).ownership_verified


def test_invalid_credentials_do_not_verify():
    result = check(auth=Auth(valid=False))
    assert result.ownership_verified and not result.credential_verified


def test_absence_requires_secret_absence():
    assert not check(state=RoleState.ABSENT).absence_verified
    assert check(state=RoleState.ABSENT, auth=Auth(absent=True)).absence_verified


def test_conflicting_receipt_rejected():
    receipt = RoleOwnershipReceipt("other", "default", "org_one", "pdb_org_one", 123, "secret/one", "op-1")
    assert not check(receipt=receipt).ownership_verified
