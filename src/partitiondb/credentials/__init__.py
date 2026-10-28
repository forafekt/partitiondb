"""Secret storage boundary for restricted tenant database roles.

Providers are supplied by the deployment. Never store passwords in tenant metadata.
"""

from typing import Protocol


class CredentialProvider(Protocol):
    def put(self, reference: str, password: str) -> None: ...
    def get(self, reference: str) -> str: ...
    def delete(self, reference: str) -> None: ...
