"""Resolve provider secret material from external stores (never persisted in app DB)."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from rai_gateway.provider_gateway.credentials.domain import SecretBackend


class SecretResolutionError(Exception):
    pass


class UnsupportedSecretBackend(SecretResolutionError):
    def __init__(self, backend: str) -> None:
        super().__init__(f"Secret backend {backend!r} is not configured")
        self.backend = backend


@dataclass(frozen=True)
class ResolvedProviderSecret:
    """Opaque secret payload used only when building upstream headers."""

    payload: dict[str, Any]
    backend: str
    reference: str


class ProviderSecretResolver(ABC):
    @abstractmethod
    def resolve(self, backend: str, secret_reference: str) -> ResolvedProviderSecret:
        raise NotImplementedError


class EnvironmentSecretResolver(ProviderSecretResolver):
    """Development / test backend: secret_reference is an environment variable name."""

    def resolve(self, backend: str, secret_reference: str) -> ResolvedProviderSecret:
        if backend != SecretBackend.ENVIRONMENT.value:
            raise UnsupportedSecretBackend(backend)
        value = os.environ.get(secret_reference)
        if not value:
            raise SecretResolutionError(
                f"Environment secret {secret_reference!r} is not set"
            )
        return ResolvedProviderSecret(
            payload={"value": value},
            backend=backend,
            reference=secret_reference,
        )


class ChainedProviderSecretResolver(ProviderSecretResolver):
    """Dispatch to a concrete backend implementation."""

    def __init__(
        self, resolvers: dict[str, ProviderSecretResolver] | None = None
    ) -> None:
        env = EnvironmentSecretResolver()
        self._by_backend: dict[str, ProviderSecretResolver] = {
            SecretBackend.ENVIRONMENT.value: env,
        }
        if resolvers:
            self._by_backend.update(resolvers)

    def resolve(self, backend: str, secret_reference: str) -> ResolvedProviderSecret:
        impl = self._by_backend.get(backend)
        if not impl:
            raise UnsupportedSecretBackend(backend)
        return impl.resolve(backend, secret_reference)


def default_secret_resolver() -> ProviderSecretResolver:
    return ChainedProviderSecretResolver()
