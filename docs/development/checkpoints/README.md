# Historical development checkpoints

These files record implementation decisions, experimental APIs, caveats and recovery constraints during PartitionDB development. They are **not** a substitute for current public documentation, test results, or a release approval.

In particular, the restricted-access integration is opt-in, and checkpoint notes do not establish automatic crash-safe replay or credential recovery. See the current `docs/` guides and `scripts/release_check.py` for the supported behavior and release gates.

- [Access Control Integration](ACCESS_CONTROL_INTEGRATION.md)
- [Access End To End Status](ACCESS_END_TO_END_STATUS.md)
- [Access Evidence Checkpoint](ACCESS_EVIDENCE_CHECKPOINT.md)
- [Access Implementation Status](ACCESS_IMPLEMENTATION_STATUS.md)
- [Access Operations Checkpoint](ACCESS_OPERATIONS_CHECKPOINT.md)
- [Access Provisioning Checkpoint](ACCESS_PROVISIONING_CHECKPOINT.md)
- [Access Recovery Checkpoint](ACCESS_RECOVERY_CHECKPOINT.md)
- [Django Access Workflow Checkpoint](DJANGO_ACCESS_WORKFLOW_CHECKPOINT.md)
- [Django Durable Evidence Checkpoint](DJANGO_DURABLE_EVIDENCE_CHECKPOINT.md)
- [Django Role Binding Checkpoint](DJANGO_ROLE_BINDING_CHECKPOINT.md)
- [Role Inspection Checkpoint](ROLE_INSPECTION_CHECKPOINT.md)
- [Role Manager Status](ROLE_MANAGER_STATUS.md)
