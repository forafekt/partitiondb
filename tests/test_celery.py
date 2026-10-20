from contextlib import contextmanager

import pytest

celery = pytest.importorskip("celery")

from partitiondb import MissingTenant, Tenant, current_tenant, tenant_scope  # noqa: E402
from partitiondb.adapters.celery import TENANT_HEADER, tenant_job, tenant_signature  # noqa: E402


@pytest.fixture
def setup():
    app = celery.Celery("partitiondb-tests", broker="memory://", backend="cache+memory://")
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        accept_content=["json"],
        task_serializer="json",
    )
    records = {"one": Tenant("one", "alpha"), "two": Tenant("two", "beta")}
    events = []

    class Repository:
        def get(self, tenant_id):
            assert current_tenant(required=False) is None
            events.append(("lookup", tenant_id))
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

    yield app, records, events, repository
    app.close()


def test_task_resolves_moved_route_and_restores_publishing_context(setup):
    app, records, events, repository = setup

    @app.task(shared=False)
    @tenant_job(repository)
    def identify():
        return current_tenant().schema

    headers = {"custom": "retained"}
    with tenant_scope(records["one"]):
        signature = tenant_signature(identify, headers=headers)
    records["one"] = Tenant("one", "moved", "dedicated")
    with tenant_scope(records["two"]):
        assert signature.apply_async().get() == "moved"
        assert current_tenant() == records["two"]
    assert headers == {"custom": "retained"}
    assert signature.options["headers"] == {"custom": "retained", TENANT_HEADER: "one"}
    assert events == ["open", ("lookup", "one"), "close"]


@pytest.mark.parametrize("identity", [None, "", 1, "disabled"])
def test_missing_invalid_or_inactive_tenant_never_runs_body(setup, identity):
    app, records, events, repository = setup
    calls = []

    @app.task(shared=False)
    @tenant_job(repository)
    def task():
        calls.append(True)

    with pytest.raises(MissingTenant):
        task.apply(headers={TENANT_HEADER: identity}, throw=True)
    assert calls == []
    assert current_tenant(required=False) is None
    if identity == "disabled":
        assert events[-1] == "close"


def test_failure_and_retry_close_repository_and_reresolve_route(setup):
    app, records, events, repository = setup

    @app.task(bind=True, max_retries=1, shared=False)
    @tenant_job(repository)
    def retrying(self):
        if self.request.retries == 0:
            records["one"] = Tenant("one", "moved")
            raise self.retry(countdown=0)
        return current_tenant().schema

    # Eager retry execution exercises Celery request-header preservation.
    app.conf.task_eager_propagates = False
    assert tenant_signature(retrying, tenant_id="one").apply_async().get() == "moved"
    assert events.count("open") == events.count("close") == 2

    @app.task(shared=False)
    @tenant_job(repository)
    def failing():
        raise RuntimeError("body failed")

    with pytest.raises(RuntimeError):
        tenant_signature(failing, tenant_id="one").apply_async().get()
    assert events.count("open") == events.count("close") == 3
    assert current_tenant(required=False) is None


def test_publishing_and_direct_call_fail_closed(setup):
    app, records, events, repository = setup

    @app.task(shared=False)
    @tenant_job(repository)
    def task():
        return current_tenant().id

    with pytest.raises(MissingTenant):
        tenant_signature(task)
    with pytest.raises(MissingTenant):
        task.run()
    with tenant_scope(records["one"]), pytest.raises(ValueError):
        tenant_signature(task, tenant_id="two")
    with pytest.raises(ValueError):
        tenant_signature(task, tenant_id="one", headers={TENANT_HEADER: "two"})
    assert events == []


def test_async_and_generator_task_functions_are_rejected(setup):
    repository = setup[-1]

    async def asynchronous():
        return 1

    def generator():
        yield 1

    for function in (asynchronous, generator):
        with pytest.raises(TypeError):
            tenant_job(repository)(function)


def test_real_memory_transport_worker_isolates_messages_and_failure_cleanup():
    import subprocess
    import sys

    # Celery's embedded worker configures process-global logging and current apps.
    # Keep that setup out of the pytest process and unrelated Django/log tests.
    subprocess.run([sys.executable, "-m", "tests.celery_worker_scenario"], check=True, timeout=45)


def test_repository_identity_mismatch_is_rejected_and_factory_closes(setup):
    app, records, events, repository = setup
    records["one"] = Tenant("different", "alpha")

    @app.task(shared=False)
    @tenant_job(repository)
    def task():
        pytest.fail("Mismatched identity must not execute")

    with pytest.raises(ValueError):
        tenant_signature(task, tenant_id="one").apply_async()
    assert events[-1] == "close"
