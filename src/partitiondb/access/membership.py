"""Framework-independent membership CRUD policy. Unknown roles grant nothing."""

from enum import StrEnum


class MembershipRole(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


def role_actions(role: str | None) -> tuple[str, ...]:
    return {
        MembershipRole.ADMIN: ("view", "add", "change", "delete"),
        MembershipRole.EDITOR: ("view", "add", "change"),
        MembershipRole.VIEWER: ("view",),
    }.get(role, ())
