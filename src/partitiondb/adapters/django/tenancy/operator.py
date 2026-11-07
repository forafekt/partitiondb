"""Explicit, independently authorized operator lifecycle actions."""

from django.core.exceptions import PermissionDenied

from partitiondb.adapters.django.control_plane import require_control_plane
from partitiondb.provisioning import Stage


def run_tenant_action(request, workflow, confirm, action):
    require_control_plane()
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_superuser and user.pk):
        raise PermissionDenied
    if action not in {"enable", "disable", "resume"}:
        raise ValueError("Unsupported tenant action")
    if action == "resume":
        with workflow.access_context(confirm.id) as context:
            if context.tenant != confirm:
                raise ValueError("Registry identity mismatch")
            with context.provisioner.store.lock(confirm.id):
                record = context.provisioner.store.get(confirm.id)
                if record is None or record.tenant != confirm:
                    raise ValueError("Checkpoint identity mismatch")
                if record.stage in {Stage.CREATING, Stage.ACCESS_PROVISIONING, Stage.DATABASE_PROVISIONING}:
                    raise ValueError("Inspect ambiguous provisioning before recovery")
                # run() obtains its own lifecycle lock and rechecks state. It
                # refuses ambiguous external effects even if state changes here.
            completed = context.provisioner.run(confirm.id)
            if completed.tenant != confirm:
                raise ValueError("Provisioner returned another tenant")
    with workflow.registry_lifecycle(confirm.id) as controller:
        return controller.set_active(confirm, active=action != "disable")
