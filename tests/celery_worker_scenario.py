"""Real solo worker/memory transport scenario, isolated from global test state."""

from contextlib import contextmanager

from celery import Celery

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.celery import tenant_job, tenant_signature


def main():
    from celery.contrib.testing.worker import start_worker
    from celery.signals import task_postrun

    app = Celery("partitiondb-worker", broker="memory://", backend="cache+memory://")
    app.conf.update(accept_content=["json"], task_serializer="json")
    records = {"one": Tenant("one", "alpha"), "two": Tenant("two", "beta")}
    events = []

    class Repository:
        def get(self, tenant_id):
            assert current_tenant(required=False) is None
            return records.get(tenant_id)

    @contextmanager
    def repository():
        assert current_tenant(required=False) is None
        events.append("open")
        try:
            yield Repository()
        finally:
            assert current_tenant(required=False) is None
            events.append("close")

    app.conf.task_always_eager = False
    completed = []

    @app.task(shared=False)
    @tenant_job(repository)
    def worker_task(fail=False):
        if fail:
            raise ValueError("expected worker failure")
        return current_tenant().schema

    def after(sender, **kwargs):
        if sender is worker_task._get_current_object():
            completed.append(current_tenant(required=False))

    task_postrun.connect(after, weak=False)
    try:
        with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=15):
            assert (
                tenant_signature(worker_task, tenant_id="one").apply_async().get(timeout=10)
                == "alpha"
            )
            failed = tenant_signature(
                worker_task, kwargs={"fail": True}, tenant_id="one"
            ).apply_async()
            try:
                failed.get(timeout=10)
            except ValueError:
                pass
            else:
                raise AssertionError("Expected worker failure was lost")
            assert (
                tenant_signature(worker_task, tenant_id="two").apply_async().get(timeout=10)
                == "beta"
            )
    finally:
        task_postrun.disconnect(after)
    assert completed == [None, None, None]
    assert events.count("open") == events.count("close") == 3
    assert current_tenant(required=False) is None
    app.close()


if __name__ == "__main__":
    main()
