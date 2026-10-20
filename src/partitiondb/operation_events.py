"""Best-effort synchronous notifications for explicit database operations."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Literal

from .core import Tenant, validate_schema


@dataclass(frozen=True, slots=True)
class OperationEvent:
    operation: Literal["schema_create", "schema_drop", "schema_rename", "migrate"]
    phase: Literal["started", "succeeded", "failed"]
    schema: str
    target_schema: str | None = None
    tenant: Tenant | None = None
    error_type: str | None = None
    mode: Literal["apply", "plan", "check"] | None = None

    def __post_init__(self) -> None:
        validate_schema(self.schema)
        if self.target_schema is not None:
            validate_schema(self.target_schema)
        if self.tenant is not None and self.tenant.schema != self.schema:
            raise ValueError("Event tenant and schema must match")


Observer = Callable[[OperationEvent], None]


def notify(observer: Observer | None, event: OperationEvent) -> None:
    if observer is not None:
        try:
            observer(event)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Operation observer failed: %s", type(error).__name__
            )


@contextmanager
def observe_operation(event: OperationEvent, observer: Observer | None = None) -> Iterator[None]:
    """Wrap the complete operation, including its commit and resource cleanup.

    Started means an attempt, not lock acquisition. Success is published only
    after the body exits normally. A failed operation may have partial committed
    effects (for example, earlier Django migrations); it does not mean rollback.
    """
    if event.phase != "started":
        raise ValueError("Operation boundary requires a started event")
    notify(observer, event)
    try:
        yield
    except BaseException as error:
        notify(observer, replace(event, phase="failed", error_type=type(error).__name__))
        raise
    else:
        notify(observer, replace(event, phase="succeeded"))
